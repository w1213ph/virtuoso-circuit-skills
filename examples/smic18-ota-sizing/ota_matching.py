"""Compare matched MOS geometry and CMFB bias on the same native OTA, load and random draws."""
import argparse
import math
from pathlib import Path
import statistics
import sys

from ota import config_for_site
from ota_cmfb import GATE_AREA_UM2, ICM, exact_swing, persist
from ota_mismatch import SEEDS, coverage, nominal_control, record
from ota_native import audit, build, hierarchy
from ota_range import replace_fixture_field
from ota_robust import check_fullscale_bench, dynamic, load_design
from ota_views import CMFB, INPUT
from audit_native import spice_number
from bias_sim import STATES
from spectre_run import read_json, sha256


PAIRS = {"input_pair":(INPUT,("MINP","MINN")), "cm_tails":(CMFB,("MCTP","MCTN")),
         "cm_loads":(CMFB,("MCM","MDMY"))}
PAIR = {"input_pair":(1.95,2.25)}
PROBES = (("pair",PAIR),
          ("tail-low",dict(PAIR,cm_tails=(1.25,1.50))),("tail-high",dict(PAIR,cm_tails=(1.45,1.50))),
          ("load-low",dict(PAIR,cm_loads=(4.85,1.50))),("load-high",dict(PAIR,cm_loads=(5.25,1.50))))


def scope_check(original,changed,pairs):
    before,bench = hierarchy(original)
    after,new_bench = hierarchy(changed)
    if bench!=new_bench or before.keys()!=after.keys() or not set(pairs)<=set(PAIRS):
        raise ValueError("The geometry experiment changed the fixture or its hierarchy")
    for group in pairs:
        cell,names = PAIRS[group]
        for name in names:
            nets,master,old = before[cell][1][name]
            actual,model,new = after[cell][1][name]
            dimensions = {"w","l","as","ad","ps","pd","nrd","nrs","sa","sb","sd","sca","scb","scc"}
            if (nets!=actual or master!=model or old.keys()!=new.keys() or
                    any(old[k]!=new[k] for k in old.keys()-dimensions)):
                raise ValueError("Unintended MOS model, wiring or non-geometry setting on "+name)
            if any(not math.isclose(spice_number(new[k])*1e6,target) for k,target in zip(("w","l"),pairs[group])):
                raise ValueError("Exported matched MOS geometry differs for "+name)
            before[cell][1][name] = after[cell][1][name]
    if before!=after:
        raise ValueError("A device outside the named matched pairs changed")


def variant(out,site,sizes,original,physical,pairs,fresh):
    candidate = dict(sizes,paired_geometry=pairs)
    if fresh:
        build(out,site,candidate)
    changed,actual = audit(out,site,candidate)
    scope_check(original,changed,pairs)
    baseline_widths = dict(input_pair=sizes["input"],cm_tails=sizes["reference"],cm_loads=sizes["load"])
    extra = sum(2*(w*l-baseline_widths[name]*sizes["length_um"]) for name,(w,l) in pairs.items())
    gate = actual["area"]["mos_gate_um2"]
    expected = dict(physical["area"],mos_gate_um2=gate)
    if (not math.isclose(gate,physical["area"]["mos_gate_um2"]+extra,abs_tol=1e-6)
            or actual["passives"]!=physical["passives"] or actual["area"]!=expected or gate>GATE_AREA_UM2):
        raise ValueError("Physical area or passive geometry exceeds the declared change")
    return changed,actual


def fullscale_copy(native):
    large = native
    for device,field,old,new in (("EVIC","gain","0.6","ICMGAIN"),("VD","val0","-50m","-300m"),
                                 ("VD","val1","50m","300m"),("VOFF","val1","20m","0")):
        large = replace_fixture_field(large,device,field,old,new)
    check_fullscale_bench(native,large)
    return large


def nominal_probe(folder,site,native,fresh):
    endpoints = {}
    for state in STATES:
        for icm in ICM:
            key = "{}-{}".format(state[0],round(1000*icm))
            endpoints[key] = exact_swing(folder/("end-"+key),native,state,site,fresh,icm)
    transients = dynamic(folder/"fullscale",fullscale_copy(native),site,fresh)
    rows = [r for values in endpoints.values() for r in values]
    return dict(endpoints=endpoints,fullscale=transients,
                static_passed=sum(r["passed"] for r in rows),dynamic_passed=sum(r["passed"] for r in transients.values()),
                worst_cm_mV=max(abs(r["cm_error_mV"]) for r in rows),
                min_headroom_mV=1000*min(r["headroom_V"] for r in rows))


def development(folder,site,ordinary,statistical,fresh):
    groups = {"all":{},"input_pair":{}}
    controls = {}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}".format(state[0],round(1000*icm))
            controls[label] = nominal_control(folder,site,state,icm,ordinary,statistical,fresh)
            for arm in groups:
                if arm=="input_pair" and icm!=ICM[0]:
                    continue
                path = folder/("{}-{}-{}".format(label,SEEDS[0],arm))
                groups[arm][label] = record(path,statistical,state,icm,site,arm,SEEDS[0],fresh)
    return controls,groups


def comparison(original,candidate):
    if original.keys()!=candidate.keys():
        raise ValueError("Unequal interface conditions in the paired comparison")
    coverage(dict(list(original.items())+[("new-"+k,v) for k,v in candidate.items()]))
    outcomes = {}
    for name,cases in (("before",original),("after",candidate)):
        joint = coverage(cases)
        errors = []
        for draw in range(joint["total"]):
            errors.append(max(abs(p["signed_diff_error_mV"]) for r in cases.values() for p in r["samples"][draw]))
        # Symmetric input, fixed TT interface and signed differential error for the mismatch distribution.
        center = [points[1] for points in cases["tt-950"]["samples"]]
        if any(abs(row["command_V"])>1e-10 for row in center):
            raise ValueError("Expected zero differential command for the offset measurement")
        signed = [r["signed_diff_error_mV"] for r in center]
        outcomes[name] = dict(joint=joint,worst_tracking_by_draw_mV=errors,
                              center_mean_mV=statistics.mean(signed),center_sample_sigma_mV=statistics.stdev(signed))
    a,b = (outcomes[k]["worst_tracking_by_draw_mV"] for k in ("before","after"))
    outcomes["improved_draws"] = sum(y<x for x,y in zip(a,b))
    return outcomes


def baseline_cases(root,site,statistical):
    prior = read_json(root/"summary.json")
    cases = {"all":{},"input_pair":{}}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}".format(state[0],round(1000*icm))
            for arm in cases:
                if arm=="input_pair" and icm!=ICM[0]:
                    continue
                path = root/"mismatch"/("{}-{}-{}".format(label,SEEDS[0],arm))
                case = record(path,statistical,state,icm,site,arm,SEEDS[0],False)
                if arm=="all" and case!=prior["cases"][str(SEEDS[0])][label]:
                    raise ValueError("Original full-circuit samples differ from the saved baseline")
                if icm==ICM[0] and coverage({label:case})!=prior["diagnostic"][state[0]]["arms"][arm]:
                    raise ValueError("Original group control differs from the saved baseline")
                cases[arm][label] = case
    return cases


def study(root,ota,cmfb,robust,site,fresh):
    original,sizes,physical = load_design(ota,cmfb,site)
    if read_json(robust/"summary.json")["source_sha256"]!=sha256(cmfb/"midpoint/summary.json"):
        raise ValueError("The original stress experiment belongs to a different OTA")
    stat_sizes = dict(sizes,mos_family="statistical")
    stat_reference,stat_area = audit(robust/"oa",site,stat_sizes)
    if stat_area!=physical:
        raise ValueError("The original statistical drawing has different physical dimensions")
    before = baseline_cases(robust,site,stat_reference)
    results = {}
    for name,pairs in PROBES:
        folder = root/name
        if fresh:
            folder.mkdir()
        native,area = variant(folder/"oa",site,sizes,original,physical,pairs,fresh)
        result = nominal_probe(folder,site,native,fresh)
        result.update(geometry={k:list(v) for k,v in pairs.items()},physical=area)
        if name=="pair":
            stat,_ = variant(folder/"statistical-oa",site,stat_sizes,stat_reference,physical,pairs,fresh)
            controls,cases = development(folder,site,native,stat,fresh)
            result.update(nominal_controls=controls,paired={k:comparison(before[k],v) for k,v in cases.items()})
        results[name] = result
        print(name,"static",result["static_passed"],"/18; dynamic",result["dynamic_passed"],"/6; FF rising",
              result["fullscale"]["ff-950mV"]["directions"]["rising"]["joint_settling_us"],flush=True)
        persist(folder/"screen.json",result,fresh)
    report = dict(source_sha256=sha256(robust/"summary.json"),probes=results)
    persist(root/"sensitivity.json",report,fresh)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","verify","screen","qualify"))
    for flag in ("config","ota","cmfb","robust","out"):
        parser.add_argument("--"+flag,type=Path,required=True)
    parser.add_argument("--validation-seed",type=int,
                        help="Seed reserved before a new design study. Verification uses the saved seed.")
    args = parser.parse_args()
    from ota_match_qualify import qualification,validation_seed
    root = args.out.resolve()
    new_screen = args.action in ("run","screen")
    frozen = read_json(root/"qualification/frozen.json")["validation_seed"] if args.action=="verify" else None
    reservation = root/"validation-seed.json"
    if not new_screen and reservation.exists():
        frozen = validation_seed(read_json(reservation)["validation_seed"],frozen)
    holdout = validation_seed(args.validation_seed,frozen)
    site = config_for_site(args.config.resolve())
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    if new_screen:
        root.mkdir(parents=True,exist_ok=False)
        persist(reservation,{"validation_seed":holdout},True)
    ota,cmfb,robust = (p.resolve() for p in (args.ota,args.cmfb,args.robust))
    search = study(root,ota,cmfb,robust,site,new_screen)
    if args.action=="screen":
        return 0
    result = qualification(root,ota,cmfb,robust,site,search,[name for name,_ in PROBES],args.action!="verify",holdout)
    return 0 if result["passed"] else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
