"""Recompute all four gain codes and both switch transitions from a downloaded run."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import sys

from run import (LOCK, MIRROR, REFERENCES, STATES, check, checks, decks, design, digest, inputs,
                 observations, read_json, required_sweeps, requirements, result, selection, tests)
from gain_native import audit, parent_leads


def table_rows(path):
    with path.open(encoding="utf-8",newline="") as source:
        return [{k:(v if k=="polarity" else float(v) if v else None) for k,v in row.items()} for row in csv.DictReader(source)]


def verify(out):
    r=requirements(out/"requirements.json")
    recipe=read_json(out/"recipe.json")
    check(recipe.get("schema_version")==1 and recipe["model_revision"]==LOCK["revision"] and
          recipe["model_lock_sha256"]==digest(MIRROR/"models.lock.json"),"Unsupported recipe or model revision")
    check(recipe["ngspice_version_sha256"]==digest(out/"ngspice-version.txt"),"Changed solver identity")
    for corner,_,_ in STATES:
        char=out/"characterization"/corner
        manifest=read_json(char/"manifest.json")
        for name in ("input.spice","table.csv","ngspice.log"):
            check(digest(char/name)==manifest["files"][name],"Changed characterization evidence: "+corner+"/"+name)
        check(all(manifest["recipe"][k]==recipe[k] for k in ("model_lock_sha256","library_sha256","ngspice_binary_sha256",
              "ngspice_version_sha256")),"Inconsistent characterization recipe")
    sizing=read_json(out/"sizing.json")
    calculated=design(table_rows(out/"characterization/tt/table.csv"),r)
    check(sizing.keys()==calculated.keys() and all(
          math.isclose(v,calculated[k],rel_tol=1e-10) if isinstance(v,float) else v==calculated[k]
          for k,v in sizing.items()),"Sizing differs from the measured TT lookup")
    reviews,areas={},{}
    for name in ("baseline","candidate"):
        stage=out/name
        views=stage/"views"
        native,geometry=audit((views/"TB_PGA_GAIN.spice").read_text(encoding="utf-8"),sizing,name,r)
        geometry["parent_leads"]=parent_leads(views)
        check(read_json(stage/"readback.json")==json.loads(json.dumps(geometry)),"Native readback differs")
        areas[name]=geometry["amplifier_gate_area_um2"]
        for directory,content in decks(r,native,recipe["model_deck_path"]):
            path=stage/directory
            check((path/"input.spice").read_text(encoding="utf-8")==content,"The simulated deck differs from the native circuit: "+directory)
            log=(path/"ngspice.log").read_text(encoding="utf-8",errors="replace")
            check("analysis_complete" in log and not re.search(r"(?im)^\s*(?:fatal|error|warning)\b|simulation interrupted|timestep too small",log),
                  "Incomplete solver run: "+directory)
        hashes={}
        plan,contexts=checks.load_records(stage/"prelayout.json",hashes)
        check(plan["inputs"]==inputs(stage) and plan["reference_inputs"]==REFERENCES and
              plan.get("required_sweeps")==required_sweeps() and plan["tests"]==tests(r),
              "The recorded tests do not cover the specified modes, receiving load and conditions")
        check(contexts==read_json(stage/"before-simulation.json"),"Inputs changed since the recorded run")
        data,_=observations(stage,r)
        for t in plan["tests"]:
            idx=t["id"]
            expected=result(t,contexts[idx],*data[idx])
            saved=plan["results"].get(idx)
            check(saved is not None and saved.keys()==expected.keys(),"Missing or incomplete measurement "+idx)
            check(all(math.isclose(saved[k],v,rel_tol=1e-10,abs_tol=1e-9) if k=="value" else saved[k]==v
                  for k,v in expected.items()),"Raw evidence differs at "+idx)
        reviews[name]=checks.check(plan,stage,contexts,hashes)
        check(reviews[name]==read_json(stage/"checked.json"),"Different recorded verdict for "+name)
    comparison,status=selection(reviews,areas)
    check(comparison==read_json(out/"comparison.json") and status==read_json(out/"status.json"),"Selected circuit differs")
    return dict(status,pass_counts={n:sum(t["status"]=="PASS" for t in rev["tests"]) for n,rev in reviews.items()},
                acceptance_per_design=sum(t.get("role")!="diagnostic" for t in tests(r)),gate_area_um2=areas)


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out",type=Path)
    args=cli.parse_args()
    try:
        print(json.dumps(verify(args.out.resolve()),indent=2))
    except (OSError,ValueError,KeyError,TypeError) as error:
        cli.exit(1,str(error)+"\n")
