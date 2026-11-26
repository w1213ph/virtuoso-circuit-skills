"""Size and audit the native driver and sampled load, including the placed parent pins."""
import math
import os
import re
import shlex
from pathlib import Path

from bench import FIXED_OUTPUT_PF, budget, sources
from mos_table import command
from native import TCL_PATH


HERE = Path(__file__).resolve().parent
OTA = {"XPREF": ("VB VB VDD VDD","p"), "XNREF": ("VB VB VSS VSS","n"), "XTAIL": ("TAIL VB VSS VSS","n"),
       "XIP": ("VX INP TAIL VSS","n"), "XIM": ("OUT INM TAIL VSS","n"),
       "XLA": ("VX VX VDD VDD","p"), "XLB": ("OUT VX VDD VDD","p")}
SAMPLER = {"XSN": ("SAMPLE CLK IN VSS","n"), "XSP": ("SAMPLE CLKB IN VDD","p")}
PORTS = {"ADRV_OTA": ("INP INM OUT VDD VSS", ["VIN","FB","OUT","VDD","0"],
                     [(-1,0),(-1,0),(1,0),(0,-1),(0,1)]),
         "ADRV_SAMPLER": ("IN CLK CLKB SAMPLE VDD VSS", ["OUT","CLK","CLKB","SAMPLE","VDD","0"],
                         [(-1,0),(0,-1),(0,1),(1,0),(0,-1),(0,1)])}


def lookup(rows, pol, length, body, target):
    candidates = [r for r in rows if r["polarity"] == pol and r["length_um"] == length
                  and abs(r["vds_V"]) == .65 and abs(r["vbs_V"]) == body
                  and r["gm_id_per_V"] and r["abs_id_uA"] >= .1]
    if not candidates:
        raise ValueError("Missing TT sizing data for " + pol)
    return min(candidates, key=lambda r: abs(r["gm_id_per_V"]-target))


def size(rows, r, long_load):
    target = budget(r)["branch_target_uA"]
    input_row = lookup(rows,"N",1,.15,16)
    load_row = lookup(rows,"P",1 if long_load else .5,0,15 if long_load else 14)
    def count(row, unit):
        n = math.ceil(target / (row["id_per_width_uA_um"]*unit))
        if not 1 <= n <= 32:
            raise ValueError("Required parallel units exceed the characterized design family")
        return n
    fingers = count(input_row,6.)
    unit = 7.5 if long_load else 8.
    load = (unit, load_row["length_um"], count(load_row,unit))
    tail_width = 4.8*fingers
    copies = math.ceil(tail_width/8)
    devices = {"XPREF": (2,.5,1), "XNREF": (1.2,1,1), "XTAIL": (tail_width/copies,1,copies),
               "XIP": (6,1,fingers), "XIM": (6,1,fingers), "XLA": load, "XLB": load,
               "XSN": (3,.15,1), "XSP": (6,.15,1)}
    return {"devices": devices, "input_lookup": input_row, "load_lookup": load_row,
            "branch_target_uA": target, "core_gate_area_um2": sum(w*l*m for w,l,m in (devices[n] for n in OTA)),
            "switch_gate_area_um2": sum(w*l*m for w,l,m in (devices[n] for n in SAMPLER))}


def materialize(folder, sizing, r):
    repl = {n: "w={:.10g} l={:g} mult={} ad={:.10g} pd={:.10g}".format(w,l,m,.3*w,2*(w+.3))
            for n,(w,l,m) in sizing["devices"].items()}
    repl.update(sources(r))
    def label(name):
        return "W/L {:g}/{:g} um, {} parallel".format(*sizing["devices"][name])
    load_note = "Mirror load per side: " + label("XLA") + ". Both wells connect to VDD."
    if sizing["devices"]["XLA"] != sizing["devices"]["XLB"]:
        wa, la, ma = sizing["devices"]["XLA"]
        wb, lb, mb = sizing["devices"]["XLB"]
        if (wa, la) != (wb, lb):
            raise ValueError("Use identical unit W/L for both ratioed mirror branches")
        load_note = "PMOS mirror: W/L {:g}/{:g} um per unit, {} at VX and {} at OUT.".format(wa, la, ma, mb)
    repl.update(INPUT_NOTE="Input pair per side: " + label("XIP") + ". Tail: " + label("XTAIL") + ".",
                LOAD_NOTE=load_note,
                CAP_NOTE="Sampling capacitor: {:g} pF. VSS returns the stored charge.".format(r["sample_cap_pF"]),
                TB_NOTE="{:g} to {:g} V input, {:g} ns track, {:g} ns hold. Read SAMPLE at the sampling capacitor.".format(
                    r["input_low_V"],r["input_high_V"],r["track_ns"],r["hold_ns"]),
                CS="{:.12g}p".format(r["sample_cap_pF"]), CFIX="{:g}p".format(FIXED_OUTPUT_PF))
    for source in sorted(list(HERE.glob("*.sym"))+list(HERE.glob("*.sch"))):
        data = source.read_text(encoding="utf-8")
        text = re.sub(r"__([A-Z][A-Z0-9_]*)__", lambda m:repl[m[1]], data)
        (folder/source.name).write_text(text,encoding="utf-8")


def parent_pins(folder):
    parent = (folder/"TB_ADRV_SAMPLE.sch").read_text(encoding="utf-8")
    wires = []
    for a,b,c,d,net in re.findall(r"(?m)^N ([-\d]+) ([-\d]+) ([-\d]+) ([-\d]+) \{lab=(\w+)\}",parent):
        wires.append(((int(a),int(b)),(int(c),int(d)),net))
    evidence = {}
    for cell,(order,nets,directions) in PORTS.items():
        # Both delivered parent instances are upright. Read the placement from the actual parent.
        placed = re.search(r"(?m)^C \{"+cell+r"\.sym\} (\d+) (\d+) 0 0 ",parent)
        if not placed:
            raise ValueError("Missing upright instance: " + cell)
        x,y = map(int,placed.groups())
        pins = re.findall(r"(?m)^B 5 ([-.\d]+) ([-.\d]+) ([-.\d]+) ([-.\d]+) \{name=(\w+) dir=(\w+)\}", (folder/(cell+".sym")).read_text())
        if [p[4] for p in pins] != order.split():
            raise ValueError("Symbol pin order changed: " + cell)
        for (a,b,c,d,p,direction),net,(ux,uy) in zip(pins,nets,directions):
            ax,ay = (float(a)+float(c))/2+x,(float(b)+float(d))/2+y
            ends = [q if (ax,ay)==start else start for start,q,n in wires if n==net and (ax,ay) in (start,q)]
            want = "inout" if p in ("VDD","VSS") else "out" if p in ("OUT","SAMPLE") else "in"
            if direction!=want or len(ends)!=1 or any((qx-ax)*uy!=(qy-ay)*ux or (qx-ax)*ux+(qy-ay)*uy<=0 for qx,qy in ends):
                raise ValueError("Check port type and outward parent wire at {}.{}".format(cell,p))
            evidence[cell+"."+p] = {"net":net,"at":[ax,ay],"ends":ends,"direction":direction}
    return evidence


def audit(native,sizing,r):
    sections = {"TOP":{}}
    scope = "TOP"
    for raw in re.sub(r"\n\s*\+\s*"," ",native).splitlines():
        line = raw.strip().upper()
        if not line or line.startswith("*"):
            continue
        words = line.split()
        if words[0]==".SUBCKT":
            scope = words[1]
            if scope not in PORTS or scope in sections or words[2:]!=PORTS[scope][0].split():
                raise ValueError("Unexpected native subcircuit or pin order: "+line)
            sections[scope]={}
        elif words[0]==".ENDS":
            scope="TOP"
        elif words[0]!=".END":
            if words[0] in sections[scope]:
                raise ValueError("Duplicate device in "+scope)
            sections[scope][words[0]] = words[1:]
    expected = {"TOP":set(sources(r))|{"XDRV","XLOAD","COUT"}, "ADRV_OTA":set(OTA), "ADRV_SAMPLER":set(SAMPLER)|{"CSH"}}
    if {s:set(parts) for s,parts in sections.items()}!=expected:
        raise ValueError("Unexpected native topology: "+str({s:list(p) for s,p in sections.items()}))
    for cell,n in (("ADRV_OTA","XDRV"),("ADRV_SAMPLER","XLOAD")):
        if sections["TOP"][n]!=PORTS[cell][1]+[cell]:
            raise ValueError("Wrong parent connections at "+n)
    for cell, devices in (("ADRV_OTA",OTA),("ADRV_SAMPLER",SAMPLER)):
        for n,(pins,pol) in devices.items():
            row=sections[cell][n]
            if row[:5]!=pins.split()+["SKY130_FD_PR__"+pol.upper()+"FET_01V8"]:
                raise ValueError("Wrong D/G/S/B or model at "+n+": "+" ".join(row[:5]))
            w,l,m=sizing["devices"][n]
            expected_params=dict(W=w,L=l,NF=1,M=m,AD=.3*w,AS=.3*w,PD=2*(w+.3),PS=2*(w+.3))
            vals={k:float(v) for k,v in (item.split("=") for item in row[5:])}
            if set(vals)!=set(expected_params) or any(not math.isclose(vals[k],v,rel_tol=1e-8) for k,v in expected_params.items()):
                raise ValueError("Wrong geometry or multiplicity at "+n)
    if sections["ADRV_SAMPLER"]["CSH"]!=["SAMPLE","VSS","{:.12g}P".format(r["sample_cap_pF"]),"M=1"] or sections["TOP"]["COUT"]!=["OUT","0","{:g}P".format(FIXED_OUTPUT_PF),"M=1"]:
        raise ValueError("Sampling or output capacitance differs from the specified load")
    nodes={"VVDD":["VDD","0"],"VDATA":["VIN","0"],"VPROBE":["FB","OUT"],"VCLK":["CLK","0"],"VCLKB":["CLKB","0"]}
    for name,value in sources(r).items():
        if sections["TOP"][name]!=nodes[name]+value.upper().split():
            raise ValueError("Unexpected excitation at "+name)
    return sections


def build(folder,tool,sizing,r):
    folder.mkdir()
    materialize(folder,sizing,r)
    env=dict(os.environ,MIRROR_SCHEMATIC_DIR=str(folder))
    command([tool,"-n","-q","-x","-s","--tcl",TCL_PATH,"--netlist_path",folder,folder/"TB_ADRV_SAMPLE.sch"],folder,"xschem.log",env)
    log=(folder/"xschem.log").read_text(errors="replace")
    if re.search(r"(?im)^error:|^warning:|unable to open|symbol not found|^skipping",log):
        raise ValueError("Review Xschem diagnostics in "+str(folder/"xschem.log"))
    original=(folder/"TB_ADRV_SAMPLE.spice").read_text(encoding="utf-8")
    sections=audit(original,sizing,r)
    # Remove location-only Xschem comments from the reproducible simulation input.
    native="\n".join(l for l in original.splitlines() if not l.strip().startswith("**"))+"\n"
    opener = ('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexport MIRROR_SCHEMATIC_DIR="$base/views"\n'
              'case "${1:-bench}" in\n core) view=ADRV_OTA.sch ;;\n symbol) view=ADRV_OTA.sym ;;\n sampler) view=ADRV_SAMPLER.sch ;;\n'
              ' bench) view=TB_ADRV_SAMPLE.sch ;;\n *) echo "Use core, symbol, sampler or bench" >&2; exit 2 ;;\nesac\n'
              'if [ -n "${XSCHEM:-}" ]; then tool=$XSCHEM; elif command -v xschem >/dev/null; then tool=xschem; else tool='
              +shlex.quote(tool)+'; fi\nexec "$tool" --tcl '+shlex.quote(TCL_PATH)+' "$MIRROR_SCHEMATIC_DIR/$view"\n')
    (folder.parent/"open.sh").write_text(opener,encoding="utf-8")
    return native, {"native_sections":sections,"parent_pins":parent_pins(folder)}
