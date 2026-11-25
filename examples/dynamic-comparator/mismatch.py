"""Sample signed decisions of an existing comparator with Spectre's PDK mismatch model."""

import argparse
import math
import re
from pathlib import Path

from measure_psf import analyze, sample, scalar_sweep
from audit_native import check_family
from run_native import cases, load_config
from spectre_run import TRANSIENT, execute, read_json, save_json, sha256


def mc_deck(original, model, corner, section, count, seed):
    include = 'include "{}" section={}'.format(model, corner)
    header, marker, tran = original.rpartition("\ntran tran ")
    if not marker or header.splitlines().count(include) != 1 or "\n" in tran.strip():
        raise ValueError("Expected the single model section and transient of the native comparator")
    header = header.replace(include, 'include "{}" section={}'.format(model, section), 1)
    return (header + "\nmc montecarlo variations=mismatch donominal=no savefamilyplots=yes "
            + "savemismatchparams=yes numruns={} seed={} {{\n".format(count, seed)
            + "tran tran " + tran.strip() + "\n}\n")


def sampled_vectors(path, count):
    blocks = re.split(r"(?m)^#(\d+)\s*$", path.read_text(encoding="utf-8"))
    if blocks[0].strip() or len(blocks) != 1 + 2 * count:
        raise ValueError("Incomplete Spectre mismatch parameter data")
    draws = {}
    for index, values in zip(blocks[1::2], blocks[2::2]):
        vector = tuple(float(value) for value in values.split())
        if int(index) in draws or not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("Duplicate or invalid Spectre mismatch parameter vector")
        draws[int(index)] = vector
    if set(draws) != set(range(1, count + 1)) or len({len(x) for x in draws.values()}) != 1:
        raise ValueError("Inconsistent mismatch vector dimensions or IDs")
    return len(set(draws.values()))


def sensitivity(rows, distinct):
    # Use the existing solver's 1e-7 V absolute tolerance and fixed observation times.
    spread = max(max(r["internal_diff_V"][j] for r in rows) - min(r["internal_diff_V"][j] for r in rows)
                 for j in range(3))
    return {"distinct_draws": distinct, "internal_diff_spread_V": spread,
            "response_observed": distinct > 1 and spread > 1e-6}


def nominal_deck(case, config, stage):
    control = stage / "simulation" / case["id"]
    origin = control / "input.scs"
    if read_json(control / "measurement.json").get("status") != "measured":
        raise ValueError("Require a measured nominal control for " + case["id"])
    dependencies = read_json(stage.parent / "common/model-dependencies.json")
    if str(config["model_file"]) not in dependencies or any(sha256(Path(p)) != h for p, h in dependencies.items()):
        raise ValueError("The PDK files differ from the characterized native run")
    native = (stage / "netlists/netlist").read_text(encoding="utf-8")
    check_family(native, config["mos_family"])
    if origin.read_text(encoding="utf-8") != TRANSIENT.format(model=config["model_file"], native=native, **case):
        raise ValueError("Recorded nominal deck differs from the supplied configuration or circuit")
    return origin, native


def run_draws(folder, original, config, section, count, seed):
    folder.mkdir()
    deck = folder / "input.scs"
    deck.write_text(mc_deck(original, config["model_file"], config["corner"],
                            section, count, seed), encoding="utf-8")
    execute([config["spectre"], "-64", "+mt=1", "input.scs", "-format", "psfascii", "-raw", "psf",
             "+log", "spectre.out"], folder, "console.out", 600)
    log = (folder / "spectre.out").read_text(encoding="utf-8", errors="replace")
    done = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", log, re.I)
    models = {Path(p.strip()).resolve() for p in re.findall(r"(?m)^Reading file:\s*([^\n]+)$", log)}
    if not done or int(done[1]) or config["model_file"] not in models:
        raise ValueError("Missing clean Spectre completion or selected PDK model")
    traces = {}
    for item in (folder / "psf").glob("*"):
        match = re.fullmatch(r"mc-(\d+)_tran\.tran(?:\.tran)?", item.name)
        if match:
            index = int(match[1])
            if index in traces:
                raise ValueError("Duplicate Monte Carlo sample " + str(index))
            traces[index] = item
    if set(traces) != set(range(1, count + 1)):
        raise ValueError("Missing requested mismatch traces: " + str(sorted(traces)))
    draws = folder / "psf/mc.mismatch.mcdata"
    parameters = folder / "psf/mc.mismatch.mcparam"
    return traces, {"deck_sha256": sha256(deck), "warnings": int(done[2]),
                    "draws_sha256": sha256(draws), "parameters_sha256": sha256(parameters),
                    "distinct_draws": sampled_vectors(draws, count)}


def verify_grid(axis):
    if len(axis) != 7801 or any(abs(t - i * 5e-12) > 1e-15 for i, t in enumerate(axis)):
        raise ValueError("Incomplete 39 ns mismatch trace at 5 ps strobe")


def verify_stimulus(data, rise, case):
    p, n, v = (sample(data["time"], data[k], rise - 50e-12) for k in ("SRC_P", "SRC_N", "VDD"))
    if not all(math.isclose(a, b, abs_tol=1e-6) for a, b in
               ((p - n, case["delta_mV"] * 1e-3), ((p + n) / 2, case["vcm_V"]), (v, case["vdd_V"]))):
        raise ValueError("Mismatch sample stimulus differs")


def run_sample_set(folder, case, config, stage, section, count, seed):
    origin, _native = nominal_deck(case, config, stage)
    traces, evidence = run_draws(folder, origin.read_text(encoding="utf-8"), config, section, count, seed)
    rows = []
    for index, trace in sorted(traces.items()):
        text = trace.read_text(encoding="utf-8")
        data = scalar_sweep(text, "time", ("SRC_P", "SRC_N", "VDD", "XDUT.XP", "XDUT.XN"))
        axis = data["time"]
        verify_grid(axis)
        stats = analyze(text, case["vdd_V"], case["vcm_V"], case["delta_mV"])
        if len(stats["cycles"]) != 3:
            raise ValueError("Incomplete clock sequence in sample " + str(index))
        for cycle in stats["cycles"]:
            verify_stimulus(data, cycle["clock_rise_ns"] * 1e-9, case)
        rise = stats["cycles"][1]["clock_rise_ns"] * 1e-9
        diff = [sample(axis, data["XDUT.XP"], rise + off) - sample(axis, data["XDUT.XN"], rise + off)
                for off in (100e-12, 200e-12, 300e-12)]
        rows.append({"index": index, "trace": trace.name, "sha256": sha256(trace), "internal_diff_V": diff,
                     "valid_decisions": stats["valid_decisions"], "all_cycles_correct": stats["precharge_and_decisions_pass"],
                     "delay_ps": stats["worst_delay_ps"], "energy_fJ": stats["mean_core_energy_fJ"],
                     "kickback_mV": stats["peak_diff_kickback_mV"]})
        row = rows[-1]
        row["unmet_limits"] = [k for k, limit in config["limits"].items() if row[k] is None or row[k] > limit]
    evidence.update(origin_sha256=sha256(origin), sensitivity=sensitivity(rows, evidence["distinct_draws"]),
                    samples=rows)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="Existing native benchmark output")
    parser.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--scenario", required=True, help="One named load and supply-ramp group from the configuration")
    parser.add_argument("--model-section", required=True, help="Site-confirmed PDK section with statistical mismatch")
    parser.add_argument("--runs", type=int, required=True, help="Number of draws per input polarity (2 or more)")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Fresh evidence directory")
    args = parser.parse_args()
    if not (args.runs >= 2 and args.seed > 0 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.model_section)):
        parser.error("Supply a supported positive seed, at least two samples and a model section name")
    config = load_config(args.config.resolve())
    conditions = {c["id"]: c for c in cases(config)}
    if args.scenario not in {c["name"] for c in config["scenarios"]}:
        parser.error("Unknown scenario")
    stage, output = args.run.resolve() / args.variant, args.out.resolve()
    output.mkdir(parents=True, mode=0o700)
    summary = {"status": "running", "seed": args.seed, "runs_per_sign": args.runs, "variation": "mismatch",
               "model_section": args.model_section, "mos_family": config["mos_family"], "variant": args.variant,
               "conditions": {}, "results": {}}
    save_json(output / "summary.json", summary)
    try:
        correct, within = {}, {}
        for sign in ("pos", "neg"):
            case = conditions[args.scenario + "_" + sign]
            summary["conditions"][sign] = case
            result = run_sample_set(output / sign, case, config, stage, args.model_section, args.runs, args.seed)
            detail = output / sign / "samples.json"
            save_json(detail, result)
            rows = result["samples"]
            correct[sign] = {r["index"] for r in rows if r["all_cycles_correct"]}
            within[sign] = {r["index"] for r in rows if r["all_cycles_correct"] and not r["unmet_limits"]}
            summary["results"][sign] = {"correct": sum(r["all_cycles_correct"] for r in rows),
                                        "within_declared_limits": len(within[sign]), "draws_sha256": result["draws_sha256"],
                                        "parameters_sha256": result["parameters_sha256"],
                                        "total": len(rows), "warnings": result["warnings"], "sensitivity": result["sensitivity"],
                                        "samples_sha256": sha256(detail)}
            save_json(output / "summary.json", summary)
        summary["status"] = ("all_sampled_decisions_correct" if all(r["correct"] == r["total"]
                             for r in summary["results"].values()) else "decision_failures_observed")
        if summary["status"] == "all_sampled_decisions_correct" and any(
                r["within_declared_limits"] < r["total"] for r in summary["results"].values()):
            summary["status"] = "sampled_limits_exceeded"
        if not all(r["sensitivity"]["response_observed"] for r in summary["results"].values()):
            summary["status"] = "mismatch_sensitivity_unconfirmed"
        if all(summary["results"]["pos"][k] == summary["results"]["neg"][k]
               for k in ("draws_sha256", "parameters_sha256")):
            summary["paired_draws"] = {"total": args.runs, "correct_both_signs": len(correct["pos"] & correct["neg"]),
                                       "within_limits_both_signs": len(within["pos"] & within["neg"])}
        else:
            summary["status"] = "unpaired_draws"
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        summary.update(status="invalid_evidence", reason=str(exc))
    save_json(output / "summary.json", summary)
    print(summary["status"], summary.get("reason", ""), flush=True)
    return 2 if summary["status"] == "invalid_evidence" else int(summary["status"] != "all_sampled_decisions_correct")


if __name__ == "__main__":
    raise SystemExit(main())
