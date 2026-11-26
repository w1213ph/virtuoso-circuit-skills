"""Measure nominal PVT, paired local mismatch, and both clock-state return ratios."""

import math
import statistics

from bench import REF_ID, budget, deck, loop_metrics, transient_metrics
from mismatch import validate_run
from mismatch_bench import (
    DEVICES,
    METRICS,
    SLOW,
    draw,
    measured,
    offset,
    pair_deck,
    pair_response,
    require,
    sample_deck,
    wrap_native,
)
from mos_table import STATES, simulate


def job(folder, expected, ngspice):
    if ngspice:
        folder.mkdir(parents=True)
        simulate(ngspice, folder, expected)
    validate_run(folder, expected)


def midpoint_vectors():
    parts = ["v(xdrv.vx)", "v(out)", "v(xdrv.tail)"]
    for name in ("XIP", "XIM", "XLA", "XLB"):
        master = "msky130_fd_pr__" + DEVICES[name][1] + "fet_01v8"
        parts.extend(
            "@m.xdrv.{}.{}[{}]".format(name.lower(), master, key) for key in ("id", "gm", "vds", "vdsat")
        )
    return parts


def with_midpoint(text):
    require(text.count("\nop\n") == 1, "Missing unique DC operating point")
    vectors = " ".join(midpoint_vectors())
    return text.replace("\nop\n", " " + vectors + "\nop\nwrdata devices.dat " + vectors + "\n")


def midpoint(folder):
    lines = [
        s.lower().split()
        for s in (folder / "devices.dat").read_text(encoding="utf-8").splitlines()
        if s.strip()
    ]
    names = midpoint_vectors()
    require(
        len(lines) == 2 and lines[0][1:] == names and len(lines[1]) == len(names) + 1,
        "Missing native MOS operating points",
    )
    values = list(map(float, lines[1][1:]))
    require(all(math.isfinite(v) for v in values), "Nonfinite MOS operating point")
    result = {"mirror_gate_V": values[0], "output_V": values[1], "tail_V": values[2], "devices": {}}
    for i, name in enumerate(("XIP", "XIM", "XLA", "XLB")):
        current, gm, vds, vsat = values[3 + 4 * i : 7 + 4 * i]
        require(abs(current) > 1e-12 and gm > 0, "Unbiased device at input midpoint: " + name)
        result["devices"][name] = {
            "current_uA": abs(current) * 1e6,
            "gm_id_per_V": gm / abs(current),
            "saturation_margin_V": abs(vds) - abs(vsat),
        }
    return result


def check_row(row, spec):
    limit = budget(spec)["half_lsb_mV"]
    return (
        all(row[k] <= limit for k in METRICS)
        and min(row["track_pm_deg"], row["hold_pm_deg"]) >= spec["min_phase_margin_deg"]
    )


def nominal(folder, native, lib, spec, ngspice):
    corners = {}
    for state in STATES:
        corner, _, supply = state
        tracked = folder / corner / "track"
        held = folder / corner / "hold"
        nominal_track = (
            sample_deck(native, lib, spec, None) if state == SLOW else deck(native, lib, state, spec, True)
        )
        job(tracked, with_midpoint(nominal_track), ngspice)
        job(held, deck(native, lib, state, spec, False), ngspice)
        loop_on = loop_metrics(tracked, supply, True, spec)
        loop_off = loop_metrics(held, supply, False, spec)
        edges = transient_metrics(tracked, spec, supply)["edges"]
        row = {
            edge + "_" + phase + "_mV": edges[edge][phase + "_error_mV"]
            for edge in ("up", "down")
            for phase in ("track", "hold")
        }
        row.update(
            track_pm_deg=loop_on["phase_margin_deg"],
            hold_pm_deg=loop_off["phase_margin_deg"],
            track_quiescent_uA=loop_on["quiescent_uA"],
            hold_quiescent_uA=loop_off["quiescent_uA"],
            operating_point=midpoint(tracked),
        )
        if state == SLOW:
            row["input_offset_mV"] = offset(tracked / "balance.dat", spec)
        corners[corner] = row
    return corners


def sample(folder, native, lib, spec, vector, ngspice):
    track, hold = folder / "track", folder / "hold"
    job(track, sample_deck(native, lib, spec, vector), ngspice)
    held_deck = deck(wrap_native(native, vector), lib, SLOW, spec, False)
    job(hold, held_deck.replace(REF_ID, REF_ID.replace(".xnref.m", ".xnref.xdevice.m")), ngspice)
    data = measured(track, spec)
    for name, path, is_track in (("track", track, True), ("hold", hold, False)):
        data[name + "_pm_deg"] = loop_metrics(path, SLOW[2], is_track, spec)["phase_margin_deg"]
    return data


def calibration(folder, native, lib, spec, sizing, nominal_ss, ngspice):
    job(folder / "pair", pair_deck(lib, sizing), ngspice)
    pair = pair_response(folder / "pair/pair.dat", sizing)
    zero = sample(folder / "zero", native, lib, spec, draw(None), ngspice)
    require(
        all(abs(zero[k] - nominal_ss[k]) <= 0.001 for k in zero),
        "Zero local variation does not reproduce the native circuit within 1 uV / 0.001 deg",
    )
    return pair


def population(folder, native, lib, spec, seeds, ngspice):
    rows = {}
    for seed in seeds:
        rows[str(seed)] = sample(folder / ("seed-" + str(seed)), native, lib, spec, draw(seed), ngspice)
    again = sample(folder / "repeat", native, lib, spec, draw(seeds[0]), ngspice)
    require(
        all(abs(v - rows[str(seeds[0])][k]) < 1e-6 for k, v in again.items()),
        "Repeated local mismatch draw changed",
    )
    offsets = [r["input_offset_mV"] for r in rows.values()]
    require(max(offsets) - min(offsets) > 0.001, "Full circuit did not respond to the local mismatch draws")
    worst = max(((r[k], int(seed), k) for seed, r in rows.items() for k in METRICS))
    return dict(
        samples=len(rows),
        passed=sum(check_row(r, spec) for r in rows.values()),
        worst_error_mV=worst[0],
        worst_seed=worst[1],
        worst_metric=worst[2],
        offset_sd_mV=statistics.stdev(offsets),
        minimum_pm_deg=min(min(r["track_pm_deg"], r["hold_pm_deg"]) for r in rows.values()),
        rows=rows,
    )


def diagnose(folder, native, lib, spec, seed, ngspice):
    groups = {
        "input_pair": ("XIP", "XIM"),
        "active_load": ("XLA", "XLB"),
        "bias": ("XPREF", "XNREF", "XTAIL"),
        "switch": ("XSN", "XSP"),
    }
    actual = draw(seed)
    rows = {}
    for group, names in groups.items():
        isolated = draw(None)
        for name in names:
            isolated[name] = actual[name]
        rows[group] = sample(folder / group, native, lib, spec, isolated, ngspice)
    return {"seed": seed, "groups": rows}
