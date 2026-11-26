"""Infer gate bias from a single measured MOS curve and check the interpolation on withheld points."""
import argparse
import bisect
import csv
import json
import math
from pathlib import Path


def saturation_margin(vds, vdsat):
    if not all(math.isfinite(v) for v in (vds,vdsat)):
        raise ValueError("Finite signed drain and saturation voltages are required")
    return abs(vds)-abs(vdsat)


def margin_change(vds_before, vdsat_before, vds_after, vdsat_after):
    before = saturation_margin(vds_before,vdsat_before)
    after = saturation_margin(vds_after,vdsat_after)
    return dict(before_V=before,after_V=after,gain_V=after-before,
                drain_change_V=abs(vds_after)-abs(vds_before),
                required_change_V=abs(vdsat_after)-abs(vdsat_before))


def bias_at_current(gates, currents, transconductances, target):
    """Interpolate VGS magnitude against log(ID) inside the measured branch interval."""
    if (not math.isfinite(target) or target<=0 or len(gates)<2
            or len(gates)!=len(currents) or len(gates)!=len(transconductances)
            or not all(math.isfinite(g) and g>=0 for g in gates)
            or any(a>=b for a,b in zip(gates,gates[1:]))):
        raise ValueError("Invalid target current or gate-voltage axis")
    if not all(math.isfinite(i) and i>=0 for i in currents) or not all(math.isfinite(g) for g in transconductances):
        raise ValueError("Invalid device sweep values")
    # Leakage at the very start can be nonmonotonic. Use the complete connected increasing
    # inversion region ending at the highest gate bias, without extrapolating to other regions.
    first=len(currents)-1
    while first>0 and 0<currents[first-1]<currents[first] and transconductances[first-1]>0:
        first-=1
    if first==len(currents)-1 or transconductances[-1]<=0 or not currents[first]<=target<=currents[-1]:
        raise ValueError("Target current is outside the measured inversion branch")
    if any(currents[i] >= target and transconductances[i] > 0 for i in range(first)):
        raise ValueError("Target current is ambiguous across measured gate branches")
    hi=max(first+1,bisect.bisect_left(currents,target,lo=first))
    lo=hi-1
    fraction=math.log(target/currents[lo])/math.log(currents[hi]/currents[lo])
    gate=gates[lo]+fraction*(gates[hi]-gates[lo])
    efficiency=[transconductances[i]/currents[i] for i in (lo,hi)]
    return dict(vgs_V=gate,gm_id_per_V=efficiency[0]+fraction*(efficiency[1]-efficiency[0]),
                bracket_V=[gates[lo],gates[hi]],bracket_A=[currents[lo],currents[hi]])


AXES = ("length_um", "width_um", "vds_V", "vbs_V")
VALUES = ("abs_vgs_V", "abs_id_uA", "gm_uS")


def read_curves(path):
    """Read one corner and temperature per table, retaining signed drain and body bias."""
    curves = {}
    conditions = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = csv.DictReader(stream)
        columns = rows.fieldnames or []
        if len(columns) != len(set(columns)) or not {"polarity", *AXES, *VALUES}.issubset(columns):
            raise ValueError("Expected named MOS sweep columns with V, um, uA and uS units")
        for row in rows:
            if None in row or any(row[k] is None for k in ("polarity", *AXES, *VALUES)):
                raise ValueError("Incomplete MOS sweep row")
            conditions.add(tuple(row.get(k) for k in ("process", "corner", "temperature_C")))
            polarity = row["polarity"].upper()
            axes = tuple(float(row[k]) for k in AXES)
            gate, current, gm = (float(row[k]) for k in VALUES)
            if (polarity not in ("N", "P") or not all(math.isfinite(v) for v in (*axes, gate, current, gm))
                    or min(axes[:2]) <= 0 or gate < 0 or current < 0):
                raise ValueError("Invalid MOS geometry or sweep data")
            curves.setdefault((polarity, *axes), []).append((gate, current * 1e-6, gm * 1e-6))
    if not curves or len(conditions) != 1:
        raise ValueError("Supply one nonempty process, corner and temperature table at a time")
    return curves


def lookup_curve(curves, key, current_uA):
    if key not in curves:
        raise ValueError("No measured curve at this polarity, W/L, signed VDS and VBS")
    gates, currents, gm = zip(*curves[key])
    return bias_at_current(gates, currents, gm, current_uA * 1e-6)


def crosscheck(curves):
    """Use even-indexed samples to predict interior odd-indexed points with gm/ID 5 to 25 per volt."""
    if not curves:
        raise ValueError("Expected at least one measured MOS curve")
    worst_gate = worst_efficiency = 0
    checked = 0
    for key, rows in curves.items():
        if len(rows) < 3 or any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
            raise ValueError("Expected one ordered gate sweep with at least three samples")
        train = tuple(zip(*rows[::2]))
        count = 0
        for gate, current, gm in rows[1:-1:2]:
            if current <= 0 or not 5 <= gm / current <= 25:
                continue
            estimate = bias_at_current(*train, current)
            if not estimate["bracket_V"][0] < gate < estimate["bracket_V"][1]:
                raise ValueError("Held-out current selected a different gate interval")
            worst_gate = max(worst_gate, abs(estimate["vgs_V"] - gate))
            worst_efficiency = max(worst_efficiency, abs(estimate["gm_id_per_V"] / (gm / current) - 1))
            count += 1
        if not count:
            raise ValueError("No in-range held-out points on curve " + str(key))
        checked += count
    return dict(curves=len(curves), withheld_points=checked, max_gate_error_mV=1000 * worst_gate,
                max_relative_gm_id_error_percent=100 * worst_efficiency)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    lookup = actions.add_parser("lookup", help="Estimate |VGS| and gm/ID at the specified drain current")
    lookup.add_argument("table", type=Path)
    lookup.add_argument("--polarity", choices=("N", "P"), required=True)
    for key in ("length-um", "width-um", "vds", "vbs", "current-uA"):
        lookup.add_argument("--" + key, type=float, required=True)
    check = actions.add_parser("check", help="Withhold alternating gate samples to measure interpolation error")
    check.add_argument("table", type=Path)
    axes = actions.add_parser("axes", help="List the measured geometries and signed terminal biases available for lookup")
    axes.add_argument("table", type=Path)
    args = parser.parse_args()
    try:
        curves = read_curves(args.table)
        if args.action == "lookup":
            key = (args.polarity, args.length_um, args.width_um, args.vds, args.vbs)
            result = lookup_curve(curves, key, args.current_uA)
        elif args.action == "check":
            result = crosscheck(curves)
        else:
            result = [dict(zip(("polarity", *AXES), key), points=len(curves[key])) for key in sorted(curves)]
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
