"""Measure differential gain, two closed-loop return ratios, supply power and settling from PSF ASCII."""
import bisect
import cmath
import math
import re
from pathlib import Path

from bias_sim import analysis, model_header
from spectre_run import read_json, sha256


LIMITS = dict(gain_dB=80.0, phase_margin_deg=55.0, cm_error_mV=25.0, settling_us=1.0)
NODES = ("VDD","VCM","CMD","INP","INN","OUTP","OUTN","XDUT.BN","VSUP:p")
BRANCHES = {"XI":("MTAIL","MINP","MINN","MLP","MLN"), "XO":("MOP","MON","MSP","MSN"),
            "XB":("MREF",), "XC":("MCM","MDMY","MDETP","MDETN","MCREFP","MCREFN","MCTP","MCTN")}
OP_DEVICES = tuple("XDUT."+block+"."+name for block,names in BRANCHES.items() for name in names)
OP_FIELDS = ("id","vgs","vds","vdsat","gm","gds")
OP_SAVES = tuple(name+":"+k for name in OP_DEVICES for k in OP_FIELDS)
MODES = {
    "gain": ("ac ac start=1 stop=1G dec=60", "ac.ac"),
    "dc": ("dc dc", "dc.dc"),
    "dm": ("stb stb start=1 stop=1G dec=60 probe=PDM", "stb.stb"),
    "cm": ("stb stb start=1 stop=1G dec=60 probe=XDUT.PCM", "stb.stb"),
    "tran": ("tran tran stop=10u maxstep=2n strobeperiod=2n strobeoutput=strobeonly errpreset=conservative", "tran.tran"),
}


def deck(site, state, native, mode):
    header = model_header(site,state)
    header[3:3] = ['include "{}" section={}_{}'.format(site["model_file"],kind,state[0]) for kind in ("res","mim")]
    return "\n".join(header + [
        "parameters VDDVAL={:g} BETA={}".format(state[2], 0 if mode=="gain" else 1),
        native, "saveOptions options save=selected", "save " + " ".join(NODES + (OP_SAVES if mode=="dc" else ())),
        MODES[mode][0]]) + "\n"


def samples(path, axis, signals):
    text = path.read_text(encoding="utf-8")
    if text.count("\nVALUE\n") != 1 or not text.rstrip().endswith("END"):
        raise ValueError("Incomplete PSF data section: " + str(path))
    values = text.split("\nVALUE\n")[1].rsplit("\nEND",1)[0]
    # Scalar OP values may carry an inline PSF property block with their units.
    values = re.sub(r' PROP\(\n(?:"[^"]+" [^\n]+\n)+\)', "", values)
    rows, row = [], {}
    for line in values.splitlines():
        m = re.fullmatch(r'"([^"]+)"\s+(?:"[^"]+"\s+)?(.+)', line.strip())
        if not m:
            raise ValueError("Invalid PSF sample: " + line)
        name, token = m.groups()
        if axis and name == axis and row:
            rows.append(row)
            row = {}
        if name not in signals and name != axis:
            continue
        if name in row or (axis and not row and name != axis):
            raise ValueError("Misaligned or repeated PSF point: " + name)
        if token.startswith("(") and token.endswith(")"):
            value = complex(*map(float,token[1:-1].split()))
        else:
            value = float(token)
        if not (math.isfinite(value.real) and math.isfinite(value.imag)):
            raise ValueError("Non-finite PSF value: " + name)
        row[name] = value
    rows.append(row)
    names = tuple(signals) + ((axis,) if axis else ())
    if any(set(r)!=set(names) for r in rows) or (not axis and len(rows)!=1):
        raise ValueError("Missing PSF signal or axis: " + str(path))
    data = {n:[r[n] for r in rows] for n in names}
    if axis:
        grid = data[axis]
        if any(a>=b for a,b in zip(grid,grid[1:])):
            raise ValueError("PSF axis must strictly increase")
        if axis=="freq":
            if len(grid)!=541 or any(not math.isclose(f,10**(i/60),rel_tol=6e-6) for i,f in enumerate(grid)):
                raise ValueError("Incomplete 1 Hz to 1 GHz frequency sweep")
        elif axis=="time":
            if len(grid)!=5001 or any(abs(t-2e-9*i)>1e-14 for i,t in enumerate(grid)):
                raise ValueError("Incomplete 0 to 10 us step response")
    return data


def unity(freq, values):
    db, phase = [], []
    for z in values:
        db.append(20 * math.log10(max(1e-300,abs(z))))
        angle = math.degrees(cmath.phase(z))
        if phase:
            angle += 360 * round((phase[-1]-angle)/360)
        phase.append(angle)
    crossings = []
    for i,(a,b) in enumerate(zip(db,db[1:])):
        if (a>=0>b) or (a<0<=b):
            t = a/(a-b)
            crossings.append(dict(frequency_Hz=freq[i]*(freq[i+1]/freq[i])**t,
                                  margin_deg=180+phase[i]+t*(phase[i+1]-phase[i]), descending=a>b))
    valid = len(crossings)==1 and crossings[0]["descending"] and abs(phase[0])<10 and db[0]>0
    return dict(low_frequency_gain_dB=db[0], crossings=crossings,
                phase_margin_deg=crossings[0]["margin_deg"] if valid else None)


def settling(times, values, target, tolerance, edge, stop):
    begin, end = bisect.bisect_left(times,edge-1e-15), bisect.bisect_right(times,stop+1e-15)
    last = next((i for i in range(end-1,begin-1,-1) if abs(values[i]-target)>tolerance), begin-1)
    if last+1>=end:
        return None
    return max(0.0,(times[last+1]-edge)*1e6)


def transient(path, supply):
    d = samples(path,"time",NODES)
    t = d["time"]
    op, on = d["OUTP"], d["OUTN"]
    cm = [(a+b)/2 for a,b in zip(op,on)]
    diff = [a-b for a,b in zip(op,on)]
    def at(us):
        return round(us*500)
    # Validate both stimuli and their timing independently from the DUT result.
    for us,cmd,off in ((2,-.05,0), (3.998,-.05,0), (4.006,.05,0), (5.99,.05,0),
                       (6.012,-.05,0), (7.998,-.05,0), (8.006,-.05,.02),(10,-.05,.02)):
        i=at(us)
        if max(abs(d["CMD"][i]-cmd), abs(d["VDD"][i]-supply), abs(d["VCM"][i]-supply/2-off))>1e-5:
            raise ValueError("Applied transient stimuli differ at {:g} us".format(us))
    if abs(d["VDD"][0])>1e-5 or abs(d["VDD"][at(.5)] - supply/2)>1e-4:
        raise ValueError("Missing supply startup ramp")
    # Both differential edges use absolute targets. CM uses a pre-step baseline so its
    # incremental settling and the separate absolute DC common-mode error remain visible.
    baseline = cm[at(7.95)]
    startup = abs(diff[at(3.95)] + .05)<=.001 and abs(cm[at(3.95)]-supply/2)<=.025
    return dict(rise_us=settling(t,diff,.05,.001,4e-6,5.99e-6),
                fall_us=settling(t,diff,-.05,.001,6.005e-6,7.99e-6),
                cm_step_us=settling(t,cm,baseline+.020,.002,8e-6,10e-6),
                cm_step_mV=1000*(cm[-1]-baseline), startup_passed=startup,
                rise_overshoot_mV=1000*max(0.0,max(diff[at(4):at(6)])-.05))


def performance(paths, state):
    supply = state[2]
    point = samples(paths["dc"],None,NODES+OP_SAVES)
    dc = {k:v[0] for k,v in point.items()}
    if (max(abs(dc["VDD"]-supply), abs(dc["VCM"]-supply/2), abs(dc["CMD"]),
            abs((dc["INP"]+dc["INN"])/2-.6*supply))>1e-5 or dc["VSUP:p"]>=0):
        raise ValueError("Invalid supply, stimulus or supply-current reference")
    ac = samples(paths["gain"],"freq",("INP","INN","OUTP","OUTN","CMD"))
    vin = [a-b for a,b in zip(ac["INP"],ac["INN"])]
    if any(abs(v-1)>1e-6 or abs(c-1)>1e-6 for v,c in zip(vin,ac["CMD"])):
        raise ValueError("Missing 1 V differential small-signal stimulus")
    response = [(a-b)/v for a,b,v in zip(ac["OUTP"],ac["OUTN"],vin)]
    gain = unity(ac["freq"],response)
    loops = {}
    for mode in ("dm","cm"):
        d = samples(paths[mode],"freq",("loopGain",))
        # Spectre stores signed return ratio. Negative feedback has negative LF return.
        loops[mode] = unity(d["freq"],[-z for z in d["loopGain"]])
    wave = transient(paths["tran"],supply)
    cm_error = abs((dc["OUTP"]+dc["OUTN"])/2 - dc["VCM"])*1000
    bias = {}
    for name in OP_DEVICES:
        current,vg,vd,vsat,gm,gds = (dc[name+":"+k] for k in OP_FIELDS)
        if current == 0 or gm<=0 or gds<=0:
            raise ValueError("Unexpected transistor operating point for " + name)
        bias[name] = dict(id_uA=current*1e6, gm_id_per_V=gm/abs(current), vgs_V=vg, vds_V=vd,
                          saturation_headroom_V=abs(vd)-abs(vsat))
    checks = dict(gain=gain["low_frequency_gain_dB"]>LIMITS["gain_dB"], dc_cm=cm_error<=LIMITS["cm_error_mV"],
                  startup=wave["startup_passed"], saturation=all(v["saturation_headroom_V"]>=0 for v in bias.values()))
    for name,loop in loops.items():
        checks[name+"_stability"] = loop["phase_margin_deg"] is not None and loop["phase_margin_deg"]>=LIMITS["phase_margin_deg"]
    for k in ("rise_us","fall_us","cm_step_us"):
        checks[k] = wave[k] is not None and wave[k]<=LIMITS["settling_us"]
    return dict(gain_dB=gain["low_frequency_gain_dB"], gain_unity=gain["crossings"], loops=loops,
                dc_cm_error_mV=cm_error, supply_current_uA=-dc["VSUP:p"]*1e6, power_uW=-dc["VSUP:p"]*supply*1e6,
                operating_points=bias, waveform=wave, passed=all(checks.values()), checks=checks)


def measure(folder, site, state, native, fresh):
    if fresh:
        folder.mkdir()
    paths = {mode:analysis(folder/mode, deck(site,state,native,mode), name, site, site["spectre"] if fresh else None)
             for mode,(_,name) in MODES.items()}
    result = performance(paths,state)
    for mode in MODES:
        job = read_json(folder/mode/"job.json")
        if job["warnings"] or any(sha256(Path(k))!=v for k,v in job["dependencies"].items()):
            raise ValueError("Review Spectre diagnostics or changed model files in " + str(folder/mode))
    for mode,loop in result["loops"].items():
        if loop["phase_margin_deg"] is None:
            continue
        log = (folder/mode/"spectre.out").read_text(encoding="utf-8",errors="replace")
        matches = re.findall(r"Phase margin = ([\d.eE+\-]+) Deg at frequency = ([\d.eE+\-]+) ([GMk]?Hz)",log)
        if len(matches)!=1:
            raise ValueError("Missing Spectre phase-margin report for " + mode)
        pm,f,unit = matches[0]
        solver_hz = float(f)*{"Hz":1,"kHz":1e3,"MHz":1e6,"GHz":1e9}[unit]
        if abs(float(pm)-loop["phase_margin_deg"])>.15 or not math.isclose(
                solver_hz, loop["crossings"][0]["frequency_Hz"], rel_tol=.01):
            raise ValueError("Complex PSF return ratio and Spectre stability report disagree for "+mode)
    return result
