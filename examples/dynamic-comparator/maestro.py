"""Create an editable comparator ADE view, reopen it and measure its native histories."""

import math
import re
from pathlib import Path

from schematic import HERE, LIB, TB, q
from spectre_run import execute, read_json, save_json, sha256
from measure_psf import scalar_sweep
from audit_native import check_family


def variables(case):
    return [("VDDVAL", "{:g}".format(case["vdd_V"])), ("VCM", "{:g}".format(case["vcm_V"])),
            ("VDIFF", "{:g}m".format(case["delta_mV"])), ("RSRC", "1k"),
            ("CLVAL", "{:g}f".format(case["cload_fF"])), ("TRSUP", "{:g}n".format(case["ramp_ns"]))]


def device_lines(deck):
    # The circuit and its parent are emitted by the same Cadence netlister.
    # Keep scope delimiters, device order, every terminal and all instance parameters.
    joined = re.sub(r"\\\r?\n\s*", " ", deck)
    return [line.split() for line in joined.splitlines()
            if re.match(r"^\s*(?:subckt\s|ends\s|\w+\s+\()", line)]


def check_deck(deck, control, case, model, saves):
    circuit = device_lines(control)
    if not circuit or device_lines(deck) != circuit:
        raise ValueError("Maestro circuit differs from the audited SI netlist")
    joined = re.sub(r"\\\r?\n\s*", " ", deck)
    def options(prefix):
        lines = re.findall("(?m)^" + prefix + r"\s+([^\n]+)", joined)
        if len(lines) != 1:
            raise ValueError("Expected one native " + prefix + " declaration")
        return dict(re.findall(r"(\w+)=([^\s]+)", lines[0]))
    if options("parameters") != dict(variables(case)):
        raise ValueError("Maestro design variables differ from the requested stimulus or load")
    expected = [("simulatorOptions options", {"temp": "{:g}".format(case["temp_C"]),
                 "reltol": "1e-5", "vabstol": "1e-7", "iabstol": "1e-13"}),
                ("tran tran", {"stop": "39n", "maxstep": "5p", "strobeperiod": "5p",
                 "strobeoutput": "strobeonly", "errpreset": "conservative"})]
    for line, required in expected:
        actual = options(line)
        if any(actual.get(k) != v for k, v in required.items()):
            raise ValueError("Maestro analysis or solver settings differ: " + line)
    includes = [line.strip() for line in joined.splitlines() if re.match(r"^\s*(include|ahdl_include)\s", line)]
    if includes != ['include "{}" section={}'.format(model, case["corner"]), 'include "{}"'.format(saves)]:
        raise ValueError("Maestro model, section or include order differs")


def history_tests(folder, expected, library=LIB, cell=TB):
    history = (folder / "maestro-evidence/maestro-history.txt").read_text(encoding="utf-8").splitlines()
    token = re.fullmatch(r'"([A-Za-z0-9_-]+\.\d+)"', history[0] if history else "")
    if not token:
        raise ValueError("Cannot identify the completed Maestro history")
    root = folder / "maestro-results" / library / cell / "maestro/results/maestro" / token[1]
    points = [p for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
    if len(points) != 1 or points[0].name != "1":
        raise ValueError("Expected one native Maestro design point for the fixed test matrix")
    observed = {p.name: p for p in points[0].iterdir() if p.is_dir() and not p.name.startswith(".")}
    if set(observed) != set(expected):
        raise ValueError("Missing or unexpected native Maestro tests")
    return token[1], observed


def review_view(stage, config, cases, measure, metrics):
    folder = stage / "maestro-evidence"
    requested = {"cases": cases, "limits": config["limits"]}
    if read_json(folder / "requested.json") != requested:
        raise ValueError("Saved coverage differs from the supplied design configuration")
    names = {case["id"].upper(): case for case in cases}
    history, jobs = history_tests(stage, names)
    control_netlist = (stage / "netlists/netlist").read_text(encoding="utf-8")
    check_family(control_netlist, config["mos_family"])
    dependencies = read_json(stage.parent / "common/model-dependencies.json")
    if str(config["model_file"]) not in dependencies or any(sha256(Path(p)) != digest for p, digest in dependencies.items()):
        raise ValueError("The model inputs changed since direct Spectre characterization")
    completed = {}
    for name, case in names.items():
        job = jobs[name]
        deck, log = job / "netlist/input.scs", job / "psf/spectre.out"
        check_deck(deck.read_text(encoding="utf-8"), control_netlist, case, config["model_file"], folder / "save.scs")
        output = log.read_text(encoding="utf-8", errors="replace")
        clean = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", output, re.I)
        loaded = {Path(p.strip()).resolve() for p in re.findall(r"(?m)^Reading file:\s*([^\n]+)$", output)}
        required = {deck.resolve(), (folder / "save.scs").resolve()} | {Path(p) for p in dependencies}
        if not (clean and int(clean[1]) == 0 and required <= loaded):
            raise ValueError("Missing completed Spectre run or model readback: " + name)
        traces = [p for p in (job / "psf").glob("tran.tran*") if p.name in ("tran.tran", "tran.tran.tran")]
        if len(traces) != 1:
            raise ValueError("Expected one native transient for " + name)
        trace = traces[0]
        axis = scalar_sweep(trace.read_text(encoding="utf-8"), "time", ())["time"]
        if len(axis) != 7801 or any(abs(t - i * 5e-12) > 1e-15 for i, t in enumerate(axis)):
            raise ValueError("Incomplete 0–39 ns waveform or incorrect 5 ps strobe grid: " + name)
        native = measure(trace, case)
        control_dir = stage / "simulation" / case["id"]
        control_record = read_json(control_dir / "measurement.json")
        if control_record.get("status") != "measured":
            raise ValueError("Missing direct control measurement for " + name)
        direct = measure(stage / control_record["trace"], case)
        values = {key: native[field] for key, field, *_ in metrics}
        for key, field, *_ in metrics:
            if not math.isclose(values[key], direct[field], rel_tol=0.01, abs_tol=1e-6):
                raise ValueError("Maestro and direct Spectre differ beyond 1% for " + name + "." + key)
        completed[name] = {"trace": str(trace.relative_to(stage)), "sha256": sha256(trace),
                           "deck_sha256": sha256(deck), "log_sha256": sha256(log), "samples": len(axis),
                           "warnings": int(clean[2]), "warning_codes": sorted(set(re.findall(r"WARNING \(([^)]+)\)", output))),
                           "decisions": native["valid_decisions"], "measurements": values,
                           "unmet": [key for key, limit in config["limits"].items() if values[key] > limit]}
    return {"status": "measured", "history": history, "tests": completed}


def launch_view(stage, config, cases, extra_analysis="", model_section=None):
    folder = stage / "maestro-evidence"
    folder.mkdir()
    results = stage / "maestro-results"
    results.mkdir()
    saves = folder / "save.scs"
    saves.write_text("simulator lang=spectre\nsave VSUP:p XDUT.XP XDUT.XN XDUT.TAIL\n" + extra_analysis, encoding="utf-8")
    tests = {case["id"].upper(): case for case in cases}
    rows = ["({} ({}))".format(q(name), " ".join("({} {})".format(q(k), q(v)) for k, v in variables(case)))
            for name, case in sorted(tests.items())]
    values = {"cmpLib": LIB, "cmpTB": TB, "cmpModel": config["model_file"], "cmpCorner": model_section or config["corner"],
              "cmpSaves": saves, "cmpSpectre": config["spectre"],
              "cmpTemp": "{:g}".format(config["temp_C"]), "cmpReadback": folder / "maestro-readback.txt",
              "cmpHistory": folder / "maestro-history.txt"}
    prefix = "\n".join(k + "=" + q(str(v)) for k, v in sorted(values.items())) + "\n"
    prefix += "cmpCases='(" + "\n".join(rows) + ")\n"
    prefix += 'envSetVal("asimenv.startup" "projectDir" \'string {})\n'.format(q(str(results)))
    prefix += 'envSetVal("adexl.historyNamePrefix" "showNameHistoryForm" \'boolean nil)\n'
    prefix += "load({})\n".format(q(str(HERE / "maestro_native.il")))
    save_json(folder / "requested.json", {"cases": cases, "limits": config["limits"]})
    for phase, procedure in (("create", "cmpConfigure"), ("run", "cmpRun")):
        script = folder / (phase + ".il")
        script.write_text(prefix + "unless(errset({}() t) exit(3)) exit(0)\n".format(procedure), encoding="utf-8")
        execute([config["virtuoso"], "-nograph", "-log", folder / ("maestro-" + phase + ".log"),
                 "-restore", script], stage, "maestro-" + phase + ".stdout", 600)
    return folder


def run_view(stage, config, cases, measure, metrics):
    folder = launch_view(stage, config, cases)
    report = review_view(stage, config, cases, measure, metrics)
    save_json(folder / "maestro-report.json", report)
    return report


if __name__ == "__main__":
    import argparse
    import json
    from run_native import METRICS, cases, load_config, measure
    parser = argparse.ArgumentParser(description="Re-evaluate both saved native Maestro histories from raw waveforms.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="Existing comparator output directory")
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    try:
        reports = {v: review_view(args.out.resolve() / v, config, cases(config), measure, METRICS)
                   for v in ("baseline", "candidate")}
    except (KeyError, ValueError, OSError) as exc:
        parser.exit(2, "Native evidence rejected: {}\n".format(exc))
    print(json.dumps(reports, indent=2, allow_nan=False))
    raise SystemExit(int(any(t["unmet"] for t in reports["candidate"]["tests"].values())))
