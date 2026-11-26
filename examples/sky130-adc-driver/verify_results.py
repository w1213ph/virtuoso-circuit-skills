"""Recompute the sample errors and phase margins from a local or downloaded run without EDA software."""
import argparse
import json
import math
import re
from pathlib import Path

from run import MIRROR, STATES, budget, checks, digest, loop_metrics, requirements, tests_for, transient_metrics
from bench import deck
from driver import audit, check_sizing, checked_views, parent_pins, size
from mos_model import SKY130
from mos_table import LENGTHS
import replay_lut
import precision


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(value,message):
    if not value:
        raise ValueError(message)


def checked_tables(out, recipe, version):
    root = out / "characterization"
    if version < 3 and not root.exists():
        return None, {}, None
    identity = {key:recipe[key] for key in ("model_lock_sha256", "library_sha256",
                                             "ngspice_version_sha256", "ngspice_binary_sha256")}
    require(identity["model_lock_sha256"] == digest(MIRROR / "models.lock.json")
            and identity["ngspice_version_sha256"] == digest(out / "ngspice-version.txt"),
            "Device characterization uses a different model lock or simulator")
    # Retain the original model path as deck text. Offline replay never opens that path.
    original = (root / "tt/input.spice").read_text(encoding="utf-8")
    match = re.search(r'(?m)^\.lib "([^"\r\n]+)" tt$', original)
    require(match, "Missing characterized model library")
    library = match[1]
    counts = {}
    for state in STATES:
        corner = state[0]
        folder = root / corner
        # Old versions recorded the reader used at run time. Their curves and tabulated values are replayed below.
        reader = read(folder / "manifest.json")["recipe"]["reader_sha256"] if version < 3 else None
        rows = replay_lut.replay(folder, library, SKY130, state, LENGTHS, identity,
                                 modes=("measured", "reused"), reader=reader)
        counts[corner] = len(rows)
        if corner == "tt":
            tt_rows = rows
    return tt_rows, counts, library


def verify(out, *, detailed=False):
    r=requirements(out/"requirements.json")
    theory=budget(r)
    reviews,areas,variants={},{},{}
    fixture=read(out/"fixture.json")
    recipe=read(out/"recipe.json")
    version=recipe.get("schema_version",1)
    require(type(version) is int and version in (1,2,3,4),"Unsupported sampling-driver run format")
    optimize = version == 4
    tt_rows, points, library = checked_tables(out, recipe, version)
    require(read(out/"budget.json") == theory, "Sampling budget differs from the confirmed requirements")
    def measure(name,expected_size):
        stage=out/name
        f=stage/"prelayout.json"
        plan=checks.load_plan(f)
        expected=[t for c,temp,vdd in STATES for t in tests_for(c,temp,vdd,r,theory,include_diagnostics=version>=2,include_dc=optimize)]
        require(plan["tests"]==expected,"Test conditions differ from requirements: "+name)
        contexts=read(stage/"before-simulation.json")
        sizing=read(stage/"sizing.json")
        if tt_rows is not None:
            check_sizing(sizing,expected_size)
        views = checked_views(stage/"views", sizing, r, legacy=version < 3)
        inputs = ["sizing.json", "views/TB_ADRV_SAMPLE.spice"] + (views if version >= 3 else [])
        require(plan["inputs"] == inputs, "Simulation input list differs from the archived design: " + name)
        native=stage/"views/TB_ADRV_SAMPLE.spice"
        netlist = native.read_text(encoding="utf-8")
        sections=audit(netlist,sizing,r)
        body = "\n".join(row for row in netlist.splitlines() if not row.strip().startswith("**")) + "\n"
        back=read(stage/"readback.json")
        require(sections==back["native_sections"] and precision.same_fixture(sections,fixture,optimize and sizing["devices"]["XSN"][2]!=1),
                "Native hierarchy differs")
        pins=json.loads(json.dumps(parent_pins(stage/"views")))
        require(pins==back["parent_pins"],"Parent pin geometry differs")
        areas[name]=sum(w*l*m for n,(w,l,m) in sizing["devices"].items() if n in sections["ADRV_OTA"])
        require(math.isclose(areas[name],sizing["core_gate_area_um2"]),"Core area differs from native sizes")
        values={}
        measurements={}
        for corner,temp,vdd in STATES:
            folder=stage/corner
            dynamics=transient_metrics(folder/"track",r,vdd)
            measurements[corner]={"transient":dynamics}
            if optimize:
                dc = folder/"dc"
                require((dc/"input.spice").read_text(encoding="utf-8")==precision.dc_deck(body,library,(corner,temp,vdd),r),
                        "Static measurement has a different native driver, input range or clock: "+name+"/"+corner)
                static=precision.dc_transfer(dc,r,vdd)
                measurements[corner]["dc"]=static
                require(static==read(folder/"measurement.json")["dc"],"Static transfer does not match raw levels")
                values[corner+".dc.transfer"]=static["worst_abs_mV"]
            for edge,m in dynamics["edges"].items():
                for phase in ("track","hold"):
                    values[corner+"."+edge+"."+phase]=m[phase+"_error_mV"]
                values[corner+"."+edge+".switch_drop"]=m["switch_drop_mV"]
            for mode in ("track","hold"):
                if library is not None:
                    original = (folder/mode/"input.spice").read_text(encoding="utf-8")
                    require(original == deck(body, library, (corner,temp,vdd), r, mode=="track"),
                            "Simulation deck differs from the editable hierarchy, clock or PVT: " + name+"/"+corner+"/"+mode)
                measured=loop_metrics(folder/mode,vdd,mode=="track",r)
                values[corner+"."+mode+".margin"]=measured["phase_margin_deg"]
                measurements[corner][mode]=measured
        hashes={}
        for t in expected:
            idx=t["id"]
            corner,metric=idx.split(".",1)
            mode="hold" if metric=="hold.margin" else "track"
            record=plan["results"][idx]
            files={corner+"/measurement.json",corner+"/"+mode+"/ngspice.log",corner+"/"+mode+"/op.dat",
                   corner+"/"+mode+("/ac.dat" if t["analysis"]=="ac" else "/transient.dat")}
            if t["analysis"]=="dc":
                files={corner+"/measurement.json",corner+"/dc/ngspice.log",corner+"/dc/dc.dat"}
            require(set(record["evidence"])==files,"Wrong evidence files at "+idx)
            ctx=contexts[idx]
            for p in [*inputs,*t["inputs"]]:
                require(checks.file_hash(stage/p,hashes)==ctx["files"][p],"Changed simulation input: "+p)
            for p in ("requirements.json","budget.json","recipe.json","fixture.json","ngspice-version.txt"):
                require(checks.file_hash(out/p,hashes)==ctx["reference_files"]["../"+p],"Changed common input: "+p)
            require(math.isclose(values[idx],record["value"],rel_tol=1e-10,abs_tol=1e-9),"Raw measurement differs: "+idx)
        reviews[name]=checks.check(plan,stage,contexts,hashes)
        require(reviews[name]==read(stage/"checked.json") and all(
            t["status"] in (("DIAGNOSTIC_MEASURED",) if t.get("role")=="diagnostic" else ("PASS","FAIL"))
            for t in reviews[name]["tests"]),
                "Archived decisions or evidence differ: "+name)
        variants[name] = dict(sizing=sizing, measurements=measurements)
        if optimize:
            return precision.summarize(sizing,measurements,reviews[name])

    originals={name:(size(tt_rows,r,name=="candidate") if tt_rows is not None else None) for name in precision.ORIGINALS}
    search=precision.evaluate_family(originals,optimize,measure)
    comparison=checks.compare(reviews["baseline"],reviews["candidate"])
    require(comparison==read(out/"comparison.json"),"Comparison differs")
    passing=comparison["passing_variants"]
    pick=min(passing,key=areas.get) if passing else None
    if optimize:
        require(all(checks.compare(reviews["baseline"],v)["status"]=="COMPARABLE" for v in reviews.values()),
                "Changed sampling contract across optimized circuits")
        require(read(out/"optimization.json")==search,"Search results, static shortlist or areas differ")
        passing,pick=precision.selection(search)
    status={"status":"completed" if pick else "review_required","passing_variants":passing,"selected":pick}
    require(read(out/"status.json")==status,"Selected circuit differs")
    result = dict(status,characterization_points=points,pass_counts={v:sum(t["status"]=="PASS" for t in data["tests"]) for v,data in reviews.items()})
    if detailed:
        result.update(variants=variants, requirements=r, budget=theory)
        if optimize:
            result["optimization"]=search
    return result


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out",type=Path)
    args=cli.parse_args()
    try:
        print(json.dumps(verify(args.out),indent=2))
    except (OSError,ValueError,KeyError) as exc:
        cli.exit(1,str(exc)+"\n")
