"""Reproduce a signed mismatch measurement inside a fresh editable Maestro test history."""

import argparse
import math
from pathlib import Path
import re
import shutil

from maestro import check_deck, device_lines, history_tests, launch_view
from measure_psf import analyze, scalar_sweep
from mismatch import mc_deck, nominal_deck
from offset import classify
from run_native import build, cases, load_config
from schematic import q
from spectre_run import read_json, save_json, sha256


def make_viewer(output, trace, draw, case):
    """Index the unchanged child PSF as one transient for ViVA's top-level results browser."""
    index = (trace.parent / "logFile").read_text(encoding="utf-8")
    header, marker, records = index.partition("\nVALUE\n")
    step = re.search(r'(?ms)^"tran-tran" "analysisInst" \(.*?(?=^"[^"\n]+" "analysisInst" \(|^END$)', records)
    if not marker or not step or '"tran.tran.tran"' not in step[0]:
        raise ValueError("Cannot isolate the native top-level transient index")
    folder = output / "selected-psf"
    folder.mkdir()
    raw = folder / "tran.tran.tran"
    shutil.copyfile(trace, raw)
    if sha256(trace) != sha256(raw):
        raise ValueError("The selected waveform changed during export")
    (folder / "logFile").write_text(header + marker + step[0].rstrip() + "\nEND\n", encoding="utf-8")
    title = "draw {} {:+g} mV".format(draw, case["delta_mV"])
    script = ('let((clk outp outn)\n  openResults({})\n  selectResult("tran-tran")\n'.format(q(str(folder))) +
              '  clk=getData("CLK") outp=getData("OUTP") outn=getData("OUTN")\n' +
              '  unless(drIsWaveform(outp) && drVectorLength(drGetWaveformXVec(outp)) == 7801\n' +
              '    error("Cannot read selected comparator waveform"))\n' +
              '  printf("CMP18_WAVE_POINTS %d\\n" drVectorLength(drGetWaveformXVec(outp)))\n' +
              '  plot(clk outp outn ?expr list("CLK" {} "OUTN")))\n'.format(q("OUTP  " + title)))
    (output / "open-wave.il").write_text(script, encoding="utf-8")


def replay(output, stage, config, mc, sign, draw):
    summary = read_json(mc / "summary.json")
    if config["mos_family"] != "statistical" or summary["mos_family"] != "statistical" or not summary.get("paired_draws"):
        raise ValueError("Require a paired statistical mismatch run")
    case = summary["conditions"][sign]
    if case not in cases(config) or not summary["results"][sign]["sensitivity"]["response_observed"]:
        raise ValueError("The recorded signed stimulus or mismatch sensitivity is unconfirmed")
    section = summary["model_section"]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", section):
        raise ValueError("Invalid model section")
    origin, native = nominal_deck(case, config, stage)
    direct = mc / sign
    details = direct / "samples.json"
    if sha256(details) != summary["results"][sign]["samples_sha256"]:
        raise ValueError("The direct mismatch measurement changed")
    record = read_json(details)
    expected = next((r for r in record["samples"] if r["index"] == draw), None)
    if expected is None or Path(expected["trace"]).name != expected["trace"]:
        raise ValueError("Unknown sample or unexpected trace name")
    trace = direct / "psf" / expected["trace"]
    deck = mc_deck(origin.read_text(encoding="utf-8"), config["model_file"], case["corner"],
                   section, summary["runs_per_sign"], summary["seed"])
    if (record["origin_sha256"] != sha256(origin) or sha256(trace) != expected["sha256"]
            or deck != (direct / "input.scs").read_text(encoding="utf-8")):
        raise ValueError("The source circuit, simulation settings or direct trace differ")
    # ADE includes this analysis in the test's Spectre input and keeps its family in the native history.
    analysis = "mc montecarlo " + deck.rsplit("\nmc montecarlo ", 1)[1].replace("\ntran tran ", "\nsampletran tran ", 1)
    (output / "common").mkdir()
    shutil.copyfile(stage.parent / "common/testbench.scs", output / "common/testbench.scs")
    fresh = output / summary["variant"]
    build(fresh, summary["variant"], config)
    if device_lines((fresh / "netlists/netlist").read_text(encoding="utf-8")) != device_lines(native):
        raise ValueError("Rebuilt geometry, connectivity or instance order differs from the source")
    evidence = launch_view(fresh, config, [case], analysis, section)
    name = case["id"].upper()
    history, jobs = history_tests(fresh, {name})
    job = jobs[name]
    check_deck((job / "netlist/input.scs").read_text(encoding="utf-8"), native, dict(case, corner=section),
               config["model_file"], evidence / "save.scs")
    raw = job / "psf"
    log = (raw / "spectre.out").read_text(encoding="utf-8", errors="replace")
    clean = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", log, re.I)
    loaded = {Path(s.strip()).resolve() for s in re.findall(r"(?m)^Reading file:\s*([^\n]+)$", log)}
    if not clean or int(clean[1]) or config["model_file"] not in loaded:
        raise ValueError("Missing clean completion or selected PDK model in native history")
    matches = [f for f in raw.glob("mc-*_sampletran.tran*")
               if re.fullmatch(r"mc-0*{}_sampletran\.tran(?:\.tran)?".format(draw), f.name)]
    if len(matches) != 1:
        raise ValueError("Expected the selected draw in the native ADE history at " + str(raw))
    psf = matches[0]
    identity = {}
    for suffix in ("mcdata", "mcparam"):
        filename = "mc.mismatch." + suffix
        identity[suffix] = sha256(raw / filename)
        if identity[suffix] != sha256(direct / "psf" / filename):
            raise ValueError("The native ADE mismatch parameter values or names differ from the direct run")
    comparisons = []
    for path in (trace, psf):
        text = path.read_text(encoding="utf-8")
        data = scalar_sweep(text, "time", ("CLK", "OUTP", "OUTN", "SRC_P", "SRC_N", "VDD", "XDUT.XP", "XDUT.XN"))
        row = classify(data, case)
        metrics = analyze(text, case["vdd_V"], case["vcm_V"], case["delta_mV"])
        comparisons.append(dict(row, energy_fJ=metrics["mean_core_energy_fJ"], kickback_mV=metrics["peak_diff_kickback_mV"]))
    a, b = comparisons
    if (a["cycle_decisions"] != b["cycle_decisions"] or (a["delay_ps"] is None) != (b["delay_ps"] is None)
            or any(not math.isclose(a[k], b[k], rel_tol=.01, abs_tol=1e-6)
                   for k in ("energy_fJ", "kickback_mV", "internal_diff_V"))
            or (a["delay_ps"] is not None and abs(a["delay_ps"] - b["delay_ps"]) > 5.01)):
        raise ValueError("The native ADE waveform and direct draw do not agree")
    make_viewer(output, psf, draw, case)
    return {"status": "reproduced", "history": history, "test": name, "seed": summary["seed"], "draw": draw,
            "delta_mV": case["delta_mV"], "direct": a, "maestro": b, "trace": str(psf.relative_to(output)),
            "sha256": sha256(psf), "parameter_hashes": identity, "warnings": int(clean[2]),
            "warning_codes": sorted(set(re.findall(r"WARNING \(([^)]+)\)", log)))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("config", "run", "mc", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--sign", choices=("pos", "neg"), required=True)
    parser.add_argument("--draw", type=int, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(mode=0o700, parents=True)
    try:
        config = load_config(args.config.resolve())
        original = args.mc.resolve()
        variant = read_json(original / "summary.json")["variant"]
        if variant not in ("baseline", "candidate"):
            raise ValueError("Unknown source variant")
        report = replay(output, args.run.resolve() / variant, config, original, args.sign, args.draw)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        report = {"status": "invalid_evidence", "reason": str(exc)}
    save_json(output / "replay.json", report)
    print(report["status"], report.get("reason", ""), flush=True)
    return int(report["status"] != "reproduced")


if __name__ == "__main__":
    raise SystemExit(main())
