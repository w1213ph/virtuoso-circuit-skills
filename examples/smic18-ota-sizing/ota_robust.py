"""Stress the qualified OTA with a full-scale reversal and paired local MOS mismatch samples."""
import argparse
from pathlib import Path
import sys

from ota import config_for_site
import ota_ade
from ota_cmfb import ICM, checked_trace, parent, persist
from ota_measure import NODES, deck, measure, samples, settling
from ota_native import audit, build, hierarchy
from bias_sim import STATES
from spectre_run import read_json, sha256


def load_design(ota,cmfb,site):
    _,ref = parent(ota,site)
    first = read_json(cmfb/"summary.json")
    selected = read_json(cmfb/"midpoint/summary.json")
    if (not selected["passed"] or selected["name"]!="w045-l375"
            or selected["first_search_sha256"]!=sha256(cmfb/"summary.json")
            or first["source_ota_sha256"]!=sha256(ota/"summary.json")
            or first["diagnosis_sha256"]!=sha256(cmfb/"diagnosis.json")):
        raise ValueError("Expected the reviewed joint-range design and matching source evidence")
    d = selected["candidate"]
    sizes = dict(ref["sizes"],cm_input=d["width_um"],cm_length_um=d["length_um"])
    design = cmfb/"midpoint"/selected["name"]
    native,physical = audit(design/"oa",site,sizes)
    if physical!=d["physical"]:
        raise ValueError("The selected physical width, length or passives differ from the accepted OTA")
    for state in STATES:
        if measure(design/state[0],site,state,native,False)!=d["corners"][state[0]]["metrics"]:
            raise ValueError("The loaded full circuit differs from the original measurements at "+state[0])
    return native,sizes,physical


def check_fullscale_bench(ordinary,large):
    blocks,bench = hierarchy(ordinary)
    for source,field,old,new in (("EVIC","gain","0.6","ICMGAIN"),("VD","val0","-50m","-300m"),
                                 ("VD","val1","50m","300m"),("VOFF","val1","20m","0")):
        params = bench[source][2]
        if params[field]!=old:
            raise ValueError("Unexpected original testbench setting for "+source)
        params[field] = new
    if (blocks,bench)!=hierarchy(large):
        raise ValueError("The full-scale OA export differs beyond the four testbench settings")


def fullscale_source(native,site,state,icm):
    source = deck(site,state,native,"tran")
    marker = "\nsaveOptions options"
    if source.count(marker)!=1:
        raise ValueError("Cannot bind the native full-scale input common-mode setting")
    return source.replace(marker,"\nparameters ICMGAIN={:.15g}".format(icm/state[2])+marker)


def commanded(t):
    # The 2 us pulse width starts after the 5 ns rising transition.
    def ramp(start):
        return max(0.0,min(1.0,(t-start)/5e-9))
    return -.30+.60*(ramp(4e-6)-ramp(6.005e-6))


def within_fullscale_deadline(time_us):
    # Spectre's 2 ns strobe can print the exact 1 us sample a fraction of a femtosecond late.
    return time_us is not None and time_us<=1.0+1e-12


def fullscale_metrics(path,supply,icm):
    data = samples(path,"time",NODES)
    times = data["time"]
    diff = [p-n for p,n in zip(data["OUTP"],data["OUTN"])]
    common = [(p+n)/2 for p,n in zip(data["OUTP"],data["OUTN"])]
    for i,t in enumerate(times):
        vdd = supply*min(t/1e-6,1.0)
        ip,inn = data["INP"][i],data["INN"][i]
        if max(abs(data["VDD"][i]-vdd),abs(data["VCM"][i]-vdd/2),abs(data["CMD"][i]-commanded(t)),
               abs((ip+inn)/2-icm*vdd/supply),abs((ip-inn)-(data["CMD"][i]-diff[i])))>3e-5:
            raise ValueError("Supply ramp, full-scale command, input common mode or closed loop differs from the fixture")
    results = {}
    for direction,target,start,stop in (("rising",.30,4e-6,6.004e-6),("falling",-.30,6.005e-6,10e-6)):
        diff_time = settling(times,diff,target,.001,start,stop)
        cm_time = settling(times,common,supply/2,.025,start,stop)
        joint_time = max(diff_time,cm_time) if None not in (diff_time,cm_time) else None
        ids = [i for i,t in enumerate(times) if start-1e-15<=t<=stop+1e-15]
        last = ids[-1]
        results[direction] = dict(differential_settling_us=diff_time,common_settling_us=cm_time,
            joint_settling_us=joint_time,passed=within_fullscale_deadline(joint_time),
            worst_common_excursion_mV=1000*max(abs(common[i]-supply/2) for i in ids),
            final_tracking_error_mV=abs(diff[last]-target)*1000,final_common_error_mV=(common[last]-supply/2)*1000)
    startup = [i for i,t in enumerate(times) if 3.8e-6-1e-15<=t<4e-6-1e-15]
    initial_ok = all(abs(diff[i]+.30)<=.001 and abs(common[i]-supply/2)<=.025 for i in startup)
    return dict(startup_passed=initial_ok,directions=results,passed=initial_ok and all(r["passed"] for r in results.values()))


def dynamic(out,native,site,fresh):
    if fresh:
        out.mkdir()
    result = {}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}mV".format(state[0],round(icm*1000))
            raw = checked_trace(out/label,fullscale_source(native,site,state,icm),"tran.tran",site,fresh)
            result[label] = fullscale_metrics(raw,state[2],icm)
            result[label]["raw_sha256"] = sha256(raw)
    return result


def show_results(report):
    for label,run in report["fullscale"].items():
        measured = []
        for name in ("rising","falling"):
            value = run["directions"][name]["joint_settling_us"]
            measured.append("unsettled" if value is None else "{:.3f} us".format(value))
        print("{} {}  rise {}  fall {}".format(label,"PASS" if run["passed"] else "FAIL",*measured))
    for seed,batch in report.get("paired_batches",{}).items():
        print("Seed {}: {}/{} pass all 18 static conditions".format(seed,batch["passed"],batch["total"]))
        print("  Passing each requirement: "+", ".join("{} {}".format(k,v) for k,v in batch["passed_per_gate"].items()))
    for corner,group in report.get("diagnostic",{}).items():
        print("{} at 0.95 V: ".format(corner)+", ".join("{} {}/{}".format(arm,v["passed"],v["total"]) for arm,v in group["arms"].items()))


def fullscale_delivery(out,native,sizes,area,site,fresh):
    project = out/"fullscale-oa"
    bench_sizes = dict(sizes,fullscale_bench=True)
    if fresh:
        build(project,site,bench_sizes)
    large,large_area = audit(project,site,bench_sizes)
    if large_area!=area:
        raise ValueError("The full-scale testbench changed the OTA geometry")
    check_fullscale_bench(native,large)
    trans = dynamic(out/"fullscale",large,site,fresh)
    if fresh:
        ota_ade.launch(project,site,STATES,ICM)
    measured = {key:{k:v for k,v in row.items() if k!="raw_sha256"} for key,row in trans.items()}
    history = ota_ade.review(project,site,STATES,measured,ICM,fullscale_metrics)
    return trans,history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","verify","dynamic"))
    for option in ("config","ota","cmfb","out"):
        parser.add_argument("--"+option,type=Path,required=True)
    args = parser.parse_args()
    fresh = args.action!="verify"
    site = config_for_site(args.config.resolve())
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    native,sizes,area = load_design(args.ota.resolve(),args.cmfb.resolve(),site)
    out = args.out.resolve()
    if fresh:
        out.mkdir(parents=True,exist_ok=False)
    trans,history = fullscale_delivery(out,native,sizes,area,site,fresh)
    report = dict(source_sha256=sha256(args.cmfb.resolve()/"midpoint/summary.json"),fullscale=trans,
                  fullscale_passed=all(r["passed"] for r in trans.values()),maestro=history)
    if args.action=="dynamic":
        persist(out/"dynamic.json",report,True)
        show_results(report)
        return 0 if report["fullscale_passed"] else 1
    stat_sizes = dict(sizes,mos_family="statistical")
    if fresh:
        build(out/"oa",site,stat_sizes)
    statistical,stat_area = audit(out/"oa",site,stat_sizes)
    if stat_area!=area:
        raise ValueError("The nominal and statistical views differ in audited device or passive geometry")
    from ota_mismatch import experiment
    report.update(experiment(out,site,native,statistical,fresh))
    persist(out/"summary.json",report,fresh)
    show_results(report)
    return 0 if report["fullscale_passed"] and report["mismatch_status"]=="passed" else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
