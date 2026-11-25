"""Recompute the sample errors and phase margins from a local or downloaded run without EDA software."""
import argparse
import json
import math
from pathlib import Path

from run import STATES, budget, checks, digest, loop_metrics, requirements, tests_for, transient_metrics
from driver import audit, parent_pins


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(value,message):
    if not value:
        raise ValueError(message)


def verify(out):
    r=requirements(out/"requirements.json")
    theory=budget(r)
    reviews,areas={},{}
    fixture=read(out/"fixture.json")
    for name in ("baseline","candidate"):
        stage=out/name
        f=stage/"prelayout.json"
        plan=checks.load_plan(f)
        expected=[t for c,temp,vdd in STATES for t in tests_for(c,temp,vdd,r,theory)]
        require(plan["tests"]==expected,"Test conditions differ from requirements: "+name)
        contexts=read(stage/"before-simulation.json")
        sizing=read(stage/"sizing.json")
        native=stage/"views/TB_ADRV_SAMPLE.spice"
        sections=audit(native.read_text(encoding="utf-8"),sizing,r)
        back=read(stage/"readback.json")
        require(sections==back["native_sections"] and {k:sections[k] for k in fixture}==fixture,"Native hierarchy differs")
        pins=json.loads(json.dumps(parent_pins(stage/"views")))
        require(pins==back["parent_pins"],"Parent pin geometry differs")
        areas[name]=sum(w*l*m for n,(w,l,m) in sizing["devices"].items() if n in sections["ADRV_OTA"])
        require(math.isclose(areas[name],sizing["core_gate_area_um2"]),"Core area differs from native sizes")
        values={}
        for corner,_,vdd in STATES:
            folder=stage/corner
            dynamics=transient_metrics(folder/"track",r,vdd)
            for edge,m in dynamics["edges"].items():
                for phase in ("track","hold"):
                    values[corner+"."+edge+"."+phase]=m[phase+"_error_mV"]
            for mode in ("track","hold"):
                measured=loop_metrics(folder/mode,vdd,mode=="track",r)
                values[corner+"."+mode+".margin"]=measured["phase_margin_deg"]
        hashes={}
        for t in expected:
            idx=t["id"]
            corner,metric=idx.split(".",1)
            mode="hold" if metric=="hold.margin" else "track"
            record=plan["results"][idx]
            files={corner+"/measurement.json",corner+"/"+mode+"/ngspice.log",corner+"/"+mode+"/op.dat",
                   corner+"/"+mode+("/ac.dat" if t["analysis"]=="ac" else "/transient.dat")}
            require(set(record["evidence"])==files,"Wrong evidence files at "+idx)
            ctx=contexts[idx]
            for p in ["sizing.json","views/TB_ADRV_SAMPLE.spice",*t["inputs"]]:
                require(checks.file_hash(stage/p,hashes)==ctx["files"][p],"Changed simulation input: "+p)
            for p in ("requirements.json","budget.json","recipe.json","fixture.json","ngspice-version.txt"):
                require(checks.file_hash(out/p,hashes)==ctx["reference_files"]["../"+p],"Changed common input: "+p)
            require(math.isclose(values[idx],record["value"],rel_tol=1e-10,abs_tol=1e-9),"Raw measurement differs: "+idx)
        reviews[name]=checks.check(plan,stage,contexts,hashes)
        require(reviews[name]==read(stage/"checked.json") and all(t["status"] in ("PASS","FAIL") for t in reviews[name]["tests"]),
                "Archived decisions or evidence differ: "+name)
    comparison=checks.compare(reviews["baseline"],reviews["candidate"])
    require(comparison==read(out/"comparison.json"),"Comparison differs")
    passing=comparison["passing_variants"]
    pick=min(passing,key=areas.get) if passing else None
    status={"status":"completed" if pick else "review_required","passing_variants":passing,"selected":pick}
    require(read(out/"status.json")==status,"Selected circuit differs")
    return dict(status,pass_counts={v:sum(t["status"]=="PASS" for t in data["tests"]) for v,data in reviews.items()})


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out",type=Path)
    args=cli.parse_args()
    try:
        print(json.dumps(verify(args.out),indent=2))
    except (OSError,ValueError,KeyError) as exc:
        cli.exit(1,str(exc)+"\n")
