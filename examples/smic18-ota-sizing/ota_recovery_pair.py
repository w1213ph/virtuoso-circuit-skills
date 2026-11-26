"""Measure the CM feedback change on identical mismatch draws and identical held trim codes."""
import argparse
from collections import Counter
from pathlib import Path
import re

from ota import config_for_site
from ota_cmfb import ICM, exact_swing, persist
from ota_mismatch import coverage
from ota_native import exported, hierarchy
from ota_trim import applied
from ota_trim_stats import bank_run, held_measurements, score, select_codes
from ota_views import CMFB, TB
from bias_sim import STATES
from spectre_run import read_json, sha256


def control_fixture(native,recovered):
    """Two noiseless bench sources switch CM topology while retaining every statistical device."""
    devices=hierarchy(native)[0][CMFB][1]
    if (devices["MCM"][0]!=["PG","RCG","VDD","VDD"] or set(devices["RREC"][0])!={"PG","RCG"}
            or not devices["RREC"][1].startswith("rhrpo") or "ABGATE" in native or "ABRES" in native):
        raise ValueError("Expected the native bridged CMFB with unused test-node names")
    changed,count=re.subn(r"(?m)^(\s*MCM \(PG )RCG( VDD VDD\))",r"\1ABGATE\2",native)
    if count!=1:
        raise ValueError("Cannot identify the CM load gate")
    changed,count=re.subn(r"(?m)^([ \t]*RREC \()([^\n)]+)(\))",
                          lambda m:m[1]+" ".join("ABRES" if name=="RCG" else name for name in m[2].split())+m[3],changed)
    if count!=1:
        raise ValueError("Cannot identify the physical feedback resistor")
    marker="ends "+CMFB
    block=("EABG (ABGATE PG RCG PG) vcvs gain={}\nEABR (ABRES RCG PG RCG) vcvs gain={}\n"
           .format(int(recovered),int(not recovered)))
    changed,count=re.subn("(?m)^"+marker+"$",lambda m:block+marker,changed)
    if count!=1:
        raise ValueError("Cannot identify the CMFB subcircuit boundary")
    return changed


def compare_fixtures(root,nominals,statistical,ordinary,site,fresh):
    methods=("diode","bridge")
    if fresh:
        root.mkdir(parents=True,exist_ok=True)
    fixed=nominals["bridge"]["fixed_code"]
    if nominals["diode"]["fixed_code"]!=fixed:
        raise ValueError("Unvaried reference codes differ")
    nets={method:control_fixture(statistical,method=="bridge") for method in methods}
    controls={}
    for method in methods:
        control=applied(control_fixture(ordinary,method=="bridge"),fixed)
        for st in STATES:
            for icm in ICM:
                label="{}-{}".format(st[0],round(icm*1000))
                folder=root/(method+"-control-"+label)
                measured=exact_swing(folder,control,st,site,fresh and not folder.exists(),icm)
                target=nominals[method]["curves"][label][str(fixed)]["points"]
                cm=max(abs(m["cm_error_mV"]-r["cm_error_mV"]) for m,r in zip(measured,target))
                error=max(abs(1000*(m["output_p_V"]-m["output_n_V"]-m["command_V"])-r["signed_error_mV"]) for m,r in zip(measured,target))
                power=max(abs(m["power_uW"]-r["power_uW"]) for m,r in zip(measured,target))
                head=max(abs(1000*m["headroom_V"]-r["headroom_mV"]) for m,r in zip(measured,target))
                if max(cm,error,head)>.02 or power>.01:
                    raise ValueError("A statistical-control circuit does not reproduce its physical DC operating point")
                controls[method+"-"+label]=dict(cm_mV=cm,differential_mV=error,headroom_mV=head,power_uW=power)
    center=next(st for st in STATES if st[0]=="tt")
    def run(method,st,icm,code):
        folder=root/("{}-{}-{}-code{}".format(method,st[0],round(icm*1000),code))
        return bank_run(folder,nets[method],st,icm,site,code,fresh and not folder.exists())
    bank={code:run("bridge",center,ICM[0],code) for code in range(16)}
    coverage(bank)
    errors={}
    for code,r in bank.items():
        if any(abs(x[1]["command_V"])>1e-9 for x in r["samples"]):
            raise ValueError("Calibrate only at zero differential command")
        errors[code]={n:v[1]["signed_diff_error_mV"] for n,v in zip(r["ids"],r["samples"])}
    frozen=select_codes(errors,4)
    freeze=root/"held.json"
    persist(freeze,dict(codes=frozen,reference="bridge-tt-950-zero",
                        jobs={str(c):r["job_sha256"] for c,r in bank.items()}),fresh and not freeze.exists())
    chosen=sorted(set(frozen.values())|{fixed})
    outputs={}
    for method in methods:
        by_code,uniform,jobs={},{},{}
        for st in STATES:
            for icm in ICM:
                label="{}-{}".format(st[0],round(icm*1000))
                results={c:(bank[c] if method=="bridge" and st==center and icm==ICM[0] else run(method,st,icm,c)) for c in chosen}
                coverage(dict(results,reference=bank[0]))
                values={c:dict(zip(r["ids"],r["samples"])) for c,r in results.items()}
                by_code[label]=held_measurements(frozen,values)
                uniform[label]=values[fixed]
                jobs[label]={str(c):r["job_sha256"] for c,r in results.items()}
        outputs[method]=dict(held=score(by_code),uniform=score(uniform),jobs=jobs)
    transitions={}
    for key in ("uniform","held"):
        rows=[outputs[m][key]["draws"] for m in methods]
        pairs=Counter((not rows[0][d]["failed"],not rows[1][d]["failed"]) for d in frozen)
        transitions[key]=dict(both_pass=pairs[True,True],gained=pairs[False,True],lost=pairs[True,False],both_fail=pairs[False,False])
    return dict(matched_draws=len(frozen),controls=controls,reference_code=fixed,held_sha256=sha256(freeze),arms=outputs,paired=transitions)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("run","verify"))
    for k in ("config","baseline","recovery","statistics","out"):
        p.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    site=config_for_site(a.config)
    site["model_sha256"]=sha256(site["model_file"])
    site["model_file"]=str(site["model_file"])
    nominal={k:read_json(path/"summary.json") for k,path in (("diode",a.baseline),("bridge",a.recovery))}
    if (nominal["diode"].get("recovery") or set(nominal["bridge"].get("recovery",{}))!={"plate_um","cm_bridge_ohm"}
            or nominal["diode"]["bank"]!=nominal["bridge"]["bank"]
            or read_json(a.statistics/"summary.json")["nominal_sha256"]!=sha256(a.recovery/"summary.json")):
        raise ValueError("Expected the native baseline and its bridged derivative with the same trim devices")
    stat=exported(a.statistics/"statistical-oa",TB)
    ordinary=exported(a.recovery/"oa",TB)
    fresh=a.action=="run"
    report=compare_fixtures(a.out,nominal,stat,ordinary,site,fresh)
    report["native_sha256"]={k:sha256(v/"summary.json") for k,v in (("diode",a.baseline),("bridge",a.recovery),("statistics",a.statistics))}
    dest=a.out/"summary.json"
    persist(dest,report,fresh and not dest.exists())
    print("Identical stored physical draws",report["matched_draws"],"paired differences",report["paired"])


if __name__=="__main__":
    main()
