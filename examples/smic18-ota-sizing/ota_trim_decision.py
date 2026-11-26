"""Combine verified trim studies and report which front-end targets still need work."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def decision(nominal,operating,statistics,nominal_hash):
    if (any(x["nominal_sha256"]!=nominal_hash for x in (operating,statistics)) or
            any(x["fixed_code"]!=nominal["fixed_code"] for x in (operating,statistics))):
        raise ValueError("Nominal, operating-point and statistical evidence must refer to the same coded DUT")
    corners = ("tt","ss","ff")
    grid = operating["grid"]
    dynamic = nominal["fullscale"]
    states = operating["operating"]
    expected_grid = {"{}-{:04d}".format(c,mv) for c in corners for mv in range(950,1101,15)}
    expected_states = {c+"-"+k for c in corners for k in ["nominal"]+[str(mv) for mv in range(950,1101,15)]}
    expected_dynamic = {c+"-"+mv+"mV" for c in corners for mv in ("950","1100")}
    if (set(states)!=expected_states or set(grid)!=expected_grid or any(len(v)!=21 for v in grid.values())
            or set(dynamic)!=expected_dynamic or any(set(v["directions"])!={"rising","falling"} for v in dynamic.values())):
        raise ValueError("Incomplete operating points, full-scale edges or joint grid")
    rows = [r for scan in grid.values() for r in scan]
    edges = [r for run in dynamic.values() for r in run["directions"].values()]
    samples = statistics["calibrated"]["draws"]
    if (operating["grid_total"]!=len(rows) or statistics["total"]!=32 or len(samples)!=32
            or sum(r["passed"] for r in rows)!=operating["grid_passed"]
            or sum(not r["failed"] for r in samples.values())!=statistics["calibrated"]["passed"]):
        raise ValueError("The saved totals disagree with the individual conditions or draws")
    dc = nominal["zero_input_open_loop"]  # DC, closed-loop startup and STB use normal feedback.
    checks = dict(gain=all(r["gain_dB"]>=80 for r in states.values()),
        input_noise=all(states[c+"-nominal"]["noise_uVrms"]<=40 for c in corners),
        phase_margins=all(dc[c]["metrics"]["checks"][loop+"_stability"] for c in corners for loop in ("dm","cm")),
        loaded_step_and_startup=all(dc[c]["metrics"]["checks"][k] for c in corners for k in ("startup","rise_us","fall_us","cm_step_us")),
        core_power=all(dc[c]["metrics"]["power_uW"]<=25.22 for c in corners),
        mos_gate_area=nominal["budget"]["physical"]["area"]["mos_gate_um2"]<=114.19,
        joint_grid=all(r["passed"] for r in rows),
        fullscale=all(r["passed"] for r in edges) and all(v["startup_passed"] for v in dynamic.values()),
        development_static=all(not r["failed"] for r in samples.values()))
    return dict(status="ready_for_reserved_test" if all(checks.values()) else "rework",checks=checks,
                static_passed=operating["grid_passed"],static_total=len(rows),edges_passed=sum(r["passed"] for r in edges),
                edges_total=len(edges),calibrated_passed=statistics["calibrated"]["passed"],development_total=statistics["total"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("nominal","operating","statistics"):
        parser.add_argument("--"+key,type=Path,required=True)
    args = parser.parse_args()
    try:
        raw = [(p/"summary.json").read_bytes() for p in (args.nominal,args.operating,args.statistics)]
        result = decision(*(json.loads(b) for b in raw),hashlib.sha256(raw[0]).hexdigest())
    except (OSError,ValueError,KeyError,TypeError) as error:
        print("Invalid evidence:",error,file=sys.stderr)
        return 2
    print(json.dumps(result,indent=2))
    return int(result["status"]=="rework")


if __name__=="__main__":
    sys.exit(main())
