"""Replay held-sample distortion and signed terminal extrema from the complete transient."""
from bisect import bisect_left
import cmath
import csv
import gzip
import io
import math

from switch_design import CYCLES, DELAY_NS, DISCARD, EDGE_NS, TONE_BIN, budget, require

PAIRS = ("GS","GD","GB","DS","BS","BD")
BASE_STEP_NS = .25


def nodes(d):
    internal = ["gate","top","bot"] if d["side_um"] else []
    if d.get("well_side_um"): internal.append("well")
    return ["source","in","sample","clk","clkb","vdd"]+["xsw."+n for n in internal]


def transient(r,state,vpp,native,d,libs,step):
    corner,temp,rail=state
    timing=budget(r)
    stop=DELAY_NS+timing["period_ns"]*(DISCARD+CYCLES)
    text=[l for l in native.splitlines() if l.strip().lower()!=".end"]
    return "\n".join(["SKY130 held-sample and terminal-voltage test"]+['.lib "{}" {}'.format(lib,corner) for lib in libs]+[
        ".temp "+str(temp),".param VRAIL={:.12g} VCM={:.12g} AMPL={:.12g} FTONE={:.12g}".format(
            rail,r["input_common_V"],vpp/2,timing["tone_Hz"]),*text,".control","set wr_singlescale",
        "set wr_vecnames","set numdgt=12","tran {:g}n {:.12g}n 0 {:g}n".format(step,stop,step),
        "wrdata wave.dat "+" ".join("v("+s+")" for s in nodes(d)),"echo analysis_complete","quit",".endc",".end",""])


def wave(path,d):
    fields=["time"]+["v("+n+")" for n in nodes(d)]
    opener=gzip.open if path.suffix==".gz" else open
    with opener(path,"rt",encoding="ascii") as f:
        require(f.readline().lower().split()==fields,"Wrong time/voltage columns in "+str(path))
        rows=[]
        for line in f:
            row=tuple(map(float,line.split()))
            require(len(row)==len(fields) and all(map(math.isfinite,row)),"Malformed voltage row in "+str(path))
            rows.append(row)
    require(len(rows)>2,"Missing transient")
    vectors=dict(zip(["time"]+[n.split(".")[-1] for n in nodes(d)],zip(*rows)))
    time=[s*1e9 for s in vectors.pop("time")]
    return time,vectors


def harmonics(samples):
    require(len(samples)==CYCLES,"Wrong coherent record length")
    result=[abs(2/CYCLES*sum(v*cmath.exp(-2j*math.pi*h*TONE_BIN*k/CYCLES) for k,v in enumerate(samples))) for h in range(1,6)]
    require(result[0]>1e-6,"No measurable fundamental")
    ratio=math.sqrt(sum(a*a for a in result[1:]))/result[0]
    return result,20*math.log10(max(1e-15,ratio))


def extremes(time,values):
    low=min(range(len(values)),key=values.__getitem__)
    high=max(range(len(values)),key=values.__getitem__)
    return dict(min_V=values[low],min_at_ns=time[low],max_V=values[high],max_at_ns=time[high])


def limits(typ,pair,forward):
    thick=typ.endswith("5")
    gate,drain,reverse=(5.5,11.,5.5) if thick else (1.95,1.95,1.95)
    if pair.startswith("G"):return -gate,gate
    if pair=="DS":return -drain,drain
    return (-reverse,forward) if typ.startswith("N") else (-forward,reverse)


def published_ranges(typ):
    """Signed, as-drawn model validity limits from the SKY130 device-details tables."""
    thick=typ.endswith("5")
    gate,drain,reverse=(5.5,11.,5.5) if thick else (1.95,1.95,1.95)
    if typ.startswith("N"):return {"GS":(0.,gate),"DS":(0.,drain),"BS":(-reverse,0. if thick else .3)}
    return {"GS":(-gate,0.),"DS":(-drain,0.),"BS":(0. if thick else -.1,reverse)}


def stress(time,vectors,d,forward):
    memo={};zero=(0.,)*len(time);data={};violations=[];published_bs={};outside={}
    def span(a,b):
        k=(a,b)
        if k not in memo:
            aa=zero if a=="vss" else vectors[a];bb=zero if b=="vss" else vectors[b]
            memo[k]=extremes(time,[u-v for u,v in zip(aa,bb)])
        return memo[k]
    for name,(pins,typ,_) in d["devices"].items():
        pin=dict(zip("DGSB",pins.lower().split()));data[name]={}
        for pair in PAIRS:
            e=span(pin[pair[0]],pin[pair[1]]);data[name][pair]=e
            lo,hi=limits(typ,pair,forward)
            if e["min_V"]<lo-1e-8 or e["max_V"]>hi+1e-8:violations.append(name+"."+pair)
        # SKY130 publishes a signed DC VBS model range. Keep this separate from
        # the example's six-pair transient limits, including when a body is reverse biased at the named drain.
        e=data[name]["BS"];observed=max(0,e["max_V"] if typ.startswith("N") else -e["min_V"])
        allowed=0. if typ.endswith("5") else (.3 if typ.startswith("N") else .1)
        published_bs[name]=dict(forward_peak_V=observed,published_forward_V=allowed,excess_V=max(0.,observed-allowed))
        for pair,(lo,hi) in published_ranges(typ).items():
            e=data[name][pair];low=lo-e["min_V"];high=e["max_V"]-hi
            if max(low,high)>1e-8:
                sign="min" if low>=high else "max"
                outside[name+"."+pair]=dict(range_V=[lo,hi],observed_V=e[sign+"_V"],time_ns=e[sign+"_at_ns"],excess_V=max(low,high))
    plates={}
    for name,a,b in ([("XCAP","top","bot")] if d["side_um"] else [])+([("XWELL","well","vdd")] if d.get("well_side_um") else []):
        e=span(a,b);plates[name]=e
        if max(-e["min_V"],e["max_V"])>5.:violations.append(name+".plate")
    return data,plates,violations,published_bs,outside


def measure(path,r,state,vpp,d,step):
    tt,v=wave(path,d);corner,_,rail=state;spec=budget(r);period=spec["period_ns"]
    stop=DELAY_NS+(DISCARD+CYCLES)*period
    require(0<=tt[0]<=step and abs(tt[-1]-stop)<1e-5 and all(0<b-a<=step*1.0001 for a,b in zip(tt,tt[1:])),"Incomplete transient or excessive time step")
    rises=[];falls=[];clock=v["clk"];prev=clock[0]
    for i,t in enumerate(tt):
        ramp=min(t/200,1.);sine=ramp*(r["input_common_V"]+vpp/2*math.sin(2*math.pi*spec["tone_Hz"]*t*1e-9))
        require(abs(v["vdd"][i]-rail*ramp)<1e-6 and abs(v["source"][i]-sine)<1e-6 and
                abs(v["clkb"][i]-(v["vdd"][i]-clock[i]))<1e-6,"Wrong supply ramp, source or complementary clock")
        if i and ((prev<rail/2<=clock[i]) or (clock[i]<rail/2<=prev)):
            cross=tt[i-1]+(t-tt[i-1])*(rail/2-prev)/(clock[i]-prev)
            (rises if clock[i]>prev else falls).append(cross)
        prev=clock[i]
    require(len(rises)==len(falls)==DISCARD+CYCLES,"Wrong number of measured clock edges")
    for i,(up,down) in enumerate(zip(rises,falls)):
        require(abs(up-(DELAY_NS+i*period+EDGE_NS/2))<1e-4 and abs(down-up-r["track_ns"])<1e-4,"Wrong clock period or track interval")
    def at(net,t):
        k=bisect_left(tt,t)
        require(0<k<len(tt),"Sample outside recorded transient")
        f=(t-tt[k-1])/(tt[k]-tt[k-1]);return v[net][k-1]+f*(v[net][k]-v[net][k-1])
    read=[fall+EDGE_NS/2+r["read_after_off_ns"] for fall in falls]
    record=[(i,dn,rtime,at("source",dn),at("in",dn),at("sample",rtime))
            for i,(dn,rtime) in enumerate(zip(falls[DISCARD:],read[DISCARD:]))]
    src,src_thd=harmonics([s[3] for s in record]);spectrum,thd=harmonics([s[5] for s in record])
    require(abs(src[0]/(vpp/2)-1)<1e-4 and src_thd<-100,"Stimulus fails the coherent reference measurement")
    gain=spectrum[0]/src[0]
    terminal,plates,bad,dc_bs,outside=stress(tt,v,d,r["max_forward_V"]);checks={"distortion":thd<=r["max_thd_dBc"],
        "gain":spec["gain_bounds"][0]<=gain<=spec["gain_bounds"][1],"terminal_and_plate_limits":not bad}
    phases={}
    if d["side_um"]:
        tracking=[dn-.25*r["track_ns"] for dn in falls]
        pre=[up-EDGE_NS for up in rises]   # includes the first cold-start cycle
        phases=dict(min_track_vgs_V=min(at("gate",t)-at("in",t) for t in tracking),max_hold_gate_abs_V=max(abs(at("gate",t)) for t in read),
                    min_precharge_V=min(at("top",t)-at("bot",t) for t in pre))
        checks.update(track_gate=phases["min_track_vgs_V"]>=rail*spec["min_track_vgs_ratio"],
                      hold_gate=phases["max_hold_gate_abs_V"]<=spec["max_hold_gate_V"],
                      precharge=phases["min_precharge_V"]>=rail*spec["min_precharge_ratio"])
    buf=io.StringIO();writer=csv.writer(buf,lineterminator="\n");writer.writerow(("index","clock_fall_50pct_ns","read_ns","source_at_fall_V","input_at_fall_V","held_V"))
    writer.writerows([[row[0]]+[format(x,".12g") for x in row[1:]] for row in record])
    return dict(corner=corner,input_vpp_V=vpp,points=len(tt),measured_edges=len(falls),retained_samples=len(record),
        thd_dBc=thd,source_thd_dBc=src_thd,gain=gain,harmonic_peak_V=spectrum,phases=phases,
        terminals=terminal,plates=plates,violations=bad,dc_model_forward_vbs=dc_bs,published_model_excursions=outside,
        checks=checks,passed=all(checks.values())),buf.getvalue()
