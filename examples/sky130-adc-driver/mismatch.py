"""Measure the selected sampling driver's slow-corner margin with local SKY130 MOS mismatch."""
import argparse
import csv
import io
import json
import math
from pathlib import Path
import re
import shutil
import sys

from verify_results import read, verify as verify_driver
from run import MIRROR, digest, requirements, save_json, simulate
from driver import audit
from fetch_models import LOCK, verify as verify_models
from mos_table import command
from mismatch_bench import (METRICS, acquisition, measured, pair_deck, pair_response, require,
                            sample_deck, summarize, trial_names)


SOURCE = ("requirements.json","sizing.json","TB_ADRV_SAMPLE.spice","transient.dat","driver-recipe.json","selection.json")
SOLVER_FILES = ("input.spice","ngspice.log","op.dat","balance.dat","ac.dat","transient.dat")


def operating_source(out):
    frozen=out/"source"
    spec=requirements(frozen/"requirements.json")
    sizes=read(frozen/"sizing.json")
    native=(frozen/"TB_ADRV_SAMPLE.spice").read_text(encoding="utf-8")
    audit(native,sizes,spec)
    return spec,sizes,native


def archive_files(out, trials):
    names=["recipe.json","ngspice-version.txt","vectors.json"]
    names+=["source/"+n for n in SOURCE]
    names+=["calibration/"+n for n in ("input.spice","ngspice.log","pair.dat")]
    names+=[folder+"/"+n for folder,_,_ in trials for n in SOLVER_FILES]
    return {n:digest(out/n) for n in names}


def validate_run(folder, expected):
    require((folder/"input.spice").read_text(encoding="utf-8")==expected,"Simulation input differs at "+folder.name)
    log=(folder/"ngspice.log").read_text(encoding="utf-8",errors="replace")
    require("analysis_complete" in log and not re.search(
        r"(?im)^\s*(?:fatal|error|warning)\b|timestep too small|simulation interrupted|no such vector",log),
        "Incomplete simulation at "+folder.name)


def same(a,b,tol,what):
    require(set(a)==set(b) and all(math.isclose(a[k],b[k],rel_tol=1e-9,abs_tol=tol) for k in a),what)


def derive(out):
    recipe=read(out/"recipe.json")
    require(recipe["schema_version"]==1 and recipe["corner"]=="ss" and recipe["model_revision"]==LOCK["revision"]
            and recipe["model_lock_sha256"]==digest(MIRROR/"models.lock.json")
            and recipe["solver_version_sha256"]==digest(out/"ngspice-version.txt"),"Inconsistent simulation recipe")
    origin=read(out/"source/driver-recipe.json")
    selected=read(out/"source/selection.json")
    require(origin["model_lock_sha256"]==recipe["model_lock_sha256"] and origin["library_sha256"]==recipe["model_library_sha256"]
            and selected["status"]=="completed" and selected["selected"]==recipe["variant"]
            and recipe["variant"] in selected["passing_variants"],"Model identity or selection differs from the nominal driver")
    spec,sizes,native=operating_source(out)
    trials=trial_names(recipe["first_seed"],recipe["samples"])
    require(read(out/"vectors.json")=={str(s):values for _,s,values in trials if s is not None},
            "A recorded local mismatch vector differs from its preassigned seed")
    require(recipe["frozen_sha256"]=={n:digest(out/"source"/n) for n in SOURCE},"Frozen design or source waveform changed")
    lib=recipe["model_path"]
    validate_run(out/"calibration",pair_deck(lib,sizes))
    cal=pair_response(out/"calibration/pair.dat",sizes)
    results={}
    for folder,_,values in trials:
        validate_run(out/folder,sample_deck(native,lib,spec,values))
        results[folder]=measured(out/folder,spec)
    nominal=results["reference"]
    same({k:nominal[k] for k in METRICS},acquisition(out/"source",spec),.001,
         "Nominal acquisition differs from the selected original design by over 1 uV")
    same(nominal,results["zero"],.001,"Zero local mismatch does not reproduce the unwrapped design within 1 uV")
    same(results[trials[2][0]],results["repeat"],1e-6,"A repeated seed did not reproduce its DC and sampled errors")
    rows=[dict(seed=s,**results[folder]) for folder,s,_ in trials[2:-1]]
    return dict(summarize(rows,nominal,spec),calibration=cal),rows


def sample_csv(rows, nominal, limit):
    buf=io.StringIO()
    writer=csv.writer(buf,lineterminator="\n")
    writer.writerow(("seed","input_offset_mV","offset_shift_mV",*METRICS,"within_all_four"))
    for row in rows:
        numeric=(row["input_offset_mV"],row["input_offset_mV"]-nominal["input_offset_mV"],*(row[m] for m in METRICS))
        writer.writerow((row["seed"],*(format(v,".10g") for v in numeric),int(all(row[m]<=limit for m in METRICS))))
    return buf.getvalue()


def report(recipe, summary):
    names={"up_track_mV":"上行跟踪末端","up_hold_mV":"上行保持窗口最大值",
           "down_track_mV":"下行跟踪末端","down_hold_mV":"下行保持窗口最大值","input_offset_mV":"平衡点输入失调（有符号）"}
    lines=["# ADC 采样驱动器的慢角局部失配","","冻结版本为 `"+recipe["variant"]+
           "`。SS，1.62 V，85 °C。输入失调在输入范围中点、开关处于跟踪状态时测量。","",
           "半个 LSB 为 {:.6g} mV。预先指定的 {} 个样本中，**{} 个同时满足双向跟踪和保持误差要求**。".format(
               summary["half_lsb_mV"],summary["samples"],summary["within_all_four"]),
           "这些计数描述已测样本。工艺全局变化、无源偏差和产品良率需独立评估。","",
           "| 测量 (mV) | 标称 | 最小 | 平均 | 样本标准差 | 最大 | 达标个数 |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for key,label in names.items():
        dist=summary["spread"][key]
        count=str(summary["within_each"][key]) if key in METRICS else "诊断读数"
        lines.append("| {} | {:.4f} | {:.4f} | {:.4f} | {:.4f} | {:.4f} | {} |".format(
            label,summary["nominal"][key],dist["min"],dist["mean"],dist["sample_sd"],dist["max"],count))
    lines+=["","[逐个种子的测值](samples.csv) · [判定与成对器件校准](summary.json) · [原始电路](source/TB_ADRV_SAMPLE.spice)",""]
    return "\n".join(lines)


def verify(out):
    recipe=read(out/"recipe.json")
    trials=trial_names(recipe["first_seed"],recipe["samples"])
    require(read(out/"evidence.sha256.json")==archive_files(out,trials),"A required solver file or checksum differs")
    summary,rows=derive(out)
    saved=read(out/"summary.json")
    # Compare floats at the precision of independent recalculation across platforms.
    def equal(a,b):
        if isinstance(a,dict):
            return isinstance(b,dict) and a.keys()==b.keys() and all(equal(v,b[k]) for k,v in a.items())
        return math.isclose(a,b,abs_tol=1e-9,rel_tol=1e-9) if isinstance(a,float) and type(b) in (float,int) else a==b
    require(equal(summary,saved),"Summary differs from the raw DC sweep and switching waveforms")
    require((out/"samples.csv").read_text(encoding="utf-8")==sample_csv(rows,summary["nominal"],summary["half_lsb_mV"])
            and (out/"RESULTS.md").read_text(encoding="utf-8")==report(recipe,summary),"Rendered results differ")
    return summary


def run(args):
    driver=args.driver_run.resolve()
    status=verify_driver(driver)
    variant=status["selected"]
    require(variant in ("baseline","candidate"),"The nominal driver has no qualified variant to freeze")
    models=(args.models or driver/"models").resolve()
    lib=verify_models(models)
    require('"' not in str(lib) and "\n" not in str(lib),"Model path cannot be represented in SPICE")
    binary=shutil.which(args.ngspice)
    require(binary,"ngspice is required for a new run")
    ng=str(Path(binary).resolve())
    trials=trial_names(args.seed,args.samples)
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    frozen=out/"source"
    frozen.mkdir()
    chosen=driver/variant
    originals=(driver/"requirements.json",chosen/"sizing.json",chosen/"views/TB_ADRV_SAMPLE.spice",chosen/"ss/track/transient.dat",
               driver/"recipe.json",driver/"status.json")
    for name,source in zip(SOURCE,originals):
        shutil.copyfile(str(source),str(frozen/name))
    spec,sizes,native=operating_source(out)
    command([ng,"-n","--version"],out,"ngspice-version.txt")
    recipe={"schema_version":1,"variant":variant,"corner":"ss","model_revision":LOCK["revision"],
            "model_lock_sha256":digest(MIRROR/"models.lock.json"),"model_library_sha256":digest(lib),
            "model_path":str(lib),"solver_version_sha256":digest(out/"ngspice-version.txt"),"solver_binary_sha256":digest(Path(ng)),
            "first_seed":args.seed,"samples":args.samples,"frozen_sha256":{n:digest(frozen/n) for n in SOURCE}}
    require(read(driver/"recipe.json")["library_sha256"]==recipe["model_library_sha256"],"Source driver used different models")
    save_json(out/"recipe.json",recipe)
    save_json(out/"vectors.json",{str(s):v for _,s,v in trials if s is not None})
    calibration=out/"calibration"
    calibration.mkdir()
    simulate(ng,calibration,pair_deck(lib,sizes))
    pair_response(calibration/"pair.dat",sizes)
    failures=[]
    for folder,_,values in trials:
        path=out/folder
        path.mkdir()
        try:
            simulate(ng,path,sample_deck(native,lib,spec,values))
        except (ValueError,RuntimeError,OSError) as error:
            failures.append({"sample":folder,"error":str(error)})
    if failures:
        save_json(out/"run-failures.json",failures)
        raise ValueError("Incomplete simulations recorded in run-failures.json")
    verify_models(models)
    summary,rows=derive(out)
    save_json(out/"evidence.sha256.json",archive_files(out,trials))
    save_json(out/"summary.json",summary)
    (out/"samples.csv").write_bytes(sample_csv(rows,summary["nominal"],summary["half_lsb_mV"]).encode())
    (out/"RESULTS.md").write_text(report(recipe,summary),encoding="utf-8")
    return summary


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    commands=cli.add_subparsers(dest="action")
    execute=commands.add_parser("run",help="Freeze the nominal driver and run local mismatch")
    execute.add_argument("--driver-run",required=True,type=Path)
    execute.add_argument("--models",type=Path)
    execute.add_argument("--ngspice",default="ngspice")
    execute.add_argument("--samples",type=int,default=32)
    execute.add_argument("--seed",type=int,default=24701,help="First seed in a consecutive range")
    for cmd in (execute,commands.add_parser("verify",help="Recompute from the saved artifact using Python")):
        cmd.add_argument("--out",required=True,type=Path)
    args=cli.parse_args()
    if not args.action:
        cli.error("Choose run or verify")
    try:
        summary=run(args) if args.action=="run" else verify(args.out)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        cli.exit(2,str(error)+"\n")
    print(json.dumps({k:summary[k] for k in ("status","samples","within_all_four","within_each","half_lsb_mV")}))
    return int(args.action=="run" and summary["status"]=="sample_limits_exceeded")


if __name__=="__main__":
    sys.exit(main())
