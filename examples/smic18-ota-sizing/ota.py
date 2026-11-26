"""Build and qualify a process-sized 1.8 V differential OTA from a fresh or verified local device table."""
import argparse
import runpy
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent/n) for n in ("smic18-bias-mirror","dynamic-comparator","sky130-current-mirror")]
from bias import config_for_site
from spectre_run import read_json, save_json, sha256
import ota_ade
from ota_measure import LIMITS, measure
from ota_native import audit, build
from ota_views import dimensions


def candidates():
    for length in (1.0,1.5):
        for drive in (0.6,1.0):
            for plate in (16.0,27.5):
                yield "l{:03d}-i{:02d}-c{:03d}".format(round(length*100),round(drive*10),round(plate*10)), (length,drive,plate)


def first_passing(results):
    passing = [(max(c["power_uW"] for c in item["corners"].values()), sum(item["physical"]["area"].values()), name)
               for name,item in results.items() if all(c["passed"] for c in item["corners"].values())]
    return min(passing)[2] if passing else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run","verify"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--devices", type=Path, help="Reuse a complete characterize.py output, independently verified before sizing")
    args = parser.parse_args()
    fresh = args.action=="run"
    root = args.out.expanduser().resolve()
    site = config_for_site(args.config.expanduser().resolve())
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    device = runpy.run_path(str(HERE/"characterize.py"))
    if fresh:
        root.mkdir(parents=True, exist_ok=False)
        if args.devices is None:
            (root/"devices").mkdir()
    source = args.devices.expanduser().resolve() if args.devices else root/"devices"
    create_devices = fresh and args.devices is None
    table = device["evaluate"](source,site,create_devices)
    if create_devices:
        save_json(source/"summary.json",table)
    elif read_json(source/"summary.json") != table:
        raise ValueError("The supplied device table differs from its raw native sweeps")
    results = {}
    for name,(length,drive,plate) in candidates():
        folder = root/name
        sizes = dimensions(table,length,drive,plate)
        if fresh:
            folder.mkdir()
            build(folder/"oa",site,sizes)
        native, physical = audit(folder/"oa",site,sizes)
        corners = {}
        for state in device["STATES"]:
            corners[state[0]] = measure(folder/state[0],site,state,native,fresh)
        results[name] = dict(sizes=sizes, physical=physical, corners=corners)
        print(name, [(k,round(v["gain_dB"],2),round(v["power_uW"],2),v["passed"]) for k,v in corners.items()],flush=True)
    winner = first_passing(results)
    report = dict(limits=LIMITS, pdk_library=site["pdk_library"], pdk_revision=site["pdk_revision"],
                  model_sha256=site["model_sha256"], device_table_sha256=sha256(source/"summary.json"),
                  output_load_pF_per_side=2, selected=winner, candidates=results)
    if winner:
        stage = root/winner/"oa"
        if fresh:
            ota_ade.launch(stage,site,device["STATES"])
        report["maestro"] = ota_ade.review(stage,site,device["STATES"],results[winner]["corners"])
    if fresh:
        save_json(root/"summary.json",report)
        if winner:
            script = '#!/bin/sh\nset -eu\ncd {}\nexec {} -restore open.il\n'.format(
                shlex.quote(str(stage)),shlex.quote(str(site["virtuoso"])))
            (root/"open.sh").write_text(script,encoding="utf-8")
            (root/"open.sh").chmod(0o755)
    elif read_json(root/"summary.json")!=report:
        raise ValueError("Published selection or metrics differ from native results")
    print("Lowest worst-corner supply power among the passing candidates:",winner)
    return 0 if winner else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
