"""Separate the CM detector, loads and tails on the same previously measured local MOS draws."""
import argparse
from pathlib import Path

from ota import config_for_site
from ota_cmfb import persist
from ota_matching import PAIR, nominal_probe, variant
from ota_mismatch import CM_GROUPS, coverage, record
from ota_native import audit
from ota_robust import load_design
from bias_sim import STATES
from spectre_run import read_json, sha256


def arguments(description,with_nominal=False,banks=None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("action",choices=("run","verify"))
    for name in ("config","ota","cmfb","matching","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    if with_nominal:
        parser.add_argument("--nominal",type=Path,required=True)
    if banks:
        parser.add_argument("--bank",choices=banks,default=banks[0])
    args = parser.parse_args()
    site = config_for_site(args.config.resolve())
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    return args,site


def investigate(args,site,fresh):
    ordinary,sizes,physical = load_design(args.ota,args.cmfb,site)
    choice = read_json(args.matching/"sensitivity.json")["probes"]["load-high"]
    qualification = args.matching/"qualification"
    native,area = audit(qualification/"statistical-oa",site,
                        dict(sizes,mos_family="statistical",paired_geometry=choice["geometry"]))
    seed = read_json(qualification/"frozen.json")["validation_seed"]
    if area!=choice["physical"]:
        raise ValueError("Statistical experiment belongs to a different physical design")
    if fresh:
        args.out.mkdir(parents=True)
    controls = {}
    for state in STATES:
        if state[0]=="tt":
            continue
        icm = 1.10 if state[0]=="ss" else .95
        condition = "{}-{}".format(state[0],round(icm*1000))
        stem = "{}-{}-".format(condition,seed)
        all_on = record(qualification/("candidate-"+str(seed))/(stem+"all"),native,state,icm,site,"all",seed,False)
        runs = {"all":all_on}
        for arm in ("off","cmfb",*CM_GROUPS,"outside_cm"):
            runs[arm] = record(args.out/(stem+arm),native,state,icm,site,arm,seed,fresh)
        coverage(runs)  # Verify a common descriptor and actual parameter vectors.
        controls[condition] = {}
        for arm,run in runs.items():
            errors = [rows[-1]["cm_error_mV"] for rows in run["samples"]]
            if any(abs(row[-1]["command_V"]-.3)>1e-12 for row in run["samples"]):
                raise ValueError("Expected positive full-scale command")
            controls[condition][arm] = dict(passed_all_three_commands=run["passed_per_gate"]["common"],
                positive_fullscale_signed_mV=errors,job_sha256=run["job_sha256"])
        print(condition,{k:v["passed_all_three_commands"] for k,v in controls[condition].items()},flush=True)
    midpoints = {}
    for width in (5.15,5.20):
        folder = args.out/("width-{:03d}".format(round(width*100)))
        if fresh:
            folder.mkdir()
        net,actual = variant(folder/"oa",site,sizes,ordinary,physical,dict(PAIR,cm_loads=(width,1.5)),fresh)
        probe = nominal_probe(folder,site,net,fresh)
        midpoints[str(width)] = dict(probe,physical=actual)
        print("CM load",width,"static",probe["static_passed"],"dynamic",probe["dynamic_passed"],flush=True)
    result = dict(development_seed=seed,source_sha256=sha256(qualification/"frozen.json"),
                  common_mode_groups=controls,nominal_midpoints=midpoints)
    persist(args.out/"summary.json",result,fresh)
    return result


if __name__=="__main__":
    options,settings = arguments(__doc__)
    investigate(options,settings,options.action=="run")
