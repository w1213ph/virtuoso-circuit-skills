"""Build an editable SKY130 sampling driver, then check its two clock states and both signal directions."""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
MIRROR=HERE.parent/"sky130-current-mirror"
sys.path.insert(0,str(MIRROR))
sys.path.insert(0,str(HERE.parents[1]/"skills/paper-to-virtuoso/scripts"))
import check_prelayout as checks
from fetch_models import LOCK, checksum, prepare, verify
from mos_table import STATES, characterize, command, save_json, simulate
from bench import FIXED_OUTPUT_PF, budget, deck, loop_metrics, requirements, transient_metrics
from driver import build, size
import precision


def digest(p):
    return checksum(p.read_bytes())


def tests_for(corner, temp, vdd, r, theory, include_diagnostics=False, include_dc=False):
    common = {"corner":corner,"supply_V":vdd,"temperature_C":temp,"input_V":[r["input_low_V"],r["input_high_V"]],
              "sample_cap_pF":r["sample_cap_pF"],"output_cap_pF":FIXED_OUTPUT_PF,"track_ns":r["track_ns"],"hold_ns":r["hold_ns"]}
    definitions = []
    for edge in ("up","down"):
        for item,description in (("track","Absolute error on the capacitor just before switch opening"),
                                 ("hold","Worst absolute capacitor error from switch fully off through the hold deadline")):
            definitions.append((edge+"."+item,"mV","tran","track",description+" after the "+edge+" step","<=",theory["half_lsb_mV"]))
    for mode in ("track","hold"):
        definitions.append((mode+".margin","deg","ac",mode,"Phase margin of the loaded voltage return ratio with the switch "+mode,
                            ">=",r["min_phase_margin_deg"]))
    for name,unit,analysis,mode,definition,op,limit in definitions:
        yield {"id":corner+"."+name,"metric":name,"unit":unit,"analysis":analysis,"testbench":"TB_ADRV_SAMPLE",
               "conditions":common,"measurement":definition,"inputs":[corner+"/"+mode+"/input.spice"],
               "target":{"source":"../requirements.json","confirmed":True,"limits":{op:limit}}}
    if include_diagnostics:
        for edge in ("up", "down"):
            name = edge + ".switch_drop"
            yield {"id": corner+"."+name, "role": "diagnostic", "metric": name, "unit": "mV",
                   "analysis": "tran", "testbench": "TB_ADRV_SAMPLE", "conditions": common,
                   "measurement": "Absolute OUT-to-SAMPLE drop just before switch opening after the "+edge+" step",
                   "inputs": [corner+"/track/input.spice"], "target": None}
    if include_dc:
        yield {"id": corner+".dc.transfer", "role": "diagnostic", "metric": "dc.transfer", "unit": "mV",
               "analysis": "dc", "testbench": "TB_ADRV_SAMPLE", "conditions": common,
               "measurement": "Maximum absolute static capacitor error across nine input voltages with the switch on",
               "inputs": [corner+"/dc/input.spice"], "target": None}


def simulate_variant(path,native,r,lib,tool,references,include_dc=False):
    theory=budget(r)
    views = sorted("views/" + p.name for p in (path/"views").iterdir() if p.suffix in (".sch", ".sym"))
    plan={"schema_version":1,"inputs":["sizing.json","views/TB_ADRV_SAMPLE.spice",*views],"reference_inputs":references,"tests":[],"results":{}}
    for state in STATES:
        corner,temp,vdd=state
        for mode in ("track","hold"):
            d=path/corner/mode
            d.mkdir(parents=True)
            (d/"input.spice").write_text(deck(native,lib,state,r,mode=="track"),encoding="utf-8")
        if include_dc:
            dc = path/corner/"dc"
            dc.mkdir()
            (dc/"input.spice").write_text(precision.dc_deck(native,lib,state,r),encoding="utf-8")
        plan["tests"].extend(tests_for(corner,temp,vdd,r,theory,include_diagnostics=True,include_dc=include_dc))
    f=path/"prelayout.json"
    save_json(f,plan)
    plan,before=checks.load_records(f,{})
    if any("error" in row for row in before.values()):
        raise ValueError("Incomplete pre-simulation inputs")
    save_json(path/"before-simulation.json",before)
    details={}
    for corner,temp,vdd in STATES:
        folder=path/corner
        values={}
        detail={}
        for mode in ("track","hold"):
            d=folder/mode
            try:
                simulate(tool,d,(d/"input.spice").read_text(encoding="utf-8"))
                detail[mode]=loop_metrics(d,vdd,mode=="track",r)
                values[mode+".margin"]=detail[mode]["phase_margin_deg"]
                if mode=="track":
                    detail["transient"]=transient_metrics(d,r,vdd)
                    for edge,x in detail["transient"]["edges"].items():
                        for item in ("track","hold"):
                            values[edge+"."+item]=x[item+"_error_mV"]
                        values[edge+".switch_drop"]=x["switch_drop_mV"]
            except (ValueError,OSError) as exc:
                detail[mode+"_error"]=str(exc)
        if include_dc:
            dc = folder/"dc"
            simulate(tool,dc,(dc/"input.spice").read_text(encoding="utf-8"))
            detail["dc"] = precision.dc_transfer(dc,r,vdd)
            values["dc.transfer"] = detail["dc"]["worst_abs_mV"]
        save_json(folder/"measurement.json",detail)
        details[corner]=detail
        for t in (t for t in plan["tests"] if t["id"].startswith(corner+".")):
            mode="hold" if t["metric"]=="hold.margin" else "track"
            files=[folder/"measurement.json",folder/mode/"ngspice.log",folder/mode/"op.dat",
                   folder/mode/("ac.dat" if t["analysis"]=="ac" else "transient.dat")]
            if t["analysis"]=="dc":
                files=[folder/"measurement.json",folder/"dc/ngspice.log",folder/"dc/dc.dat"]
            record={"unit":t["unit"],"context_sha256":before[t["id"]]["context_sha256"],
                    "evidence":{p.relative_to(path).as_posix():digest(p) for p in files if p.is_file()}}
            if t["metric"] in values:
                record.update(status="measured",value=values[t["metric"]])
            else:
                record.update(status="run_failed",reason=detail.get(mode+"_error","Missing measurement"))
            plan["results"][t["id"]]=record
        save_json(f,plan)
        print(path.name,corner,detail.get("track_error","track measured"),detail.get("hold_error","hold measured"),flush=True)
    plan,after=checks.load_records(f,{})
    review=checks.check(plan,path,after,{})
    save_json(path/"checked.json",review)
    return review,details


def report(out,r,theory,geometry,measurements,reviews,pick,incomplete=False,search=None):
    lines=["# ADC 采样驱动器前仿","","    "+r["purpose"].replace("\n","\n    "),"",
           "量化范围 {:g} V、{} 位，半个 LSB 为 {:.4f} mV。输入步阶 {:.3f} V，采样电容 {:g} pF、输出固定负载 {:g} pF。".format(
               r["full_scale_span_V"],r["adc_bits"],theory["half_lsb_mV"],theory["step_V"],r["sample_cap_pF"],FIXED_OUTPUT_PF),
           "跟踪 {:g} ns，关断后观察 {:g} ns；跟踪和保持工作点的小信号相位裕度目标均为 {:g}°。".format(
               r["track_ns"],r["hold_ns"],r["min_phase_margin_deg"]),
           "单极点初算 τ ≤ {:.2f} ns，带宽 ≥ {:.2f} MHz，等效跨导 ≥ {:.2f} µS。查表与尺寸见各版 sizing.json。".format(
               theory["tau_max_ns"],theory["one_pole_bandwidth_MHz"],theory["gm_floor_uS"]),"",
           "**有测量未完成，无法完成两版选型。**" if incomplete else
           ("**选中 "+pick+"。**" if pick else "**当前条件下没有全部达标的版本。**"),""]
    if pick:
        lines+=["[选中原理图]({0}/views/ADRV_OTA.sch) · [实际采样负载]({0}/views/ADRV_SAMPLER.sch) · "
                "[测试台]({0}/views/TB_ADRV_SAMPLE.sch) · [逐项结果]({0}/checked.json)".format(pick),"",
                "在本结果目录执行 `sh "+pick+"/open.sh bench`。可将 `bench` 换为 `core`、`symbol` 或 `sampler`。",""]
    if search:
        lines+=["## 尺寸与静态精度","",
                "每个工况在输入区间等距测九点。静态误差用于比较镜像比。选型先核对 18 项双向采样和稳定性要求，再比较放大器与传输门的总栅面积。",
                "","| 电路 | 镜像单元 VX / OUT | 传输门并联 | 最坏静态误差 (mV) | 放大器 / 开关栅面积 (µm²) | 达标项 |",
                "|---|---:|---:|---:|---:|---:|"]
        for name, row in search.items():
            sz=geometry[name]
            d=sz["devices"]
            lines.append("| [{0}]({0}/views/ADRV_OTA.sch) | {1} / {2} | {3} | {4:.4f} | {5:.3f} / {6:.3f} | {7}/18 |".format(
                name,d["XLA"][2],d["XLB"][2],d["XSN"][2],row["static_mV"],sz["core_gate_area_um2"],
                sz["switch_gate_area_um2"],row["pass_count"]))
        lines+=["","[各版尺寸筛选记录](optimization.json)",""]
    lines+=["| 版本、工况 | 跟踪误差 ↑ / ↓ (mV) | 保持最坏误差 ↑ / ↓ (mV) | 相位裕度 跟踪 / 保持 (°) | 静态电流 (µA) |",
            "|---|---:|---:|---:|---:|"]
    for name,records in measurements.items():
        for corner,m in records.items():
            if not all(k in m for k in ("transient","track","hold")):
                lines.append("| {} {} | 测量不完整 | | | |".format(name,corner))
                continue
            a,b=(m["transient"]["edges"][d] for d in ("up","down"))
            lines.append("| {} {} | {:.3f} / {:.3f} | {:.3f} / {:.3f} | {:.2f} / {:.2f} | {:.2f} |".format(
                name,corner,a["track_error_mV"],b["track_error_mV"],a["hold_error_mV"],b["hold_error_mV"],
                m["track"]["phase_margin_deg"],m["hold"]["phase_margin_deg"],m["track"]["quiescent_uA"]))
    lines.append("")
    for name,review in reviews.items():
        required=[t for t in review["tests"] if t.get("role")!="diagnostic"]
        failed=[t for t in required if t["status"]!="PASS"]
        lines.append("- {} 通过 {}/{} 项，放大器栅面积 {:.2f} µm²，采样开关另计 {:.2f} µm²。".format(name,len(required)-len(failed),
                     len(required),geometry[name]["core_gate_area_um2"],geometry[name]["switch_gate_area_um2"]))
        for t in failed:
            if t["status"]=="FAIL":
                op,limit=next(iter(t["limits"].items()))
                info="实测 {:.4g} {}，要求 {} {:g} {}".format(t["value"],t["unit"],{"<=":"≤",">=":"≥"}[op],limit,t["unit"])
            else:
                info=t.get("detail",t["status"])
            lines.append("  - "+t["id"]+" "+info)
    lines+=["", "### 开关导通末端的误差来源", "",
            "在跟踪结束、时钟开始关断的同一时刻，比较驱动输出和采样电容。验收使用采样电容相对目标的误差。下表的开关压差帮助定位误差来源。", "",
            "| 版本、工况 | 边沿 | 驱动输出到目标 (mV) | 开关两端压差 (mV) | 采样电容到目标 (mV) |",
            "|---|---|---:|---:|---:|"]
    for name, records in measurements.items():
        for corner, record in records.items():
            if "transient" not in record:
                lines.append("| {} {} | 双向 | 测量不完整 | | |".format(name,corner))
                continue
            for edge, label in (("up","上升"), ("down","下降")):
                m=record["transient"]["edges"][edge]
                lines.append("| {} {} | {} | {:.4f} | {:.4f} | {:.4f} |".format(
                    name,corner,label,m["driver_error_mV"],m["switch_drop_mV"],m["track_error_mV"]))
    lines+=["","参考支路由电源直接建立。静态电流在两输入电平的中点从放大器电源测得。电源、输入、时钟是测试台激励。"
            "小信号裕度通过高阻反馈输入的串联电压注入估算，真实开关的双向瞬态单独验收。"
            "面积统计两类电路的 MOS 栅面积；完整 ADC、噪声、随机失配和电容版图面积仍需在目标工程验证。",""]
    (out/"RESULTS.md").write_text("\n".join(lines),encoding="utf-8")


def run(out,r,models,previous,xschem,ngspice,optimize=False):
    save_json(out/"requirements.json",r)
    theory=budget(r)
    save_json(out/"budget.json",theory)
    lib=verify(models) if models else prepare(out/"models")
    if '"' in str(lib) or "\n" in str(lib):
        raise ValueError("Unsupported model path")
    tables=out/"characterization"
    tables.mkdir()
    command([ngspice,"-n","--version"],out,"ngspice-version.txt")
    identity={"model_lock_sha256":digest(MIRROR/"models.lock.json"),"library_sha256":digest(lib),
              "ngspice_version_sha256":digest(out/"ngspice-version.txt"),"ngspice_binary_sha256":digest(Path(ngspice))}
    for state in STATES:
        rows,record=characterize(tables,lib,ngspice,state,identity,previous)
        if state[0]=="tt":
            tt_rows=rows
        print("MOS",state[0],record["mode"],record["points"],flush=True)
    recipe=dict(identity,schema_version=4 if optimize else 3,extractor=digest(HERE/"bench.py"),driver=digest(HERE/"driver.py"),
                runner=digest(HERE/"run.py"),xschem_binary=digest(Path(xschem)))
    if optimize:
        recipe["precision_search"]=digest(HERE/"precision.py")
    save_json(out/"recipe.json",recipe)
    refs=["../requirements.json","../budget.json","../recipe.json","../fixture.json","../ngspice-version.txt",str(lib)]
    refs += [str(lib.parent/p) for p in sorted(LOCK["files"])]
    geometry,reviews,measurements={},{},{}
    def measure(name,sizing):
        path=out/name
        path.mkdir()
        geometry[name]=sizing
        save_json(path/"sizing.json",sizing)
        native,readback=build(path/"views",xschem,sizing,r)
        save_json(path/"readback.json",readback)
        fixture={k:readback["native_sections"][k] for k in ("TOP","ADRV_SAMPLER")}
        if name=="baseline":
            save_json(out/"fixture.json",fixture)
        elif not precision.same_fixture(readback["native_sections"],json.loads((out/"fixture.json").read_text()),
                                        optimize and sizing["devices"]["XSN"][2]!=1):
            raise ValueError("Candidate changed the sampling fixture")
        reviews[name],measurements[name]=simulate_variant(path,native,r,lib,ngspice,refs,include_dc=optimize)
        if optimize:
            return precision.summarize(sizing,measurements[name],reviews[name])

    originals={name:size(tt_rows,r,name=="candidate") for name in precision.ORIGINALS}
    search=precision.evaluate_family(originals,optimize,measure)
    comparison=checks.compare(reviews["baseline"],reviews["candidate"])
    save_json(out/"comparison.json",comparison)
    missing = [name+"/"+test["id"] for name,review in reviews.items() for test in review["tests"]
               if test["status"] not in ("PASS", "FAIL", "DIAGNOSTIC_MEASURED")]
    eligible=comparison["passing_variants"]
    pick=min(eligible,key=lambda n:geometry[n]["core_gate_area_um2"]) if eligible and not missing else None
    if optimize:
        if any(checks.compare(reviews["baseline"],review)["status"]!="COMPARABLE" for review in reviews.values()):
            raise ValueError("Optimized variants have different measurement contracts")
        eligible,pick=precision.selection(search)
        save_json(out/"optimization.json",search)
    report(out,r,theory,geometry,measurements,reviews,pick,incomplete=bool(missing),search=search if optimize else None)
    if missing:
        return {"status":"failed","selected":None,"reason":"Missing verified measurements: "+", ".join(missing)}
    return {"status":"completed" if pick else "review_required","passing_variants":eligible,"selected":pick}


def main(argv=None, default_requirements=HERE/"requirements.example.json"):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out",type=Path,required=True,help="New result directory")
    cli.add_argument("--requirements",type=Path,default=default_requirements)
    cli.add_argument("--models",type=Path,help="Already verified public SKY130 model directory")
    cli.add_argument("--reuse-lut",type=Path,help="Reuse a previous verified characterization if compatible")
    cli.add_argument("--optimize",action="store_true",help="Measure DC transfer and try mirror ratios and switch sizes if both original drivers miss")
    cli.add_argument("--xschem",default="xschem")
    cli.add_argument("--ngspice",default="ngspice")
    args=cli.parse_args(argv)
    try:
        r=requirements(args.requirements)
        tools=[shutil.which(v) for v in (args.xschem,args.ngspice)]
        if not all(tools):
            raise ValueError("Install Xschem and ngspice or specify their executable paths")
        out=args.out.resolve()
        out.mkdir(parents=True)
    except (OSError,ValueError) as exc:
        cli.error(str(exc))
    save_json(out/"status.json",{"status":"running"})
    try:
        result=run(out,r,args.models.resolve() if args.models else None,args.reuse_lut.resolve() if args.reuse_lut else None,
                   *(str(Path(p).resolve()) for p in tools),optimize=args.optimize)
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        result={"status":"failed","reason":str(exc)}
    save_json(out/"status.json",result)
    print(json.dumps(result),flush=True)
    return {"completed":0, "review_required":1, "failed":2}[result["status"]]


if __name__=="__main__":
    sys.exit(main())
