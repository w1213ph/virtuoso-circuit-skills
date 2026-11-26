"""Locate the first out-of-band return and the source of CM error on the actual trimmed DUT."""
import argparse
import math
from pathlib import Path
from statistics import mean
import sys

from ota import config_for_site
from ota_cmfb import checked_trace, persist, swing_points
from ota_matching import fullscale_copy
from ota_measure import samples
from ota_mismatch import CM_GROUPS, COUNT, SEEDS, coverage, record
from ota_native import exported
from ota_robust import fullscale_metrics, fullscale_source
from ota_trim import applied
from ota_trim_views import EXTRA_MOS, bit_gain
from ota_views import TB
from bias_sim import STATES
from spectre_run import read_json, sha256
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"skills/paper-to-virtuoso/scripts"))
from settling_diagnosis import common_components, settling_window


def wave_detail(raw,state,icm):
    trace = samples(raw,"time",("OUTP","OUTN"))
    derived = fullscale_metrics(raw,state[2],icm)
    diff = [p-n for p,n in zip(trace["OUTP"],trace["OUTN"])]
    result = {}
    for edge,target,start,stop in (("rising",.30,4e-6,6.004e-6),("falling",-.30,6.005e-6,10e-6)):
        windows = {str(mv):settling_window(trace["time"],diff,target,mv*1e-3,start,stop,2.001e-9)
                   for mv in (60,12,3,1)}
        held = windows["1"]["permanent_entry_s"]
        expected = derived["directions"][edge]["differential_settling_us"]
        if (held is None)!=(expected is None) or (held is not None and abs(held*1e6-expected)>1e-9):
            raise ValueError("Permanent differential settling disagrees with the circuit acceptance test")
        result[edge] = windows
    return dict(edges=result,raw_sha256=sha256(raw))


def correlation(first,second):
    a,b = mean(first),mean(second)
    dx,dy = [v-a for v in first],[v-b for v in second]
    denom = math.sqrt(sum(v*v for v in dx)*sum(v*v for v in dy))
    return sum(x*y for x,y in zip(dx,dy))/denom if max(first)-min(first)>1e-8 and max(second)-min(second)>1e-8 else None


def group_detail(out,statistics,state,icm,site,code,fresh):
    native = applied(exported(statistics/"statistical-oa",TB),code)
    levels = {}
    for bit in range(4):
        value = int(bit_gain(code,bit))
        levels["T"+str(bit)],levels["XDUT.XI.TB"+str(bit)] = value,1-value
    label = "{}-{}".format(state[0],round(1000*icm))
    runs,results = {},{}
    for arm in ("off",*CM_GROUPS,"outside_cm","all"):
        existing = arm=="all"
        folder = statistics/(label+"-code"+str(code)) if existing else out/(label+"-"+arm)
        run = record(folder,native,state,icm,site,arm,SEEDS[0],fresh and not existing and not folder.exists(),EXTRA_MOS,levels)
        runs[arm]=run
        components=[]
        imbalance=[]
        for i in range(1,COUNT+1):
            rows = swing_points(folder/("psf/mc-{:03d}_cm_sweep.dc".format(i)),state,icm)
            components.append(common_components([r["command_V"] for r in rows],[r["cm_error_mV"] for r in rows]))
            a,b = [abs(rows[1]["current_uA"][name]) for name in ("MCTP","MCTN")]
            if a+b<=0:
                raise ValueError("Missing CM detector tail currents")
            imbalance.append((a-b)/(a+b))
        results[arm] = dict(passed=run["passed_per_gate"]["common"],total=COUNT,
                            max_abs_mV={k:max(abs(row[k]) for row in components) for k in ("zero","odd","even")},
                            tail_odd_correlation=correlation(imbalance,[row["odd"] for row in components]),
                            job_sha256=run["job_sha256"])
    coverage(runs)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("run","verify"))
    for key in ("config","nominal","statistics","out"):
        p.add_argument("--"+key,type=Path,required=True)
    args = p.parse_args()
    site = config_for_site(args.config)
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    fresh = args.action=="run"
    if fresh:
        args.out.mkdir(parents=True,exist_ok=True)
    nom,stats = (read_json(f/"summary.json") for f in (args.nominal,args.statistics))
    if (stats["nominal_sha256"]!=sha256(args.nominal/"summary.json") or nom["fixed_code"]!=stats["fixed_code"]
            or stats["seed"]!=SEEDS[0] or stats["total"]!=COUNT):
        raise ValueError("Nominal and development evidence must belong to the same calibrated circuit")
    fixed = "oa" if nom["fixed_code"]==nom["bank"]["code"] else "fixed-code-oa"
    net = fullscale_copy(exported(args.nominal/fixed,TB))
    wave,group = {},{}
    for state in STATES:
        for icm in (.95,1.10):
            label = "{}-{}mV".format(state[0],round(1000*icm))
            raw = checked_trace(args.nominal/"fullscale"/label,fullscale_source(net,site,state,icm),"tran.tran",site,False)
            wave[label] = wave_detail(raw,state,icm)
            if wave[label]["raw_sha256"]!=nom["fullscale"][label]["raw_sha256"]:
                raise ValueError("The diagnostic waveform differs from nominal qualification")
        if state[0]!="tt":
            icm = 1.10 if state[0]=="ss" else .95
            group[state[0]] = group_detail(args.out,args.statistics,state,icm,site,nom["fixed_code"],fresh)
    result = dict(waveforms=wave,groups=group,fixed_code=nom["fixed_code"],seed=SEEDS[0],
                  nominal_sha256=sha256(args.nominal/"summary.json"),statistics_sha256=sha256(args.statistics/"summary.json"))
    persist(args.out/"summary.json",result,fresh and not (args.out/"summary.json").exists())
    print("Replayed",len(wave),"full-scale waveforms and two sets of six 34-MOS mismatch arms")


if __name__=="__main__":
    main()
