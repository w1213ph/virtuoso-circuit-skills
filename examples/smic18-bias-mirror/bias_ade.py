"""Create, reopen and audit the six saved native Maestro tests for the selected design."""

import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

from audit_native import spice_number
from bias_sim import STATES, BRANCH_OP, NODES, hold_ns, performance
from bias_views import LIB, TB
from maestro import device_lines, history_tests
from measure import observation_ns
from schematic import q
from spectre_run import execute, read_json, sha256


def variables(state, mode, req):
    return dict(
        VDDVAL="{:g}".format(state[2]),
        VOUT="0.8",
        ISET="{:.12g}u".format(req["reference_uA"]),
        ISTART="{:.12g}u".format(req["reference_uA"] if mode == "dc" else 0),
        THOLD="{:.12g}n".format(hold_ns(req)),
        TPER="{:.12g}n".format(2 * hold_ns(req)),
    )


def save_file(mode):
    return "simulator lang=spectre\nsave " + " ".join(NODES + (BRANCH_OP if mode == "dc" else ())) + "\n"


def launch(stage, config, req):
    folder = stage / "maestro-evidence"
    folder.mkdir()
    dest = stage / "maestro-results"
    dest.mkdir()
    for mode in ("dc", "tran"):
        (folder / (mode + "-save.scs")).write_text(save_file(mode), encoding="utf-8")
    rows = []
    for state in STATES:
        for mode in ("dc", "tran"):
            pairs = " ".join(
                "({} {})".format(q(k), q(v)) for k, v in sorted(variables(state, mode, req).items())
            )
            rows.append(
                "({} {} {} {} ({}))".format(
                    q((state[0] + "_" + mode).upper()), q(mode), q(state[0]), q(str(state[1])), pairs
                )
            )
    settings = dict(
        biasLib=LIB,
        biasTB=TB,
        biasSpectre=config["spectre"],
        biasModel=config["model_file"],
        biasDCSaves=folder / "dc-save.scs",
        biasTranSaves=folder / "tran-save.scs",
        biasReadback=folder / "maestro-readback.txt",
        biasHistory=folder / "maestro-history.txt",
        biasStop="{:.12g}n".format(observation_ns(req["max_settling_ns"])),
    )
    prefix = "\n".join(k + "=" + q(str(v)) for k, v in settings.items()) + "\n"
    prefix += "biasTests='(" + "\n".join(rows) + ")\n"
    prefix += 'envSetVal("asimenv.startup" "projectDir" \'string {})\n'.format(q(str(dest)))
    prefix += 'envSetVal("adexl.historyNamePrefix" "showNameHistoryForm" \'boolean nil)\n'
    prefix += "load({})\n".format(q(str(Path(__file__).resolve().with_name("bias_ade.il"))))
    for phase, proc in (("create", "biasConfigure"), ("run", "biasRun")):
        script = folder / (phase + ".il")
        script.write_text(prefix + "unless(errset({}() t) exit(3)) exit(0)\n".format(proc), encoding="utf-8")
        execute(
            [config["virtuoso"], "-nograph", "-log", folder / (phase + ".log"), "-restore", script],
            stage,
            "maestro-" + phase + ".stdout",
            600,
        )


def options(joined, prefix):
    lines = re.findall(r"(?m)^" + re.escape(prefix) + r"\s+([^\n]+)", joined)
    if len(lines) != 1:
        raise ValueError("Expected one ADE declaration of " + prefix)
    return dict(re.findall(r"(\w+)=([^\s]+)", lines[0]))


def matches_options(actual, required):
    for key, wanted in required.items():
        value = actual.get(key)
        if value == wanted:
            continue
        try:
            if math.isclose(spice_number(value), spice_number(wanted), rel_tol=1e-8):
                continue
        except (ValueError, KeyError):
            pass
        raise ValueError("ADE option {} differs: {} vs {}".format(key, value, wanted))


def check_plotted_outputs(stage, history, names):
    """Check the saved output definitions and the waveform actually evaluated in this history."""
    view = stage / LIB / TB / "maestro"
    expressions = {n: '(- {}("/VFORCE/PLUS"))'.format("IS" if n.endswith("_DC") else "IT") for n in names}
    for state in (view / "active.state", view / "test_states" / (history + ".state")):
        tests = {t.get("Name"): t for t in ET.parse(str(state)).findall("./Test")}
        if set(tests) != set(names):
            raise ValueError("Saved Maestro tests differ from the fixed matrix")
        for name, test in tests.items():
            wanted = {"IOUT_SINK": expressions[name], "VREF_NODE": '"/IREF"'}
            outputs = {}
            for item in test.findall('.//field[@Type="defstruct"]'):
                fields = {f.get("Name"): f.text for f in item.findall("field")}
                label = (fields.get("name") or "").strip('"')
                if label in wanted:
                    outputs[label] = fields.get("signal" if label == "VREF_NODE" else "expression")
            if outputs != wanted:
                raise ValueError("Saved Maestro output expression or node differs: " + name)
    rdb = view / "results" / "maestro" / (history + ".rdb")
    query = """SELECT t.name, r.expression, v.value, v.errorID FROM result r
               JOIN test t USING (testID) JOIN resultValue v USING (resultID)
               WHERE r.name = 'IOUT_SINK'"""
    with closing(sqlite3.connect(rdb.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        rows = sorted(database.execute(query).fetchall())
    if rows != sorted((n, expr, "wave", None) for n, expr in expressions.items()):
        raise ValueError("IOUT_SINK was not evaluated as a waveform for every Maestro test")


def review(stage, site, req, direct):
    evidence = stage / "maestro-evidence"
    native = (stage / "netlists/netlist").read_text(encoding="utf-8")
    names = [(s[0] + "_" + m).upper() for s in STATES for m in ("dc", "tran")]
    history, jobs = history_tests(stage, names, LIB, TB)
    try:
        check_plotted_outputs(stage, history, names)
    except (sqlite3.Error, ET.ParseError) as exc:
        raise ValueError("Unreadable saved Maestro state or results index: " + str(exc)) from exc
    saved = (evidence / "maestro-readback.txt").read_text(encoding="utf-8")
    records = {}
    for state in STATES:
        corner = state[0]
        traces = []
        for mode in ("dc", "tran"):
            name = (corner + "_" + mode).upper()
            job = jobs[name]
            deck_file = job / "netlist/input.scs"
            deck = deck_file.read_text(encoding="utf-8")
            if device_lines(deck) != device_lines(native):
                raise ValueError("ADE netlist differs from SI: " + name)
            flat = re.sub(r"\\\r?\n\s*", " ", deck)
            params = options(flat, "parameters")
            requested = variables(state, mode, req)
            if params.keys() != requested.keys():
                raise ValueError("Unexpected ADE variables: " + name)
            matches_options(params, requested)
            matches_options(
                options(flat, "simulatorOptions options"),
                dict(temp=str(state[1]), reltol="1e-5", vabstol="1e-7", iabstol="1e-13"),
            )
            if mode == "dc":
                matches_options(
                    options(flat, "dc dc"), dict(param="VOUT", start="0", stop="1.5", step="0.01")
                )
            else:
                matches_options(
                    options(flat, "tran tran"),
                    dict(
                        stop="{:.12g}n".format(observation_ns(req["max_settling_ns"])),
                        maxstep="200p",
                        strobeperiod="200p",
                        strobeoutput="strobeonly",
                        errpreset="conservative",
                    ),
                )
            includes = re.findall(
                r'(?m)^\s*(?:include|ahdl_include)\s+"([^"]+)"(?:\s+section=(\w+))?[ \t]*$', flat
            )
            if (
                len(includes) != 2
                or includes[0] != (site["model_file"], corner)
                or includes[1][1]
                or not includes[1][0].endswith("/maestro-evidence/" + mode + "-save.scs")
                or (evidence / (mode + "-save.scs")).read_text(encoding="utf-8") != save_file(mode)
            ):
                raise ValueError("Wrong ADE model, corner or saved signals: " + name)
            settings = [s for s in saved.splitlines() if s.startswith(name + " ")]
            if (
                len(settings) != 1
                or site["model_file"] not in settings[0]
                or '"' + corner + '"' not in settings[0]
            ):
                raise ValueError("Reopened ADE configuration differs: " + name)
            saved_options = dict(re.findall(r'\("([^"]+)" "([^"]+)"\)', settings[0]))
            form = (
                dict(anaName="dc", sweep="Design Variable", param="VOUT", start="0", stop="1.5", step="0.01")
                if mode == "dc"
                else dict(
                    anaName="tran",
                    stop="{:.12g}n".format(observation_ns(req["max_settling_ns"])),
                    maxstep="200p",
                    strobeperiod="200p",
                    strobeoutput="strobeonly",
                )
            )
            matches_options(saved_options, form)
            log_path = job / "psf/spectre.out"
            log = log_path.read_text(encoding="utf-8", errors="replace")
            success = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", log, re.I)
            loaded = set(re.findall(r"(?m)^Reading file:\s*([^\n]+)$", log))
            direct_job = read_json(stage / corner / mode / "job.json")
            if (
                not success
                or int(success[1])
                or not (set(direct_job["dependencies"]) | {includes[1][0]}) <= loaded
            ):
                raise ValueError("Incomplete ADE history or missing models: " + name)
            raw_names = ("dc.dc",) if mode == "dc" else ("tran.tran", "tran.tran.tran")
            paths = [job / "psf" / n for n in raw_names if (job / "psf" / n).is_file()]
            if len(paths) != 1:
                raise ValueError("Missing native ADE waveform: " + name)
            traces.append(paths[0])
            records[name] = dict(
                raw=paths[0].relative_to(stage).as_posix(),
                raw_sha256=sha256(paths[0]),
                deck_sha256=sha256(deck_file),
                warnings=int(success[2]),
                warning_codes=sorted(set(re.findall(r"WARNING \(([^)]+)\)", log))),
            )
        result = performance(traces[0], traces[1], state, req)
        if result["passed"] != direct[corner]["passed"]:
            raise ValueError("Direct and ADE electrical verdicts differ: " + corner)
        for key in ("worst_error_pct", "settling_ns", "output_current_uA_at_0p8V", "reference_node_V"):
            if not math.isclose(
                result["metrics"][key], direct[corner]["metrics"][key], abs_tol=1e-6, rel_tol=0.002
            ):
                raise ValueError("Direct and ADE waveforms differ: " + corner + "." + key)
        records[corner.upper() + "_DC"]["worst_error_pct"] = result["metrics"]["worst_error_pct"]
        records[corner.upper() + "_TRAN"]["settling_ns"] = result["metrics"]["settling_ns"]
    return dict(history=history, tests=records)
