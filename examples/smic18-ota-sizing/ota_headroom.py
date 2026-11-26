"""Budget a CM detector sizing change at measured terminal biases and replay held-die DC results."""
import argparse
from collections import Counter
from pathlib import Path
import re
import runpy
import sys

from ota import config_for_site
from ota_cmfb import ICM, persist
from ota_measure import samples
from ota_native import hierarchy
from ota_mismatch import COUNT, SEEDS, coverage
from ota_recovery import BIAS_WIDTHS, DESIGNS, SENSE_SIZES
from ota_robust import load_design
from ota_trim import BANKS, physical_bank
from ota_trim_stats import bank_run, score
from ota_views import CMFB, TB
from bias_sim import STATES
from spectre_run import read_json, sha256
from measure_psf import scalar_sweep

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"skills/paper-to-virtuoso/scripts"))
from device_headroom import bias_at_current, margin_change, saturation_margin
from trim_calibration import held_measurements


TAILS = ("XDUT.XC.MCTP","XDUT.XC.MCTN")
REFERENCE = ("XDUT.XC.MCREFP","XDUT.XC.MCREFN")


def reference_point(folder, report, state, polarity):
    """Read the tail and its reference gate at the actual limiting end of the swing."""
    key=state[0]+"-950"
    code=report["fixed_code"]
    source=folder/(key+"-code"+str(code))
    job=read_json(source/"job.json")
    raw=source/"psf"/job["trace"]
    if job["warnings"] or sha256(raw)!=report["curves"][key][str(code)]["raw_sha256"] or sha256(raw)!=job["raw_sha256"]:
        raise ValueError("The baseline terminal biases must come from the verified code sweep")
    fields=("vgs","vds","vdsat","gm","id")
    measured=samples(raw,"CMCOMMAND",("VCM","OUTP","OUTN")+tuple(n+":"+k for n in TAILS+REFERENCE for k in fields))
    pos=0 if polarity<0 else 2
    if len(measured["CMCOMMAND"])!=3 or abs(measured["CMCOMMAND"][pos]-.30*polarity)>1e-7:
        raise ValueError("The measured output command differs from the requested endpoint")
    tail=min(TAILS,key=lambda n:saturation_margin(measured[n+":vds"][pos],measured[n+":vdsat"][pos]))
    device="XDUT.XC.MCREF"+tail[-1]
    source_v=measured[tail+":vds"][pos]
    gate=measured["VCM"][pos]
    if abs(measured[device+":vgs"][pos]+source_v-gate)>2e-6:
        raise ValueError("Reference gate and tail drain do not establish the same detector source voltage")
    return dict(device=device.split(".")[-1],gate_V=gate,tail_V=source_v,vds_V=measured[device+":vds"][pos],
                vbs_V=-source_v,current_A=abs(measured[device+":id"][pos]),vgs_V=measured[device+":vgs"][pos],
                gm_id_per_V=measured[device+":gm"][pos]/abs(measured[device+":id"][pos]),raw_sha256=sha256(raw))


def replica_curve(folder,net,site,state,point,width,length,fresh):
    name=point["device"]
    side=name[-1]
    if hierarchy(net)[0][CMFB][1][name][:2]!=(["RCG","VCM","TC"+side,"VSS"],"n18"):
        raise ValueError("Unexpected native reference device terminals or model")
    matches=re.findall(r"(?m)^\s*"+name+r"\s+\(RCG VCM TC"+side+r" VSS\)\s+(n18 [^\n]+)$",net)
    if len(matches)!=1:
        raise ValueError("Cannot isolate the complete SI-exported reference device card")
    sweep=runpy.run_path(str(Path(__file__).with_name("characterize.py")))["sweep"]
    key=(length,"N")
    _,count=sweep(folder,state,site,{key:matches[0]},{key:(point["vds_V"],point["vbs_V"])},fresh,
                  width_um=width,target_current_uA=1e6*point["current_A"])
    raw=folder/"psf/transfer.dc"
    values=scalar_sweep(raw.read_text(encoding="utf8"),"VGMAG",["VN0:p","MN0:gm"])
    currents=[abs(v) for v in values["VN0:p"]]
    result=bias_at_current(values["VGMAG"],currents,values["MN0:gm"],point["current_A"])
    return dict(lookup=result,points=count,raw_sha256=sha256(raw))


def budget(args,site,design,fresh):
    nominal=args.reference/"r130-nominal"
    baseline=read_json(nominal/"summary.json")
    if baseline.get("recovery")!=DESIGNS["r130"] or baseline["bank"]!=BANKS["centered"]:
        raise ValueError("Expected the native 130 kohm CMFB baseline")
    _,sizes,area=load_design(args.ota,args.cmfb,site)
    root=args.out/(args.design+"-budget")
    if fresh:root.mkdir(parents=True)
    if args.design=="r130":
        net,physical=physical_bank(nominal/"fixed-code-oa",site,sizes,area,False,baseline["bank"],baseline["fixed_code"],design)
    else:
        net,physical=physical_bank(root/"oa",site,sizes,area,fresh,baseline["bank"],baseline["fixed_code"],design)
    width=design.get("cm_input",sizes["cm_input"])
    length=design.get("cm_length_um",sizes["cm_length_um"])
    output={}
    for state in STATES:
        for sign in (-1,1):
            label=state[0]+("-neg" if sign<0 else "-pos")
            point=reference_point(nominal,baseline,state,sign)
            transfer=replica_curve(root/label,net,site,state,point,width,length,fresh)
            predicted=point["gate_V"]-transfer["lookup"]["vgs_V"]
            if args.design=="r130" and abs(predicted-point["tail_V"])>.00025:
                raise ValueError("The baseline inverse device lookup misses its measured source voltage")
            output[label]=dict(reference=point,transfer=transfer,predicted_tail_V=predicted,
                          predicted_lift_mV=1000*(predicted-point["tail_V"]))
    result=dict(geometry_um=[width,length],area=physical["physical"]["area"],endpoints=output,
                baseline_sha256=sha256(nominal/"summary.json"))
    persist(root/"summary.json",result,fresh)
    return result


def nominal_prediction(args,root):
    prediction=read_json(root/"summary.json")
    screen=(args.reference if args.design=="r130" else args.out)/(args.design+"-screen")
    record=read_json(screen/"summary.json")
    if record["design"]!=DESIGNS[args.design] or record["area"]["physical"]["area"]!=prediction["area"]:
        raise ValueError("The component lookup and complete circuit do not have the same design")
    actual={}
    for state in STATES:
        label=state[0]+"-950"
        job=read_json(screen/(label+"-dc/job.json"))
        raw=screen/(label+"-dc/psf")/job["trace"]
        if job["warnings"] or sha256(raw)!=job["raw_sha256"]:
            raise ValueError("Changed circuit operating point")
        values=samples(raw,"CMCOMMAND",tuple(n+":vds" for n in TAILS))
        for pos,direction in ((0,"neg"),(2,"pos")):
            key=state[0]+"-"+direction
            trial=prediction["endpoints"][key]
            ref=trial["reference"]
            tail=TAILS[0] if ref["device"]=="MCREFP" else TAILS[1]
            if len(values["CMCOMMAND"])!=3 or abs(values["CMCOMMAND"][pos]-(-.3 if pos==0 else .3))>1e-7:
                raise ValueError("Mismatch between the device sweep and the circuit command")
            source=values[tail+":vds"][pos]
            actual[key]=dict(measured_lift_mV=1000*(source-ref["tail_V"]),predicted_lift_mV=trial["predicted_lift_mV"],
                             prediction_error_mV=1000*(trial["predicted_tail_V"]-source),raw_sha256=sha256(raw))
    return actual


def paired_stress(args,site,design,fresh):
    stats=args.reference/"r130-statistics"
    nominal=args.reference/"r130-nominal"
    original=read_json(stats/"summary.json")
    freeze=read_json(stats/"frozen.json")
    frozen=freeze["calibration_codes"]
    if (original["calibration_sha256"]!=sha256(stats/"frozen.json") or original["nominal_sha256"]!=sha256(nominal/"summary.json")
            or freeze["seed"]!=SEEDS[0] or freeze["reference"]!="tt-950-zero" or len(frozen)!=COUNT):
        raise ValueError("The fixed per-die control words must come from the verified original OTA")
    result_root=args.out/(args.design+"-stress")
    if fresh:result_root.mkdir(parents=True,exist_ok=True)
    source_root=args.out/(args.design+"-budget")
    prediction=nominal_prediction(args,source_root)
    _,sizes,area=load_design(args.ota,args.cmfb,site)
    baseline=args.design=="r130"
    stat_folder=stats/"statistical-oa" if baseline else result_root/"statistical-oa"
    state_sizes=dict(sizes,mos_family="statistical")
    bank=BANKS["centered"]
    native,audited=physical_bank(stat_folder,site,state_sizes,area,fresh and not stat_folder.exists(),bank,recovery=design)
    if audited["physical"]["area"]!=read_json(source_root/"summary.json")["area"]:
        raise ValueError("Ordinary and statistical transistor dimensions or drawn area differ")
    prior,_=physical_bank(stats/"statistical-oa",site,state_sizes,area,False,bank,recovery=DESIGNS["r130"])
    code0=original["fixed_code"]
    center=next(s for s in STATES if s[0]=="tt")
    control=bank_run(stats/("tt-950-code"+str(code0)),prior,center,ICM[0],site,code0,False)
    if set(control["ids"])!=set(frozen):
        raise ValueError("The archived reference codes and sampled vectors differ")
    selected, tails, jobs = {},{},{}
    fields=tuple(n+":"+k for n in TAILS for k in ("vds","vdsat"))
    for state in STATES:
        for icm in ICM:
            label="{}-{}".format(state[0],round(1000*icm))
            runs,measurements={},{}
            for code in sorted(set(frozen.values())):
                folder=(stats if baseline else result_root)/(label+"-code"+str(code))
                run=bank_run(folder,native,state,icm,site,code,fresh and not baseline and not folder.exists())
                coverage({"reference":control,"candidate":run})
                if baseline and run["job_sha256"]!=original["jobs"][label][str(code)]:
                    raise ValueError("The baseline held-code job differs from the reference calibration")
                runs[code]=run
                for i,draw in enumerate(run["ids"],1):
                    if frozen[draw]!=code:continue
                    raw=folder/("psf/mc-{:03d}_cm_sweep.dc".format(i))
                    data=samples(raw,"CMCOMMAND",fields)
                    if len(data["CMCOMMAND"])!=3 or any(abs(x-y)>1e-7 for x,y in zip(data["CMCOMMAND"],(-.3,0,.3))):
                        raise ValueError("Missing the actual negative, zero or positive differential command")
                    measurements[draw]=[[data[n+":vds"][j],data[n+":vdsat"][j]] for j in range(3) for n in TAILS]
            observed={k:dict(zip(r["ids"],r["samples"])) for k,r in runs.items()}
            selected[label]=held_measurements(frozen,observed)
            if set(measurements)!=set(frozen):
                raise ValueError("Missing measured tail voltages for one or more physical draws")
            tails[label]=measurements
            jobs[label]={str(c):r["job_sha256"] for c,r in runs.items()}
    metrics=score(selected)
    if baseline and metrics!=original["calibrated"]:
        raise ValueError("Replaying the baseline does not reproduce the original per-die verdicts")
    raw_table=result_root/"tails.json"
    persist(raw_table,tails,fresh and not raw_table.exists())
    reference_path=args.out/"r130-stress/tails.json"
    if baseline:
        before=tails
    else:
        ref_summary=read_json(args.out/"r130-stress/summary.json")
        if (sha256(reference_path)!=ref_summary["tails_sha256"] or ref_summary["frozen_sha256"]!=sha256(stats/"frozen.json")):
            raise ValueError("The headroom reference is not bound to the original trim codes")
        before=read_json(reference_path)
    worst=None
    failure_counts=Counter()
    recovered=[]
    for label,draws in tails.items():
        for draw,rows in draws.items():
            for i,row in enumerate(rows):
                change=margin_change(*before[label][draw][i],*row)
                if change["before_V"]<0:
                    recovered.append(change)
                if change["after_V"]<0:
                    failure_counts[label]=failure_counts.get(label,0)+1
                if worst is None or change["after_V"]<worst["after_V"]:
                    worst=dict(change,condition=label,device=TAILS[i%2].split(".")[-1],command_V=(-.3,0,.3)[i//2],
                               held_code=frozen[draw],draw=draw)
    passed_before=original["calibrated"]["draws"]
    pairs=Counter((not passed_before[d]["failed"],not v["failed"]) for d,v in metrics["draws"].items())
    if not recovered:
        raise ValueError("The original failure did not occur on the replayed development samples")
    report=dict(design=design,area=audited["physical"]["area"],frozen_sha256=sha256(stats/"frozen.json"),jobs=jobs,
                descriptor_sha256=control["descriptor_sha256"],lookup_sha256=sha256(source_root/"summary.json"),
                nominal_prediction=prediction,tails_sha256=sha256(raw_table),score=metrics,worst=worst,
                compared_tail_points=2*3*len(ICM)*len(STATES)*len(frozen),negative_tail_points=dict(failure_counts),
                old_negative_points=len(recovered),old_points_recovered=sum(v["after_V"]>=0 for v in recovered),
                min_gain_on_old_failures_mV=1000*min(x["gain_V"] for x in recovered),
                paired_joint=dict(both_pass=pairs[True,True],gained=pairs[False,True],lost=pairs[True,False],both_fail=pairs[False,False]))
    path=result_root/"summary.json"
    persist(path,report,fresh and not path.exists())
    return report


def code_range(args,site,design,fresh):
    """Bound what one static trim word could achieve using all conditions for diagnosis."""
    parent = args.reference if args.design == "r130" else args.out
    stats = parent / (args.design + "-statistics")
    nominal = parent / (args.design + "-nominal/summary.json")
    result = read_json(stats / "summary.json")
    freeze = read_json(stats / "frozen.json")
    frozen = freeze["calibration_codes"]
    if (result["calibration_sha256"] != sha256(stats / "frozen.json") or result["nominal_sha256"] != sha256(nominal)
            or freeze["seed"] != SEEDS[0] or freeze["reference"] != "tt-950-zero"
            or read_json(nominal).get("recovery") != design):
        raise ValueError("Expected the candidate's native four-bit calibration and sampled circuit")
    root = args.out / (args.design + "-codes")
    if fresh:
        root.mkdir(parents=True, exist_ok=True)
    _,sizes,area = load_design(args.ota,args.cmfb,site)
    net,_ = physical_bank(stats/"statistical-oa",site,dict(sizes,mos_family="statistical"),area,False,
                          BANKS["centered"],recovery=design)
    control = None
    results, jobs = {}, {}
    for code in range(16):
        cases = {}
        jobs[code] = {}
        for state in STATES:
            for icm in ICM:
                label = "{}-{}".format(state[0],round(1000*icm))
                source_hash = (freeze["jobs"] if label == "tt-950" else result["jobs"][label]).get(str(code))
                folder = (stats if source_hash else root) / (label + "-code" + str(code))
                run = bank_run(folder,net,state,icm,site,code,fresh and not source_hash and not folder.exists())
                if source_hash and run["job_sha256"] != source_hash:
                    raise ValueError("Changed native job used for the candidate's calibration")
                if control is None:
                    control = run
                coverage({"reference":control,"candidate":run})
                cases[label] = dict(zip(run["ids"],run["samples"]))
                jobs[code][label] = run["job_sha256"]
        results[code] = score(cases)["draws"]
    if any(results[code][draw] != result["calibrated"]["draws"][draw] for draw,code in frozen.items()):
        raise ValueError("Full code sweep does not reproduce the physical one-point calibration")
    draws = {}
    for draw, held in frozen.items():
        choices = {c:results[c][draw] for c in results}
        draws[draw] = dict(held=held, passing=[c for c,r in choices.items() if not r["failed"]],
            tracking=[c for c,r in choices.items() if "tracking" not in r["failed"]],
            common=[c for c,r in choices.items() if "common" not in r["failed"]],
            best_tracking_mV=min(r["max_tracking_mV"] for r in choices.values()),
            best_common_mV=min(r["max_common_mV"] for r in choices.values()))
    report = dict(frozen_sha256=sha256(stats/"frozen.json"),statistics_sha256=sha256(stats/"summary.json"),
                  conditions_per_code=18, codes=16, jobs={str(c):v for c,v in jobs.items()}, draws=draws,
                  held_passed=result["calibrated"]["passed"],
                  attainable={gate:sum(bool(r[gate]) for r in draws.values()) for gate in ("passing","tracking","common")})
    target = root / "summary.json"
    persist(target,report,fresh and not target.exists())
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("run","verify"))
    p.add_argument("--stage",choices=("budget","stress","codes"),required=True)
    p.add_argument("--design",choices=["r130",*SENSE_SIZES,*BIAS_WIDTHS],required=True)
    for key in ("config","ota","cmfb","reference","out"):
        p.add_argument("--"+key,type=Path,required=True)
    args=p.parse_args()
    site=config_for_site(args.config)
    site["model_sha256"]=sha256(site["model_file"])
    site["model_file"]=str(site["model_file"])
    if args.stage=="budget":
        report=budget(args,site,DESIGNS[args.design],args.action=="run")
        print(args.design,report["geometry_um"],report["area"]["mos_gate_um2"],
              {k:round(v["predicted_lift_mV"],3) for k,v in report["endpoints"].items()})
    elif args.stage=="stress":
        report=paired_stress(args,site,DESIGNS[args.design],args.action=="run")
        print(args.design,report["score"]["passed_per_gate"],"joint",report["score"]["passed"],
              "min tail margin mV",1000*report["worst"]["after_V"],"recovered",report["old_points_recovered"])
    else:
        report=code_range(args,site,DESIGNS[args.design],args.action=="run")
        print(args.design,"factory-calibrated",report["held_passed"],"static-code upper bounds",report["attainable"])


if __name__=="__main__":
    main()
