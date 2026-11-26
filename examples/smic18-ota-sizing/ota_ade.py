"""Save a native ADE test matrix and replay the selected OTA from its completed Maestro history."""
import math
import re
from pathlib import Path
import sqlite3
from contextlib import closing
import xml.etree.ElementTree as ET

from bias_ade import matches_options, options
from maestro import device_lines, history_tests
from schematic import q
from spectre_run import execute, sha256
from ota_measure import MODES, NODES, OP_SAVES, performance
from ota_views import LIB, TB


TRAN_OUTPUTS = {"DIFF_V":'(VT("/OUTP") - VT("/OUTN"))',"CM_V":'((VT("/OUTP") + VT("/OUTN")) / 2)'}


def settings(mode):
    if mode=="dc":
        return "dc", {"saveOppoint":"t"}, {"SUPPLY_UW":'-1e6 * getData("VDD" ?result "dc") * getData("VSUP:p" ?result "dc")'}
    if mode=="tran":
        return "tran", dict(stop="10u", maxstep="2n", strobeperiod="2n", strobeoutput="strobeonly",
                            errpreset="conservative"), {"DIFF_V":'VT("/OUTP")-VT("/OUTN")', "CM_V":'(VT("/OUTP")+VT("/OUTN"))/2'}
    freq = dict(start="1", stop="1G", dec="60", incrType="Logarithmic")
    if mode=="gain":
        return "ac", freq, {"DIFF_GAIN_DB":'db20((VF("/OUTP")-VF("/OUTN"))/(VF("/INP")-VF("/INN")))'}
    freq["probe"] = "/PDM" if mode=="dm" else "/XDUT/PCM"
    return "stb", freq, {"RETURN_DB":'db20(getData("loopGain" ?result "stb"))'}


def save_file(mode):
    return "simulator lang=spectre\nsave "+" ".join(NODES+(OP_SAVES if mode=="dc" else ()))+"\n"


def pairs(values):
    return "(" + " ".join("("+q(k)+" "+q(v)+")" for k,v in values.items()) + ")"


def test_groups(states, input_levels):
    for state in states:
        corner,_,supply = state
        for icm in input_levels or (None,):
            key = corner if icm is None else "{}-{}mV".format(corner,round(icm*1000))
            tests = {}
            for mode in MODES if icm is None else ("tran",):
                variables = dict(VDDVAL="{:g}".format(supply),BETA="0" if mode=="gain" else "1")
                name = (corner+"_"+mode).upper()
                if icm is not None:
                    variables["ICMGAIN"] = "{:.15g}".format(icm/supply)
                    name = "{}_FULL_{}".format(corner.upper(),round(icm*1000))
                tests[mode] = (name,variables)
            yield key,state,icm,tests


def launch(stage, site, states, input_levels=()):
    folder = stage/"maestro-evidence"
    folder.mkdir()
    dest = stage/"maestro-results"
    dest.mkdir()
    for mode in ("tran",) if input_levels else MODES:
        (folder/(mode+".scs")).write_text(save_file(mode), encoding="utf-8")
    rows = []
    for _key,(corner,temp,_supply),_icm,tests in test_groups(states,input_levels):
        for mode,(name,variables) in tests.items():
            ana, opts, outputs = settings(mode)
            values = [q(name),q(ana),q(corner),q(str(temp)),pairs(opts),
                      q(str(folder/(mode+".scs"))),pairs(variables),pairs(outputs)]
            rows.append("("+" ".join(values)+")")
    prefix = "\n".join(k+"="+q(str(v)) for k,v in dict(fdLib=LIB,fdTB=TB,fdSpectre=site["spectre"],
                 fdModel=site["model_file"],fdReadback=folder/"maestro-readback.txt",
                 fdHistory=folder/"maestro-history.txt").items())+"\n"
    prefix += "fdTests='("+"\n".join(rows)+")\n"
    prefix += 'envSetVal("asimenv.startup" "projectDir" \'string {})\n'.format(q(str(dest)))
    prefix += 'envSetVal("adexl.historyNamePrefix" "showNameHistoryForm" \'boolean nil)\n'
    prefix += "load({})\n".format(q(str(Path(__file__).resolve().with_name("ota_ade.il"))))
    for phase,proc in (("create","fdConfigure"),("run","fdRun")):
        script = folder/(phase+".il")
        script.write_text(prefix+"unless(errset({}() t) exit(3)) exit(0)\n".format(proc), encoding="utf-8")
        execute([site["virtuoso"],"-nograph","-log",folder/(phase+".log"),"-restore",script],stage,"maestro-"+phase+".stdout",900)


def check_transient_outputs(test):
    observed = {}
    for item in test.findall('.//field[@Type="defstruct"]'):
        fields = {f.get("Name"):f.text for f in item.findall("field")}
        label = (fields.get("name") or "").strip('"')
        if label in TRAN_OUTPUTS:
            if label in observed:
                raise ValueError("Repeated saved Maestro output "+label)
            observed[label] = fields.get("expression")
    if observed!=TRAN_OUTPUTS:
        raise ValueError("Incorrect saved differential or common-mode expression in "+test.get("Name",""))


def review(stage, site, states, direct, input_levels=(), transient_measure=None):
    matrix = list(test_groups(states,input_levels))
    names = [name for _key,_state,_icm,tests in matrix for name,_variables in tests.values()]
    transients = {tests["tran"][0] for _key,_state,_icm,tests in matrix}
    history, jobs = history_tests(stage,names,LIB,TB)
    evidence = stage/"maestro-evidence"
    readback = (evidence/"maestro-readback.txt").read_text(encoding="utf-8")
    native = (stage/TB/"netlist").read_text(encoding="utf-8")
    view = stage/LIB/TB/"maestro"
    for state in (view/"active.state",view/"test_states"/(history+".state")):
        saved_tests = ET.parse(str(state)).findall("./Test")
        if sorted(t.get("Name") for t in saved_tests)!=sorted(names):
            raise ValueError("Incomplete saved Maestro test matrix")
        for test in saved_tests:
            if test.get("Name") in transients:
                check_transient_outputs(test)
    # A history is complete only if its configured outputs were actually evaluated.
    query = """SELECT t.name,r.name,v.value,v.errorID,r.expression FROM result r JOIN test t USING(testID)
               JOIN resultValue v USING(resultID)"""
    rdb = view/"results/maestro"/(history+".rdb")
    with closing(sqlite3.connect(rdb.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        plotted = {(test,name):(value,error,expr) for test,name,value,error,expr in db.execute(query)}
    records = {}
    for key,state,icm,tests in matrix:
        corner,temp,supply = state
        paths = {}
        for mode,(name,variables) in tests.items():
            job = jobs[name]
            p = job/"netlist/input.scs"
            native_deck = p.read_text(encoding="utf-8")
            flat = re.sub(r"\\\r?\n\s*", " ",native_deck)
            if device_lines(native_deck)!=device_lines(native):
                raise ValueError("ADE circuit differs from native SI for "+name)
            # The PDK declares local subcircuit parameters as well as the two testbench parameters.
            design_vars = [s for s in flat.splitlines() if s.startswith("parameters ") and "VDDVAL=" in s]
            if len(design_vars)!=1:
                raise ValueError("Missing ADE bench variables: "+name)
            matches_options(options(design_vars[0],"parameters"),variables)
            matches_options(options(flat,"simulatorOptions options"),dict(temp=str(temp),reltol="1e-5",vabstol="1e-7",iabstol="1e-13"))
            ana,_,outputs = settings(mode)
            solver_opts = dict(re.findall(r"(\w+)=([^\s]+)",MODES[mode][0]))
            if solver_opts:
                matches_options(options(flat,ana+" "+ana),solver_opts)
            includes = re.findall(r'(?m)^\s*include\s+"([^"]+)"(?:\s+section=(\w+))?[ \t]*$',flat)
            saves = evidence/(mode+".scs")
            if includes != [(site["model_file"],c) for c in (corner,"res_"+corner,"mim_"+corner)]+[(str(saves),"")]:
                raise ValueError("Incorrect ADE MOS, resistor, MIM or signal include in "+name)
            if saves.read_text(encoding="utf-8")!=save_file(mode) or not any(t.startswith(name+" ") and
                    all('"'+c+'"' in t for c in (corner,"res_"+corner,"mim_"+corner)) for t in readback.splitlines()):
                raise ValueError("Saved signals or reopened corner differ for "+name)
            for out in outputs:
                value,error,expr = plotted.get((name,out),(None,"missing",None))
                if error is not None or value is None or (mode!="dc" and value!="wave"):
                    raise ValueError("Unevaluated ADE output "+name+"/"+out+": "+str((value,error)))
                if mode=="tran" and expr!=TRAN_OUTPUTS[out]:
                    raise ValueError("The Maestro history evaluated a different voltage expression for "+name+"/"+out)
                if mode=="dc" and not math.isclose(float(value), direct[corner]["power_uW"], rel_tol=.002):
                    raise ValueError("Displayed ADE power differs from the measured supply power")
            log = (job/"psf/spectre.out").read_text(encoding="utf-8", errors="replace")
            if not re.search(r"spectre completes with 0 errors?, 0 warnings?",log,re.I):
                raise ValueError("Review native Maestro solver diagnostics in "+name)
            trace = MODES[mode][1]
            found = [job/"psf"/n for n in ((trace,"tran.tran.tran") if mode=="tran" else (trace,)) if (job/"psf"/n).is_file()]
            if len(found)!=1:
                raise ValueError("Missing or ambiguous native ADE result for "+name)
            paths[mode]=found[0]
            records[name]=dict(raw_sha256=sha256(found[0]), deck_sha256=sha256(p), outputs=sorted(outputs))
        reevaluated = performance(paths,state) if icm is None else transient_measure(paths["tran"],supply,icm)
        def compare(first,second):
            if isinstance(first,dict):
                return isinstance(second,dict) and first.keys()==second.keys() and all(compare(first[k],second[k]) for k in first)
            if isinstance(first,list):
                return isinstance(second,list) and len(first)==len(second) and all(compare(a,b) for a,b in zip(first,second))
            if type(first) is float:
                return math.isclose(first,second,abs_tol=2e-5,rel_tol=.002)
            return first==second
        if not compare(reevaluated,direct[key]):
            raise ValueError("Native Maestro and direct Spectre measurements differ in "+key)
    return dict(history=history, tests=records)
