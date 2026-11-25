"""Measure per-draw comparator decision thresholds on a signed differential-input grid."""

import argparse
import bisect
from collections import Counter
import math
from pathlib import Path
import re

from measure_psf import HALF_EDGE, PERIOD, edges, sample, scalar_sweep
from mismatch import nominal_deck, run_draws, verify_grid, verify_stimulus
from run_native import cases, load_config
from spectre_run import TRANSIENT, read_json, save_json, sha256


DEFAULT_GRID = [-32, -24, -16, -12, -8, -6, -5, -4, -2, 0, 2, 4, 5, 6, 8, 12, 16, 24, 32]


def classify(data, case):
    """Return the actual stable rail decision, even with zero input or reversed polarity."""
    times, vdd = data["time"], case["vdd_V"]
    verify_grid(times)
    rises = list(edges(times, data["CLK"], vdd / 2, True))
    falls = list(edges(times, data["CLK"], vdd / 2, False))
    if len(rises) != 4:
        raise ValueError("Expected three complete periods and a fourth clock rise")
    cycles = []
    for rise, following in zip(rises, rises[1:]):
        falling = [t for t in falls if rise < t < following]
        if len(falling) != 1 or not math.isclose(following - rise, PERIOD, rel_tol=0.002):
            raise ValueError("Missing evaluation/reset interval or wrong clock period")
        verify_stimulus(data, rise, case)
        precharged = all(sample(times, data[k], rise - HALF_EDGE) >= 0.8 * vdd for k in ("OUTP", "OUTN"))
        left = bisect.bisect_left(times, rise)
        right = bisect.bisect_right(times, falling[0] - HALF_EDGE)
        sign, delay = 0, None
        if precharged:
            for direction, high, low in ((1, "OUTP", "OUTN"), (-1, "OUTN", "OUTP")):
                first = right
                while first > left and data[high][first-1] >= 0.8 * vdd and data[low][first-1] <= 0.2 * vdd:
                    first -= 1
                if first < right and times[right-1] - times[first] >= HALF_EDGE:
                    sign, delay = direction, (times[first] - rise) * 1e12
                    break
        cycles.append((sign, delay))
    signs = {sign for sign, _ in cycles}
    sign = signs.pop() if len(signs) == 1 else 0
    observe = rises[1] + 200e-12
    internal = sample(times, data["XDUT.XP"], observe) - sample(times, data["XDUT.XN"], observe)
    return {"decision": sign, "delay_ps": max(d for _, d in cycles) if sign else None,
            "cycle_decisions": [s for s, _ in cycles], "internal_diff_V": internal}


def bracket(grid, signs):
    negatives = [x for x, s in zip(grid, signs) if s == -1]
    positives = [x for x, s in zip(grid, signs) if s == 1]
    lower, upper = (max(negatives) if negatives else None), (min(positives) if positives else None)
    if lower is not None and upper is not None and lower >= upper:
        status = "nonmonotone"
    elif 0 in signs:
        status = "unresolved"
    elif lower is None:
        status = "below_grid"
    elif upper is None:
        status = "above_grid"
    else:
        status = "bracketed"
    return {"status": status, "last_negative_mV": lower, "first_positive_mV": upper}


def scan(output, stage, config, scenario, section, seed, count, grid):
    if config["mos_family"] != "statistical":
        raise ValueError("Use the audited statistical device family")
    if (len(grid) < 3 or any(not math.isfinite(v) for v in grid) or grid != sorted(set(grid))
            or not grid[0] < 0 < grid[-1] or 0 not in grid):
        raise ValueError("Supply an increasing unique grid with both signs and zero")
    conditions = {c["id"]: c for c in cases(config)}
    control = conditions[scenario + "_pos"]
    origin, native = nominal_deck(control, config, stage)
    nominal_deck(conditions[scenario + "_neg"], config, stage)
    summary = {"status": "running", "seed": seed, "runs": count, "grid_mV": grid, "corner": config["corner"],
               "section": section, "control": control, "origin_sha256": sha256(origin), "levels": []}
    save_json(output / "summary.json", summary)
    signs = [[] for _ in range(count)]
    identity = None
    observed = False
    for index, delta in enumerate(grid):
        case = dict(control, delta_mV=delta)
        deck = TRANSIENT.format(model=config["model_file"], native=native, **case)
        directory = output / "level-{:02d}".format(index)
        traces, evidence = run_draws(directory, deck, config, section, count, seed)
        pair = (evidence["draws_sha256"], evidence["parameters_sha256"])
        if identity is not None and pair != identity:
            raise ValueError("The mismatch draw values or parameter identities changed across input levels")
        identity = pair
        rows = []
        for draw, trace in sorted(traces.items()):
            data = scalar_sweep(trace.read_text(encoding="utf-8"), "time",
                                ("CLK", "OUTP", "OUTN", "SRC_P", "SRC_N", "VDD", "XDUT.XP", "XDUT.XN"))
            row = classify(data, case)
            row.update(draw=draw, trace=trace.name, sha256=sha256(trace))
            signs[draw-1].append(row["decision"])
            rows.append(row)
        internal = [row["internal_diff_V"] for row in rows]
        observed |= evidence["distinct_draws"] > 1 and max(internal) - min(internal) > 1e-6
        detail = directory / "decisions.json"
        save_json(detail, dict(evidence, delta_mV=delta, samples=rows))
        summary["levels"].append({"delta_mV": delta, "directory": directory.name, "sha256": sha256(detail)})
        save_json(output / "summary.json", summary)
        print("Measured {} mV, {} draws".format(delta, count), flush=True)
    summary.update(draws_sha256=identity[0], parameters_sha256=identity[1], response_observed=observed,
                   brackets=[dict(bracket(grid, s), draw=i, decisions=s) for i, s in enumerate(signs, 1)])
    summary["counts"] = dict(Counter(row["status"] for row in summary["brackets"]))
    summary["status"] = "measured" if observed else "mismatch_sensitivity_unconfirmed"
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--model-section", required=True)
    parser.add_argument("--runs", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--grid-mv", nargs="+", type=float, default=DEFAULT_GRID)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.seed <= 0 or args.runs < 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.model_section):
        parser.error("Expected a positive seed, at least two draws and a model section name")
    output = args.out.resolve()
    output.mkdir(parents=True, mode=0o700)
    try:
        result = scan(output, args.run.resolve() / args.variant, load_config(args.config.resolve()), args.scenario,
                      args.model_section, args.seed, args.runs, args.grid_mv)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        saved = output / "summary.json"
        result = read_json(saved) if saved.is_file() else {}
        result.update(status="invalid_evidence", reason=str(exc))
    save_json(output / "summary.json", result)
    print(result["status"], result.get("reason", ""), flush=True)
    return 0 if result["status"] == "measured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
