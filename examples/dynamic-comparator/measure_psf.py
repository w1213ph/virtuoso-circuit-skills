"""Evaluate a comparator transient from Spectre PSF ASCII scalar traces."""

import argparse
import bisect
import json
import math
from pathlib import Path


PERIOD = 10e-9
HALF_EDGE = 50e-12


def scalar_sweep(text, sweep, names):
    columns = {name: [] for name in (sweep, *names)}
    in_values = False
    row = {}

    def append():
        if not row:
            return
        missing = columns.keys() - row.keys()
        if missing:
            raise ValueError(f"Missing scalar traces at {row.get(sweep)}: {sorted(missing)}")
        for key, column in columns.items():
            column.append(row[key])

    for line in text.splitlines():
        if not in_values:
            in_values = line.strip() == "VALUE"
            continue
        if line.strip() == "END":
            break
        if not line.startswith('"'):
            continue
        _, name, value = line.split('"', 2)
        if name == sweep:
            append()
            row = {}
        if name in columns:
            value = float(value.strip())
            if not math.isfinite(value) or name in row:
                raise ValueError(f"Invalid scalar sample for {name}")
            row[name] = value
    append()
    axis = columns[sweep]
    if len(axis) < 2 or any(right <= left for left, right in zip(axis, axis[1:])):
        raise ValueError("Need a strictly increasing scalar sweep")
    return columns


def edges(times, samples, threshold, rising):
    for i in range(1, len(times)):
        prev, now = samples[i - 1:i + 1]
        crossing = prev < threshold <= now if rising else prev >= threshold > now
        if crossing:
            fraction = (threshold - prev) / (now - prev)
            yield times[i - 1] + fraction * (times[i] - times[i - 1])


def sample(times, values, instant):
    if not times[0] <= instant <= times[-1]:
        raise ValueError("Measurement outside recorded time range")
    i = bisect.bisect_left(times, instant)
    if times[i] == instant:
        return values[i]
    span = times[i] - times[i - 1]
    return values[i - 1] + (values[i] - values[i - 1]) * (instant - times[i - 1]) / span


def integral(times, values, start, stop):
    lo = bisect.bisect_right(times, start)
    hi = bisect.bisect_left(times, stop)
    grid = [start, *times[lo:hi], stop]
    samples = [sample(times, values, start), *values[lo:hi], sample(times, values, stop)]
    return math.fsum((t2 - t1) * (y1 + y2) / 2 for t1, t2, y1, y2 in
                     zip(grid, grid[1:], samples, samples[1:]))


def analyze(text, vdd, vcm, delta_mV):
    if vdd <= 0 or not delta_mV:
        raise ValueError("Provide positive nominal supply and nonzero signed stimulus")
    traces = ("CLK", "VDD", "INP", "INN", "OUTP", "OUTN", "VSUP:p")
    samples = scalar_sweep(text, "time", traces)
    times = samples["time"]
    rising = list(edges(times, samples["CLK"], 0.5 * vdd, True))
    falling = list(edges(times, samples["CLK"], 0.5 * vdd, False))
    high, low = ("OUTP", "OUTN") if delta_mV > 0 else ("OUTN", "OUTP")
    core_power = [-v * i for v, i in zip(samples["VDD"], samples["VSUP:p"])]
    cycles = []
    for first, second in zip(rising, rising[1:]):
        fall = next((fall for fall in falling if first < fall < second), None)
        if fall is None or not math.isclose(second - first, PERIOD, rel_tol=0.002):
            raise ValueError("Missing evaluation/reset interval or wrong clock period")
        left = bisect.bisect_left(times, first)
        right = bisect.bisect_right(times, fall - HALF_EDGE)
        if right <= left:
            raise ValueError("No samples in evaluation window")
        valid_from = right
        for i in range(right - 1, left - 1, -1):
            if samples[high][i] < 0.8 * vdd or samples[low][i] > 0.2 * vdd:
                break
            valid_from = i
        delay = None if valid_from == right else (times[valid_from] - first) * 1e12
        precharged = all(sample(times, samples[name], first - HALF_EDGE) >= 0.8 * vdd
                         for name in ("OUTP", "OUTN"))
        kick_indices = range(bisect.bisect_left(times, first - HALF_EDGE), right)
        kickback = max(abs(samples["INP"][i] - samples["INN"][i] - delta_mV / 1000)
                       for i in kick_indices) * 1000
        common = max(abs((samples["INP"][i] + samples["INN"][i]) / 2 - vcm)
                     for i in kick_indices) * 1000
        cycles.append({
            "clock_rise_ns": first * 1e9,
            "precharged": precharged,
            "decision_delay_ps": delay,
            "core_energy_fJ": integral(times, core_power, first, second) * 1e15,
            "differential_kickback_mV": kickback,
            "common_mode_kickback_mV": common,
        })
    if not cycles:
        raise ValueError("Record two clock rises and a complete evaluation interval")
    good = all(cycle["precharged"] and cycle["decision_delay_ps"] is not None for cycle in cycles)
    return {
        "valid_decisions": int(sum(cycle["decision_delay_ps"] is not None for cycle in cycles)),
        "precharge_and_decisions_pass": good,
        "worst_delay_ps": max(cycle["decision_delay_ps"] for cycle in cycles) if good else None,
        "mean_core_energy_fJ": math.fsum(cycle["core_energy_fJ"] for cycle in cycles) / len(cycles),
        "peak_diff_kickback_mV": max(cycle["differential_kickback_mV"] for cycle in cycles),
        "peak_common_kickback_mV": max(cycle["common_mode_kickback_mV"] for cycle in cycles),
        "cycles": cycles,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--vdd", type=float, required=True)
    parser.add_argument("--vcm", type=float, required=True)
    parser.add_argument("--delta-mv", type=float, required=True)
    args = parser.parse_args()
    result = analyze(args.trace.read_text(encoding="utf-8"), args.vdd, args.vcm, args.delta_mv)
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(0 if result["precharge_and_decisions_pass"] else 1)


if __name__ == "__main__":
    main()
