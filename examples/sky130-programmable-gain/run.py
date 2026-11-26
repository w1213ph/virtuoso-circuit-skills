"""Build two editable gain stages and qualify them on the same receiving circuit."""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIRROR = HERE.parent/"sky130-current-mirror"
sys.path.insert(0, str(MIRROR))
sys.path.insert(0, str(HERE.parents[1]/"skills/paper-to-virtuoso/scripts"))
import check_prelayout as checks
from fetch_models import LOCK, checksum, prepare, verify
from gain_measure import CODES, GRID, check, deck, design, read_json, requirements, static_metrics, switching
from gain_native import build
from mos_table import STATES, characterize, command, save_json, simulate


def digest(path):
    return checksum(path.read_bytes())


def tests(r):
    definitions=[]
    for corner,temp,vdd in STATES:
        common={"corner":corner,"supply_V":vdd,"temperature_C":temp,"input_cm_V":vdd*r["input_common_mode_fraction"],
                "receiver_cap_pF":r["receiver_cap_pF"],"port":"RXIN","receiver":"PGA_RECEIVER","input":"VIN"}
        for code in CODES:
            prefix=corner+"/"+code+"/"
            state=dict(common,code_B1_B0=code,frequency_Hz=[r["low_Hz"],r["high_Hz"]],frequency_points=GRID)
            spec=(("max_gain_error_dB","dB","ac","Largest absolute dB error of RXIN/VIN on the frequency grid",
                   {"<=":r["max_gain_error_dB"]}),
                  ("inverting","1","ac","Negative real part of RXIN/VIN at the low frequency",{"==":1}),
                  ("supply_uA","uA","op","Total DC current drawn from VSUP",{">":0,"<=":r["max_supply_uA"]}),
                  ("drive_high_dB","dB","ac","Magnitude of DRIVE/VIN at the high frequency, before RISO",None))
            for name,unit,analysis,measurement,limits in spec:
                t={"id":corner+"."+code+"."+name,"metric":name,"unit":unit,"analysis":analysis,
                   "testbench":"TB_PGA_GAIN","conditions":dict(state,target_V_V=r["gain_targets_V_V"][int(code,2)]),
                   "inputs":[prefix+"input.spice"],"measurement":measurement,
                   "target":{"confirmed":True,"source":"../requirements.json","limits":limits} if limits else None}
                if not limits:
                    t["role"]="diagnostic"
                    t["conditions"]["port"]="DRIVE"
                definitions.append(t)
        for start,end in (("00","11"),("11","00")):
            definitions.append({"id":corner+"."+start+"_to_"+end+".settling_ns","metric":"settling_ns","unit":"ns",
                "analysis":"tran","testbench":"TB_PGA_GAIN","conditions":dict(common,from_code=start,to_code=end,transition=start+"_to_"+end,
                    input_offset_mV=r["transition_input_offset_mV"],settling_band_mV=r["settling_band_mV"]),
                "inputs":[corner+"/switching/input.spice",corner+"/00/input.spice",corner+"/11/input.spice"],
                "measurement":"Time after the last input bit crosses 50% until RXIN stays inside the final DC band",
                "target":{"confirmed":True,"source":"../requirements.json","limits":{"<=":r["max_settling_ns"]}}})
    return definitions


def required_sweeps():
    corners = [state[0] for state in STATES]
    return [{"metric":"max_gain_error_dB","axes":{"corner":corners,"port":["RXIN"],"code_B1_B0":list(CODES)}},
            {"metric":"settling_ns","axes":{"corner":corners,"port":["RXIN"],"transition":["00_to_11","11_to_00"]}}]


def inputs(stage):
    views=stage/"views"
    return ["readback.json","views/TB_PGA_GAIN.spice"]+[
        "views/"+p.name for p in sorted(views.iterdir()) if p.suffix in (".sch",".sym")]


REFERENCES=["../requirements.json","../recipe.json","../sizing.json","../ngspice-version.txt"]


def observations(stage,r):
    records={}
    cache={}
    detail={}
    for corner,_,supply in STATES:
        static={}
        for code in CODES:
            prefix=corner+"/"+code+"/"
            value=static_metrics(stage/prefix,r,code,supply)
            static[code]=value
            evidence={prefix+n:checks.file_hash(stage/prefix/n,cache) for n in ("ngspice.log","op.dat","ac.dat")}
            for metric in ("max_gain_error_dB","inverting","supply_uA","drive_high_dB"):
                records[corner+"."+code+"."+metric]=(value[metric],evidence)
        prefix=corner+"/switching/"
        dynamic=switching(stage/prefix,r,supply,{c:static[c]["offset_rx_V"] for c in ("00","11")})
        files=[prefix+"ngspice.log",prefix+"transient.dat"]
        for endpoint in ("00","11"):
            files.extend(corner+"/"+endpoint+"/"+n for n in ("offset.dat","ngspice.log"))
        evidence={name:checks.file_hash(stage/name,cache) for name in files}
        for start,end in (("00","11"),("11","00")):
            records[corner+"."+start+"_to_"+end+".settling_ns"]=(dynamic[end]["settling_ns"],evidence)
        detail[corner]={"static":static,"transitions":dynamic}
    return records,detail


def result(t,context,value,evidence):
    data={"context_sha256":context["context_sha256"], "evidence":evidence}
    if value is None:
        return dict(data,status="run_failed",reason="RXIN did not settle inside the final DC band within the observed window")
    return dict(data,status="measured",unit=t["unit"],value=value)


def decks(r,native,lib):
    for state in STATES:
        for code in CODES:
            yield state[0]+"/"+code,deck(native,lib,state,r,code)
        yield state[0]+"/switching",deck(native,lib,state,r,"00",transient=True)


def variant(out,name,r,sizing,lib,xs,ng):
    stage=out/name
    stage.mkdir()
    native,readback=build(stage,xs,sizing,name,r)
    save_json(stage/"readback.json",readback)
    generated=list(decks(r,native,lib))
    for directory,content in generated:
        folder=stage/directory
        folder.mkdir(parents=True)
        (folder/"input.spice").write_text(content,encoding="utf-8")
    path=stage/"prelayout.json"
    plan={"schema_version":1,"inputs":inputs(stage),"reference_inputs":REFERENCES,"required_sweeps":required_sweeps(),
          "tests":tests(r),"results":{}}
    save_json(path,plan)
    plan,before=checks.load_records(path,{})
    check(all("context_sha256" in c for c in before.values()),"Incomplete inputs before simulation")
    save_json(stage/"before-simulation.json",before)
    for directory,content in generated:
        simulate(ng,stage/directory,content)
    after=checks.snapshot(plan,stage,{})
    check(after==before,"Inputs changed during simulation")
    rows,detail=observations(stage,r)
    for t in plan["tests"]:
        idx=t["id"]
        plan["results"][idx]=result(t,before[idx],*rows[idx])
    save_json(path,plan)
    review=checks.check(plan,stage,after,{})
    save_json(stage/"checked.json",review)
    return review,readback,detail


def selection(reviews,areas):
    comparison=checks.compare(reviews["baseline"],reviews["candidate"])
    passing=comparison["passing_variants"]
    selected=min(passing,key=areas.get) if passing else None
    return comparison,{"status":"completed" if selected else "review_required","selected":selected,
                       "passing_variants":passing}


def report(out,r,sizing,status,reviews,areas,details):
    lines=["# 可编程增益前仿","",r["purpose"],"",
           "输入共模为供电的 {:.0%}，接收电容 {:g} pF。增益取实际 RXIN/VIN，在 {:g} 至 {:g} Hz 的 41 个点核对。".format(
               r["input_common_mode_fraction"],r["receiver_cap_pF"],r["low_Hz"],r["high_Hz"]),
           "最大增益误差 {:g} dB，电源电流上限 {:g} µA。切码时输入增加 {:g} mV，要求 {:g} ns 内进入并保持在独立 DC 终值的 ±{:g} mV 内。".format(
               r["max_gain_error_dB"],r["max_supply_uA"],r["transition_input_offset_mV"],r["max_settling_ns"],r["settling_band_mV"]),"",
           "**选中 {}。**".format(status["selected"]) if status["selected"] else "**两版均需复核，未选出达标版本。**","",
           "| 版本 | 正式指标通过 | 放大核心并联组数 | 放大器及两组开关栅面积 (µm²) | 整个测试电源最坏静态电流 (µA) |",
           "|---|---:|---:|---:|---:|"]
    for name,review in reviews.items():
        required=[t for t in review["tests"] if t.get("role")!="diagnostic"]
        current=max(m["supply_uA"] for c in details[name].values() for m in c["static"].values())
        lines.append("| {} | {}/{} | {} | {:.2f} | {:.2f} |".format(name,sum(t["status"]=="PASS" for t in required),
                     len(required),sizing[name],areas[name],current))
    for name,data in details.items():
        lines+=["","## "+name,"","[测试台]("+name+"/views/TB_PGA_GAIN.sch) · [放大级]("+name+"/views/PGA_STAGE.sch) · [接收端]("+name+"/views/PGA_RECEIVER.sch) · [逐项判定]("+name+"/checked.json)","",
                "| 工况 | B1 B0 | 理想增益幅值 (dB) | RXIN 低端 / 高端 (dB) | DRIVE 高端 (dB) | 全频点最大误差 (dB) | RXIN 直流 (V) |",
                "|---|---|---:|---:|---:|---:|---:|"]
        for corner,record in data.items():
            for code,m in record["static"].items():
                target=20*math.log10(r["gain_targets_V_V"][int(code,2)])
                lines.append("| {} | {} | {:.3f} | {:.3f} / {:.3f} | {:.3f} | {:.3f} | {:.4f} |".format(
                    corner,code,target,m["gain_low_dB"],m["gain_high_dB"],m["drive_high_dB"],m["max_gain_error_dB"],m["rx_dc_V"]))
        lines+=["","| 工况 | 00 → 11 建立 (ns) | 11 → 00 建立 (ns) |","|---|---:|---:|"]
        for corner,record in data.items():
            times=[record["transitions"][c]["settling_ns"] for c in ("11","00")]
            lines.append("| {} | {} | {} |".format(corner,*("未建立" if n is None else "{:.1f}".format(n) for n in times)))
        for t in reviews[name]["tests"]:
            if t.get("role")!="diagnostic" and t["status"]!="PASS":
                if "value" in t:
                    limits=" 且 ".join("{} {:g}".format(op,value) for op,value in t.get("limits",{}).items())
                    message="实测 {:.4g} {}，要求 {} {}".format(t["value"],t["unit"],limits,t["unit"])
                else:
                    message=t.get("detail",t["status"])
                lines+=["","- `{}` {}".format(t["id"],message)]
    lines+=["","MOS 使用 SKY130 工艺模型。电阻和电容采用标称值，接收晶体管栅面积另计 2 µm²。开关逐位回读、偏置和独立终值见各工况原始记录。","",
            "若接收电容全部由单层 MiM 实现，按 2 fF/µm² 的标称面积密度，极板面积约为 {:g} µm²。放大级四个电阻合计 420 kΩ，若用 2 kΩ/□ 高阻多晶硅，约为 210 方块。这里按名义密度估算，端部、边缘及间距需由目标器件确认。[SKY130 器件数据](https://skywater-pdk.readthedocs.io/en/main/rules/device-details.html)".format(500*r["receiver_cap_pF"]),""]
    (out/"RESULTS.md").write_text("\n".join(lines),encoding="utf-8")


def run(out,r,models,previous,xs,ng):
    out.mkdir(parents=True,exist_ok=False)
    save_json(out/"requirements.json",r)
    lib=verify(models) if models else prepare(out/"models")
    check('"' not in str(lib) and "\n" not in str(lib),"Model path cannot be represented in SPICE")
    command([ng,"-n","--version"],out,"ngspice-version.txt")
    identity={"model_lock_sha256":digest(MIRROR/"models.lock.json"),"library_sha256":digest(lib),
              "ngspice_version_sha256":digest(out/"ngspice-version.txt"),"ngspice_binary_sha256":digest(Path(ng))}
    recipe=dict(identity,model_deck_path=str(lib),model_revision=LOCK["revision"],xschem_binary_sha256=digest(Path(xs)),
                scripts={s:digest(HERE/s) for s in ("run.py","gain_native.py","gain_measure.py")},schema_version=1)
    save_json(out/"recipe.json",recipe)
    folder=out/"characterization"
    folder.mkdir()
    for state in STATES:
        rows,manifest=characterize(folder,lib,ng,state,identity,previous)
        print("MOS",state[0],manifest["mode"],manifest["points"],flush=True)
        if state[0]=="tt":
            sizing=design(rows,r)
    save_json(out/"sizing.json",sizing)
    reviews,areas,details={},{},{}
    for name in ("baseline","candidate"):
        review,readback,detail=variant(out,name,r,sizing,lib,xs,ng)
        reviews[name],areas[name],details[name]=review,readback["amplifier_gate_area_um2"],detail
    verify(lib.parent)
    comparison,status=selection(reviews,areas)
    save_json(out/"comparison.json",comparison)
    report(out,r,sizing,status,reviews,areas,details)
    save_json(out/"status.json",status)
    return status


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out",required=True,type=Path,help="A new output directory")
    cli.add_argument("--requirements",type=Path,default=HERE/"requirements.example.json")
    cli.add_argument("--models",type=Path,help="Previously prepared pinned SKY130 MOS model directory")
    cli.add_argument("--reuse-lut",type=Path,help="Previously generated characterization directory")
    cli.add_argument("--ngspice",default="ngspice")
    cli.add_argument("--xschem",default="xschem")
    args=cli.parse_args()
    try:
        xs,ng=shutil.which(args.xschem),shutil.which(args.ngspice)
        check(xs and ng,"Install Xschem and ngspice or specify their executables")
        outcome=run(args.out.resolve(),requirements(args.requirements),args.models.resolve() if args.models else None,
                    args.reuse_lut.resolve() if args.reuse_lut else None,str(Path(xs).resolve()),str(Path(ng).resolve()))
        print(json.dumps(outcome))
    except (OSError,ValueError) as error:
        cli.exit(2,str(error)+"\n")
    sys.exit(0 if outcome["selected"] else 1)
