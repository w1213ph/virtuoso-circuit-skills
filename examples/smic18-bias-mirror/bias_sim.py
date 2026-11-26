"""Characterize SI-exported devices and measure native Spectre DC and turn-on traces."""

import csv
import math
import re
from pathlib import Path

from measure import observation_ns, sample_metrics
from measure_psf import scalar_sweep
from spectre_run import read_json, run_spectre, save_json, sha256


STATES = (("tt", 27, 1.8), ("ss", 85, 1.62), ("ff", -40, 1.8))
LENGTHS = (0.5, 1.0)
OP_FIELDS = ("gm", "gds", "vdsat", "cgg", "cgd")
NODES = ("IOUT", "IREF", "VFORCE:p", "VSUP:p", "VDD")
BRANCH_OP = tuple(
    "XDUT.{}:{}".format(name, field) for name in ("MREF", "MOUT") for field in ("gm", "gds", "vdsat")
)


def analysis(folder, deck, filename, site, tool=None):
    names = (filename, "tran.tran.tran") if filename == "tran.tran" else (filename,)
    if tool:
        folder.mkdir()
        (folder / "input.scs").write_text(deck, encoding="utf-8")
        trace, deps, warnings = run_spectre(tool, folder, Path(site["model_file"]), names)
        save_json(
            folder / "job.json",
            dict(
                trace=trace.name,
                raw_sha256=sha256(trace),
                deck_sha256=sha256(folder / "input.scs"),
                log_sha256=sha256(folder / "spectre.out"),
                dependencies=deps,
                warnings=warnings,
            ),
        )
    job = read_json(folder / "job.json")
    found = [folder / "psf" / name for name in names if (folder / "psf" / name).is_file()]
    if len(found) != 1 or found[0].name != job["trace"]:
        raise ValueError("Missing or ambiguous native analysis: " + str(folder))
    trace = found[0]
    log = (folder / "spectre.out").read_text(encoding="utf-8", errors="replace")
    summary = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", log, re.I)
    loaded = set(re.findall(r"(?m)^Reading file:\s*([^\n]+)$", log))
    if (
        job["raw_sha256"] != sha256(trace)
        or job["deck_sha256"] != sha256(folder / "input.scs")
        or job["log_sha256"] != sha256(folder / "spectre.out")
        or (folder / "input.scs").read_text(encoding="utf-8") != deck
        or site["model_file"] not in job["dependencies"]
        or job["dependencies"][site["model_file"]] != site["model_sha256"]
        or not set(job["dependencies"]) <= loaded
        or not summary
        or int(summary[1])
        or int(summary[2]) != job["warnings"]
    ):
        raise ValueError(
            "Missing or inconsistent Spectre input, dependency, log or raw result: " + str(folder)
        )
    return trace


def model_header(site, state):
    return [
        "simulator lang=spectre",
        "global 0",
        'include "{}" section={}'.format(site["model_file"], state[0]),
        "simulator lang=spectre",
        "opt options temp={} reltol=1e-5 vabstol=1e-7 iabstol=1e-13".format(state[1]),
    ]


def lookup_deck(site, state, cards):
    lines = model_header(site, state) + ["parameters VGMAG=0", "VG (G 0) vsource dc=VGMAG"]
    curves, saves = [], []
    for i, length in enumerate(LENGTHS):
        for j, drain in enumerate((0.40, 0.65, 1.20)):
            for k, body in enumerate((0.0, -0.15)):
                tag = "N{}{}{}".format(i, j, k)
                lines += [
                    "VD{0} (D{0} 0) vsource dc={1:g}".format(tag, drain),
                    "VB{0} (B{0} 0) vsource dc={1:g}".format(tag, body),
                    "M{0} (D{0} G 0 B{0}) {1}".format(tag, cards[length]),
                ]
                fields = ["VD" + tag + ":p"] + ["M" + tag + ":" + x for x in OP_FIELDS]
                saves.extend(fields)
                curves.append((length, drain, body, fields))
    lines += [
        "saveOptions options save=selected",
        "save " + " ".join(saves),
        "gate_sweep dc param=VGMAG start=0 stop={:g} step={:.12g}".format(state[2], state[2] / 180),
    ]
    return "\n".join(lines) + "\n", curves


def lookup(folder, site, state, cards, tool=None):
    deck, curves = lookup_deck(site, state, cards)
    raw = analysis(folder, deck, "gate_sweep.dc", site, tool)
    cols = scalar_sweep(
        raw.read_text(encoding="utf-8"), "VGMAG", [name for *_, fields in curves for name in fields]
    )
    axis = cols["VGMAG"]
    if len(axis) != 181 or any(abs(x - i * state[2] / 180) > 1e-7 for i, x in enumerate(axis)):
        raise ValueError("Incomplete native gate sweep")
    rows = []
    for length, drain, body, fields in curves:
        for gate, measured, gm, gds, vdsat, cgg, cgd in zip(axis, *(cols[n] for n in fields)):
            current = abs(measured)
            rows.append(
                dict(
                    polarity="N",
                    length_um=length,
                    width_um=2.0,
                    vds_V=drain,
                    vbs_V=body,
                    abs_vgs_V=gate,
                    abs_id_uA=current * 1e6,
                    id_per_width_uA_um=current * 1e6 / 2,
                    gm_id_per_V=gm / current if current >= 1e-10 else None,
                    gm_uS=gm * 1e6,
                    gds_uS=gds * 1e6,
                    gm_gds=gm / gds if gds > 0 else None,
                    vdsat_V=abs(vdsat),
                    cgg_fF=cgg * 1e15,
                    cgd_signed_fF=cgd * 1e15,
                )
            )
    table = folder / "table.csv"
    if tool:
        with table.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    with table.open(encoding="utf-8", newline="") as stream:
        table_rows = csv.DictReader(stream)
        if table_rows.fieldnames != list(rows[0]):
            raise ValueError("Unexpected native lookup columns")
        for row in rows:
            saved = next(table_rows, None)
            if saved != {k: "" if v is None else str(v) for k, v in row.items()}:
                raise ValueError("Lookup table differs from the native device curves")
        if next(table_rows, None) is not None:
            raise ValueError("Unexpected trailing device rows")
    return rows


def hold_ns(req):
    return max(1000.0, observation_ns(req["max_settling_ns"]) + 20.0)


def circuit_deck(site, state, native, req, mode):
    current = req["reference_uA"]
    start = current if mode == "dc" else 0
    hold = hold_ns(req)
    lines = model_header(site, state) + [
        "parameters VDDVAL={:g} VOUT=0.8 ISET={:.12g}u ISTART={:.12g}u THOLD={:.12g}n TPER={:.12g}n".format(
            state[2], current, start, hold, 2 * hold
        ),
        native,
        "saveOptions options save=selected",
        "save " + " ".join(NODES + (BRANCH_OP if mode == "dc" else ())),
    ]
    if mode == "dc":
        lines.append("dc dc param=VOUT start=0 stop=1.50 step=0.01")
    else:
        lines.append(
            "tran tran stop={:.12g}n maxstep=200p strobeperiod=200p strobeoutput=strobeonly errpreset=conservative".format(
                observation_ns(req["max_settling_ns"])
            )
        )
    return "\n".join(lines) + "\n"


def waveform(path, axis, names):
    columns = scalar_sweep(path.read_text(encoding="utf-8"), axis, names)
    return list(zip(columns[axis], *(columns[n] for n in NODES))), columns


def performance(dc_path, tran_path, state, req):
    dc, columns = waveform(dc_path, "VOUT", NODES + BRANCH_OP)
    transient, _ = waveform(tran_path, "time", NODES)
    m = sample_metrics(
        dc,
        transient,
        state[2],
        req["reference_uA"],
        req["max_error_pct"],
        req["max_settling_ns"],
        allow_unsettled=True,
    )
    op = {}
    gate = dc[80][2]
    for device, current, vds in (("MREF", -dc[80][4], gate), ("MOUT", -dc[80][3], dc[80][1])):
        gm, gds, sat = (columns["XDUT." + device + ":" + field][80] for field in ("gm", "gds", "vdsat"))
        if min(current, gm, gds, sat) <= 0:
            raise ValueError("Invalid native mirror operating point: " + device)
        op[device] = dict(
            current_uA=current * 1e6,
            vgs_V=gate,
            vds_V=vds,
            vbs_V=0.0,
            gm_id_per_V=gm / current,
            gm_uS=gm * 1e6,
            gds_uS=gds * 1e6,
            vdsat_V=sat,
            saturation_margin_V=vds - sat,
        )
    passed = (
        m["worst_error_pct"] <= req["max_error_pct"]
        and m["settling_ns"] is not None
        and m["settling_ns"] <= req["max_settling_ns"]
    )
    return dict(metrics=m, operating_point=op, passed=passed)


def measure_cases(folder, site, native, req, tool=None):
    result = {}
    for state in STATES:
        case = folder / state[0]
        if tool:
            case.mkdir()
        traces = []
        for mode, name in (("dc", "dc.dc"), ("tran", "tran.tran")):
            traces.append(
                analysis(case / mode, circuit_deck(site, state, native, req, mode), name, site, tool)
            )
        result[state[0]] = performance(traces[0], traces[1], state, req)
    return result
