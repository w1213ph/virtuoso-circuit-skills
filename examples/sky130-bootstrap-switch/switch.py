"""Build the editable sampling switches or independently replay their raw evidence."""
import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path

from switch_models import HERE, LOCK, check_extra, fetch_models, prepare_extra
from switch_design import STATES, budget, char_deck, char_results, design_set, require, requirements
from switch_measure import BASE_STEP_NS, measure, transient
from switch_views import OPENER, audit, build, parent_check, view_sources
from mos_table import save_json, simulate


def libs_at(folder,root):
    depth=len(folder.relative_to(root).parts)
    return ["../"*depth+"models/"+part for part in ("core/mirror.lib.spice","switch/switch.lib.spice")]


def comparable(a,b):
    if type(b) is float:return type(a) in (int,float) and math.isclose(a,b,rel_tol=2e-9,abs_tol=2e-8)
    if isinstance(b,dict):return isinstance(a,dict) and a.keys()==b.keys() and all(comparable(a[k],v) for k,v in b.items())
    if isinstance(b,list):return isinstance(a,list) and len(a)==len(b) and all(comparable(x,y) for x,y in zip(a,b))
    return type(a) is type(b) and a==b


def logged(folder):
    body=(folder/"ngspice.log").read_text(encoding="utf-8",errors="replace")
    require("analysis_complete" in body and not re.search(r"(?im)^\s*(?:fatal|error|warning)\b|simulation interrupted|timestep too small|no such vector",body),"Incomplete solver log in "+str(folder))


def verify_text(path,expected):
    require(path.read_text(encoding="utf-8")==expected,"Evidence differs from recomputation: "+str(path))


def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def snapshot(root):
    keys=("input.spice","wave.dat.gz","sweep.dat","ngspice.log","BSW_SWITCH.sch","TB_BSW_SAMPLE.spice","TB_BSW_SAMPLE.sch")
    return {p.relative_to(root).as_posix():digest(p) for group in ("characterization","designs","refinement")
            for p in sorted((root/group).rglob("*")) if p.is_file() and p.name in keys}


def characterize(root,r,solver):
    resistances=[]
    for state in STATES:
        folder=root/"characterization"/state[0];deck=char_deck(r,state,libs_at(folder,root))
        if solver:
            folder.mkdir(parents=True);simulate(solver,folder,deck)
        else:
            logged(folder);verify_text(folder/"input.spice",deck)
        curves,ron=char_results(folder/"sweep.dat",r,state)
        if solver:(folder/"devices.csv").write_text(curves,encoding="utf-8")
        else:verify_text(folder/"devices.csv",curves)
        resistances.append(ron)
    return design_set(r,resistances)


def run_case(root,folder,r,state,level,native,d,step,solver):
    deck=transient(r,state,level,native,d,libs_at(folder,root),step)
    if solver:
        folder.mkdir(parents=True);simulate(solver,folder,deck)
        raw=folder/"wave.dat"
        with raw.open("rb") as src,gzip.open(folder/"wave.dat.gz","wb",compresslevel=4) as dst:shutil.copyfileobj(src,dst)
        raw.unlink()
    else:
        verify_text(folder/"input.spice",deck);logged(folder)
    m,samples=measure(folder/"wave.dat.gz",r,state,level,d,step)
    if solver:
        (folder/"samples.csv").write_text(samples,encoding="utf-8");save_json(folder/"metrics.json",m)
    else:
        verify_text(folder/"samples.csv",samples)
        require(comparable(json.loads((folder/"metrics.json").read_text()),m),"Saved metrics differ from raw transient: "+str(folder))
    return m


def area_key(name,designs):
    d=designs[name];return (d["plate_um2"],d["gate_um2"],name)


def case_table(r):
    return [(s[0]+"-swing"+str(i+1),s,level) for s in STATES for i,level in enumerate(r["input_vpp_V"])]


def compact(m):
    return {k:m[k] for k in ("corner","input_vpp_V","thd_dBc","gain","phases","checks","violations","passed")}


def refine(root,r,d,native,cases,coarse,solver):
    """Resolve sharp switching edges in every swing and corner before a design can be selected."""
    result={}
    for key,state,level in cases:
        previous=coarse[key];steps=[]
        for factor in (2,4,8):
            step=BASE_STEP_NS/factor
            folder=root/"refinement"/d["name"]/key/("divide-"+str(factor))
            m=run_case(root,folder,r,state,level,native,d,step,solver)
            extrema=[(abs(a[e]-m["terminals"][part][pair][e]),part+"."+pair+"."+e)
                     for part,measured in previous["terminals"].items() for pair,a in measured.items() for e in ("min_V","max_V")]
            extrema.extend((abs(a[e]-m["plates"][part][e]),part+".plate."+e) for part,a in previous["plates"].items() for e in ("min_V","max_V"))
            dv,where=max(extrema);dd=abs(m["thd_dBc"]-previous["thd_dBc"])
            # Require successive solutions to agree on the individual pass/fail decisions as well as the numeric tolerances.
            stable=previous["checks"]==m["checks"] and set(previous["violations"])==set(m["violations"]) and dd<=.5 and dv<=.03
            outside=m["published_model_excursions"]
            pin=max(outside,key=lambda p:outside[p]["excess_V"]) if outside else None
            body=max(m["dc_model_forward_vbs"],key=lambda n:m["dc_model_forward_vbs"][n]["excess_V"])
            steps.append(dict(step_ns=step,thd_dBc=m["thd_dBc"],thd_change_dB=dd,max_terminal_extrema_change_V=dv,
                              limiting_terminal=where,checks=m["checks"],violations=m["violations"],stable=stable,passed=m["passed"] and stable,
                              published_excursion=dict(terminal=pin,**outside[pin]) if pin else None,
                              max_forward_vbs=dict(device=body,**m["dc_model_forward_vbs"][body])))
            if stable:break
            previous=m
        result[key]=steps
    worst=max(result,key=lambda k:result[k][-1]["thd_dBc"])
    return dict(cases=result,case=worst,passed_cases=sum(steps[-1]["passed"] for steps in result.values()),
                passed=all(steps[-1]["passed"] for steps in result.values()),thd_dBc=result[worst][-1]["thd_dBc"])


def evaluate(root,r,solver=None,xschem=None):
    designs,sizing=characterize(root,r,solver);variants={};records={};netlists={};cases=case_table(r)
    for name,d in designs.items():
        stage=root/"designs"/name;folder=stage/"views"
        if solver:
            native=build(folder,xschem,d,r);(stage/"open.sh").write_text(OPENER,encoding="utf-8")
        else:
            views=view_sources(d,r);parent_check(views)
            for file,content in views.items():verify_text(folder/file,content)
            verify_text(stage/"open.sh",OPENER)
            native=(folder/"TB_BSW_SAMPLE.spice").read_text(encoding="utf-8");audit(native,d,r)
        netlists[name]=native;rows={}
        for key,state,level in cases:
            rows[key]=run_case(root,stage/key,r,state,level,native,d,BASE_STEP_NS,solver)
        records[name]=rows
        worst=max(rows,key=lambda k:rows[k]["thd_dBc"])
        candidates=[(val["excess_V"],part,key,val["forward_peak_V"],val["published_forward_V"])
                    for key,m in rows.items() for part,val in m["dc_model_forward_vbs"].items()]
        excess,device,where,peak,limit=max(candidates)
        excursions=[(e["excess_V"],key,pin,e) for key,m in rows.items() for pin,e in m["published_model_excursions"].items()]
        model={"cases_outside":sum(bool(m["published_model_excursions"]) for m in rows.values()),"worst":None}
        if excursions:
            _,key,pin,e=max(excursions,key=lambda item:item[:3]);model["worst"]=dict(case=key,terminal=pin,**e)
        variants[name]=dict(cases={k:compact(m) for k,m in rows.items()},passed_cases=sum(m["passed"] for m in rows.values()),
            cases_total=len(rows),gate_um2=d["gate_um2"],plate_um2=d["plate_um2"],worst_thd_case=worst,
            worst_thd_dBc=rows[worst]["thd_dBc"],published_model=model,
            max_dc_vbs_excess=dict(excess_V=excess,device=device,case=where,forward_peak_V=peak,published_forward_V=limit))
        if solver:print(name,variants[name]["passed_cases"],"/",len(rows),flush=True)
    boosted=[n for n in variants if n!="transmission"]
    passing=sorted((n for n in boosted if variants[n]["passed_cases"]==len(cases) and sizing["within_rc_budget"]),
                   key=lambda n:area_key(n,designs))
    diagnostic=min(boosted,key=lambda n:(-variants[n]["passed_cases"],variants[n]["worst_thd_dBc"],area_key(n,designs)))
    refine_names=passing or [diagnostic]
    refined={};selected=None
    for name in refine_names:
        refined[name]=refine(root,r,designs[name],netlists[name],cases,records[name],solver)
        if solver:print("refinement",name,refined[name]["passed_cases"],"/",len(cases),flush=True)
        if refined[name]["passed"] and name in passing:
            selected=name;break
    if not selected:
        diagnostic=min(refined,key=lambda n:(-refined[n]["passed_cases"],refined[n]["thd_dBc"],area_key(n,designs)))
    return dict(requirements=r,sizing=sizing,variants=variants,refinement=refined,selected=selected,diagnostic=selected or diagnostic)


def overview(summary):
    r=summary["requirements"];selected=summary["selected"];ref=summary["diagnostic"];variants=summary["variants"]
    out=["# 采样开关前仿结果","",r["purpose"],"",
         ("工程指标通过，选中 `"+selected+"`。" if selected else "搜索范围内没有同时通过全部工程检查和步长复测的自举电路。"),
         "用于查看原理图和测试台的电路为 `"+ref+"`。在其目录用 `sh open.sh core` 或 `sh open.sh bench` 打开。","",
         "| 电路 | 初筛通过工况 | 初筛最差 H2–H5 THD (dBc) | 对应工况 | 总栅面积 (µm²) | MIM 极板面积 (µm²) |",
         "|---|---:|---:|---|---:|---:|"]
    for n,v in variants.items():
        out.append("| {} | {}/{} | {:.2f} | {} | {:.2f} | {:g} |".format(n,v["passed_cases"],v["cases_total"],v["worst_thd_dBc"],v["worst_thd_case"],v["gate_um2"],v["plate_um2"]))
    v=variants[ref];out+=["","## 各工况与失真","","| 工况 | 输入 Vpp | THD (dBc) | 基波增益 | 超限项 |","|---|---:|---:|---:|---|"]
    for key,m in v["cases"].items():
        failures=[k for k,ok in m["checks"].items() if not ok]
        out.append("| {} | {:g} | {:.2f} | {:.5f} | {} |".format(key,m["input_vpp_V"],m["thd_dBc"],m["gain"],", ".join(failures+m["violations"]) or "通过"))
    model=v["published_model"];e=model["worst"]
    if e:
        out+=["","按网表标定的 D/G/S/B 方向逐只比较公开模型 VGS、VDS、VBS 有效范围，{} 个工况出现越界。最大一处在 {} 的 {}，{:.2f} ns 测得 {:.4f} V，有效范围为 {} V。波形中的反向导通和时钟边沿也计入。".format(
            model["cases_outside"],e["case"],e["terminal"],e["time_ns"],e["observed_V"],e["range_V"])]
    dc=v["max_dc_vbs_excess"];f=summary["refinement"][ref];s=summary["sizing"]
    out+=["","厚栅主开关扫描选取 W={width_um:g} µm、L=0.5 µm，选中宽度的最差导通电阻为 {ron_ohm:.2f} Ω，分配的上限为 {target_ohm:.2f} Ω。".format(**s),
        "","## 细步长复测","","{} 的 {} 个工况中有 {} 个通过复测，最终最差 H2–H5 总失真为 {:.2f} dBc。每个工况逐次减半步长，并检查相邻两次的结论、失真和端压极值。".format(ref,v["cases_total"],f["passed_cases"],f["thd_dBc"]),
        "","| 工况 | 最终步长 (ns) | THD (dBc) | 前后差 (dB) | 端压最大差 (V) | 通过 |","|---|---:|---:|---:|---:|---|"]
    for key,steps in f["cases"].items():
        last=steps[-1]
        out.append("| {} | {:g} | {:.2f} | {:.3f} | {:.4f} | {} |".format(key,last["step_ns"],last["thd_dBc"],last["thd_change_dB"],last["max_terminal_extrema_change_V"],"是" if last["passed"] else "否"))
    out+=[
        "","工程瞬态结压预算为 {:g} V。另行比较模型文档的正向 VBS 范围，{} 的 {} 在 {} 测得 {:.4f} V，文档正向边界为 {:g} V，超出 {:.4f} V。端压记录含上电和开关边沿。".format(r["max_forward_V"],ref,dc["device"],dc["case"],dc["forward_peak_V"],dc["published_forward_V"],dc["excess_V"]),
        "","面积分别为 MOS 的 ΣW×L×并联数和各 MIM 的 ΣW×L。规定工况以外的工作范围、可靠性寿命和成品芯片性能仍需相应证据。",
        "","原始波形在各工况的 `wave.dat.gz`，128 个保持样本在 `samples.csv`，六个有符号 MOS 端压的极值和发生时刻在 `metrics.json`。"]
    return "\n".join(out)+"\n"


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__);sub=cli.add_subparsers(dest="action")
    run=sub.add_parser("run");run.add_argument("--requirements",type=Path,default=HERE/"requirements.example.json")
    run.add_argument("--out",required=True,type=Path);run.add_argument("--core-models",type=Path);run.add_argument("--switch-models",type=Path)
    run.add_argument("--ngspice",default="ngspice");run.add_argument("--xschem",default="xschem")
    verify=sub.add_parser("verify");verify.add_argument("--out",required=True,type=Path)
    args=cli.parse_args(argv)
    if args.action is None:cli.error("Choose run or verify")
    root=args.out.expanduser().resolve()
    try:
        if args.action=="run":
            r=requirements(args.requirements);require(not root.exists(),"Choose a new output directory")
            root.mkdir(parents=True);save_json(root/"requirements.json",r)
            fetch_models.prepare(root/"models/core",args.core_models);prepare_extra(root/"models/switch",args.switch_models)
            save_json(root/"model-identity.json",{"core":fetch_models.LOCK,"switch":LOCK})
            summary=evaluate(root,r,args.ngspice,args.xschem)
            save_json(root/"summary.json",summary);save_json(root/"raw-sha256.json",snapshot(root))
            (root/"RESULTS.md").write_text(overview(summary),encoding="utf-8")
        else:
            r=requirements(root/"requirements.json")
            require(json.loads((root/"model-identity.json").read_text())=={"core":fetch_models.LOCK,"switch":LOCK},"Model identity differs")
            if (root/"models").exists():fetch_models.verify(root/"models/core");check_extra(root/"models/switch")
            require(json.loads((root/"raw-sha256.json").read_text())==snapshot(root),"Raw evidence checksum differs")
            summary=evaluate(root,r)
            require(comparable(json.loads((root/"summary.json").read_text(encoding="utf-8")),summary),"Published decision differs from replay")
            verify_text(root/"RESULTS.md",overview(summary))
        print(overview(summary))
        return 0 if summary["selected"] else 1
    except (OSError,ValueError,KeyError,TypeError,IndexError,EOFError) as exc:
        print("Invalid switch evidence: "+str(exc),file=sys.stderr);return 2


if __name__=="__main__":
    sys.exit(main())
