"""Calibrate each physical OTA once on development silicon draws and test its frozen code across PVT."""
from collections import Counter
from pathlib import Path
import sys

from ota_cm_groups import arguments
from ota_cmfb import ICM, POWER_UW, persist
from ota_mismatch import COUNT, LIMITS, SEEDS, coverage, nominal_control, record
from ota_native import hierarchy
from ota_robust import load_design
from ota_trim import BANKS, GEOMETRY, applied, physical_bank
from ota_trim_views import EXTRA_MOS, bit_gain
from bias_sim import STATES
from spectre_run import read_json, sha256

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"skills/paper-to-virtuoso/scripts"))
from trim_calibration import held_measurements, select_codes


def bank_run(folder,native,state,icm,site,code,fresh):
    levels = {}
    for bit in range(4):
        value = int(bit_gain(code,bit))
        levels["T"+str(bit)] = value
        levels["XDUT.XI.TB"+str(bit)] = 1-value
    return record(folder,applied(native,code),state,icm,site,"all",SEEDS[0],fresh,EXTRA_MOS,levels)


def score(cases):
    ids = set(next(iter(cases.values())))
    if len(ids)!=COUNT or any(set(rows)!=ids for rows in cases.values()):
        raise ValueError("Require complete matched physical draws across operating conditions")
    by_draw = {}
    for draw in sorted(ids):
        points = [point for rows in cases.values() for point in rows[draw]]
        if len(points)!=18:
            raise ValueError("Expected all three commands at six operating conditions")
        failed = {k for row in points for k in row["failed"]}
        if max(row["power_uW"] for row in points)>POWER_UW:
            failed.add("power_budget")
        by_draw[draw] = dict(failed=sorted(failed),
            max_tracking_mV=max(abs(row["signed_diff_error_mV"]) for row in points),
            max_common_mV=max(abs(row["cm_error_mV"]) for row in points),
            max_power_uW=max(row["power_uW"] for row in points))
    return dict(passed=sum(not v["failed"] for v in by_draw.values()),
                passed_per_gate={k:sum(k not in v["failed"] for v in by_draw.values()) for k in (*LIMITS,"power_budget")},
                draws=by_draw)


def experiment(args,site,fresh):
    _,sizes,reference = load_design(args.ota,args.cmfb,site)
    nominal = read_json(args.nominal/"summary.json")
    bank = nominal["bank"]
    if nominal["baseline_sha256"]!=sha256(args.matching/"sensitivity.json") or bank not in BANKS.values():
        raise ValueError("Nominal evidence belongs to a different physical bank")
    net,area = physical_bank(args.nominal/"oa",site,sizes,reference,False,bank)
    if fresh:
        args.out.mkdir(parents=True)
    stat,measured = physical_bank(args.out/"statistical-oa",site,dict(sizes,mos_family="statistical"),reference,fresh,bank)
    if (measured!=area or nominal["budget"]!=area or nominal["geometry"]!={k:list(v) for k,v in GEOMETRY.items()}
            or hierarchy(net)[1]!=hierarchy(stat)[1]):
        raise ValueError("The nominal and statistical fixtures, geometry or passives differ")
    center = next(state for state in STATES if state[0]=="tt")
    target = ICM[0]
    controls = {}
    for code in sorted({0,nominal["fixed_code"],15}):
        folder = args.out/("nominal-code"+str(code))
        if fresh:
            folder.mkdir()
        for state in STATES:
            for icm in ICM:
                key = "{}-{}-{}".format(code,state[0],round(icm*1000))
                controls[key] = nominal_control(folder,site,state,icm,applied(net,code),applied(stat,code),fresh)
    calibration = {i:bank_run(args.out/("tt-950-code"+str(i)),stat,center,target,site,i,fresh) for i in range(16)}
    coverage(calibration)
    errors = {}
    for code,run in calibration.items():
        if any(abs(row[1]["command_V"])>1e-12 for row in run["samples"]):
            raise ValueError("Calibration requires zero differential command")
        errors[code] = {n:r[1]["signed_diff_error_mV"] for n,r in zip(run["ids"],run["samples"])}
    frozen = select_codes(errors,4)
    persist(args.out/"frozen.json",dict(seed=SEEDS[0],reference="tt-950-zero",calibration_codes=frozen,
            jobs={str(c):r["job_sha256"] for c,r in calibration.items()},oa_sha256=sha256(args.out/"statistical-oa/build.il")),fresh)
    nominal_code = nominal["fixed_code"]
    nominal_errors = [nominal["curves"]["tt-950"][str(c)]["points"][1]["signed_error_mV"] for c in range(16)]
    if nominal_code!=min(range(16),key=lambda i:(abs(nominal_errors[i]),i)):
        raise ValueError("The nominal fixed-code control differs from the reference calibration")
    used = sorted(set(frozen.values())|{nominal_code})
    fixed,selected,jobs = {},{},{}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}".format(state[0],round(icm*1000))
            runs = {c:calibration[c] if (state==center and icm==target) else
                    bank_run(args.out/(label+"-code"+str(c)),stat,state,icm,site,c,fresh) for c in used}
            coverage(dict(runs,reference=calibration[0]))
            observed = {c:dict(zip(r["ids"],r["samples"])) for c,r in runs.items()}
            selected[label] = held_measurements(frozen,observed)
            fixed[label] = observed[nominal_code]
            jobs[label] = {str(c):r["job_sha256"] for c,r in runs.items()}
            print(label,"replayed",len(used),"physical codes",flush=True)
    a,b = score(fixed),score(selected)
    pairs = Counter((bool(a["draws"][n]["failed"]),bool(b["draws"][n]["failed"])) for n in frozen)
    result = dict(seed=SEEDS[0],total=COUNT,unvaried_model_controls=controls,calibration_sha256=sha256(args.out/"frozen.json"),
            nominal_sha256=sha256(args.nominal/"summary.json"),fixed_code=nominal_code,
            histogram={str(c):n for c,n in Counter(frozen.values()).items()},fixed=a,calibrated=b,
            paired=dict(both_pass=pairs[False,False],gained=pairs[True,False],lost=pairs[False,True],both_fail=pairs[True,True]),jobs=jobs)
    persist(args.out/"summary.json",result,fresh)
    return result


if __name__=="__main__":
    options,settings = arguments(__doc__,with_nominal=True)
    report = experiment(options,settings,options.action=="run")
    print("Same physical DUT and draws, fixed",report["fixed"]["passed"],"calibrated",report["calibrated"]["passed"],"/",COUNT)
