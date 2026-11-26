"""Make the same editable driver with different input-pair areas and PMOS mirror ratios."""

import copy
import json
import math
import tempfile
from pathlib import Path

from driver import OTA, audit, build, materialize, parent_pins, size
from mismatch_bench import require
from mos_table import save_json


def family(rows, spec, repair=False):
    base = size(rows, spec, True)
    n = base["devices"]["XIP"][2]
    load = base["devices"]["XLA"][2]
    result = {} if repair else {"original": base}
    # Keep the PMOS unit width identical on the two sides of each mirror.
    coarse = (
        ("equal-units", 1, n, 1, 3 * load, 0),
        ("ratio", 1, n, 1, 3 * load, 24),
        ("pair-2", 2, 2 * n, 1, 3 * load, 24),
        ("pair-3", 2, 3 * n, 1, 3 * load, 24),
        ("pair-4", 2, 4 * n, 1, 3 * load, 24),
        ("long-load", 2, 3 * n, 1.5, 4 * load, 32),
    )
    if repair:
        fine = max(2, round(10 * load / 1.6))
        candidates = (
            ("fine-ratio", 2, 3 * n, 1.5, fine, 25, 1.6),
            ("matched-fine", 2, 4 * n, 1.5, fine, 25, 1.6),
            ("matched-long", 2, 4 * n, 2, 5 * load, 20, 2.5),
            ("large-long", 2, 5 * n, 2, 5 * load, 20, 2.5),
        )
    else:
        candidates = [r + (2.5,) for r in coarse]
    for name, length, count, load_length, reference_units, ratio_step, unit_width in candidates:
        sizing = copy.deepcopy(base)
        extra = max(1, round(reference_units / ratio_step)) if ratio_step else 0
        for device in ("XIP", "XIM"):
            sizing["devices"][device] = (6, length, count)
        sizing["devices"]["XLA"] = (unit_width, load_length, reference_units)
        sizing["devices"]["XLB"] = (unit_width, load_length, reference_units + extra)
        # At the budgeted branch current, record the measured device rows nearest each
        # new current density. The actual bias and gm/ID are measured again in the complete driver.
        for device, polarity, body, key in (
            ("XIP", "N", 0.15, "input_lookup"),
            ("XLA", "P", 0, "load_lookup"),
        ):
            w, l, m = sizing["devices"][device]
            density = sizing["branch_target_uA"] / (w * m)
            rows_here = [
                r
                for r in rows
                if r["polarity"] == polarity
                and r["length_um"] == l
                and abs(r["vds_V"]) == 0.65
                and abs(r["vbs_V"]) == body
                and r["gm_id_per_V"] is not None
            ]
            require(bool(rows_here), "No characterized rows at the coupled device geometry")
            sizing[key] = min(rows_here, key=lambda r: abs(r["id_per_width_uA_um"] - density))
        sizing["core_gate_area_um2"] = sum(
            w * l * m for k, (w, l, m) in sizing["devices"].items() if k in OTA
        )
        result[name] = sizing
    return result


def check_sizing(saved, sizing):
    expected = json.loads(json.dumps(sizing))
    # Python 3.12 changed float summation. A different rounding of the area total
    # leaves the individual native W, L, M and the underlying MOS table rows identical.
    require(saved.keys() == expected.keys(), "Missing or unexpected sizing fields")
    for key, value in expected.items():
        agrees = (
            math.isclose(saved[key], value, rel_tol=1e-13, abs_tol=1e-12)
            if key in ("core_gate_area_um2", "switch_gate_area_um2")
            else saved[key] == value
        )
        require(agrees, "Native sizes differ from characterized family: " + key)


def native_views(folder, sizing, spec, xschem):
    views = folder / "views"
    if xschem:
        folder.mkdir()
        save_json(folder / "sizing.json", sizing)
        _, back = build(views, xschem, sizing, spec)
        save_json(folder / "readback.json", back)
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    check_sizing(read(folder / "sizing.json"), sizing)
    raw = (views / "TB_ADRV_SAMPLE.spice").read_text(encoding="utf-8")
    sections = audit(raw, sizing, spec)
    back = read(folder / "readback.json")
    require(
        sections == back["native_sections"]
        and json.loads(json.dumps(parent_pins(views))) == back["parent_pins"],
        "Native hierarchy or parent pins differ",
    )
    with tempfile.TemporaryDirectory() as tmp:
        check = Path(tmp)
        materialize(check, sizing, spec)
        for f in check.iterdir():
            require(
                f.read_text(encoding="utf-8") == (views / f.name).read_text(encoding="utf-8"),
                "Editable schematic or symbol differs: " + f.name,
            )
    fixture = {k: sections[k] for k in ("TOP", "ADRV_SAMPLER")}
    native = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("**")) + "\n"
    return native, fixture


def pick(training, limit_mV, reserve_percent):
    """Freeze the training choice before reading the independent seed block."""

    def area(name):
        return training[name]["area_um2"], name

    eligible = [
        name
        for name, record in training.items()
        if record["pvt_pass"] and record["passed"] == record["samples"]
    ]
    reserved = [
        name
        for name in eligible
        if training[name]["worst_error_mV"] <= limit_mV * (1 - reserve_percent / 100)
    ]
    if reserved:
        return min(reserved, key=area), "reserve_met"
    if eligible:
        return min(eligible, key=area), "sample_limits_met"
    return min(
        training,
        key=lambda n: (
            not training[n]["pvt_pass"],
            -training[n]["passed"],
            training[n]["worst_error_mV"],
            area(n),
        ),
    ), "diagnostic_only"
