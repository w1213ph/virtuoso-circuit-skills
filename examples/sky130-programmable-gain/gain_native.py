"""Build and audit the editable hierarchy, including the receiving device and switch bodies."""
import math
import os
from pathlib import Path
import re
import shlex
import shutil

from gain_measure import check
from mos_table import command
from native import TCL_PATH


HERE = Path(__file__).resolve().parent
PORTS = {"PGA_STAGE":"IN B0 B1 OUT VDD VSS", "PGA_SWITCH":"A B EN VDD VSS",
         "PGA_RECEIVER":"DRIVE RXIN VDD VSS"}
PULSE = "PWL(0 0 1U 0 1.002U 'VSUPPLY' 4U 'VSUPPLY' 4.002U 0 7U 0)"


def geometry(sizing, variant):
    n = sizing[variant]
    return {"XAMP_N":(sizing["n_width_um"],1,n), "XAMP_P":(sizing["p_width_um"],1,n*sizing["p_units"])}


def mos_props(w,l,m):
    return "w={:.9g} l={:g} mult={} ad={:.9g} pd={:.9g}".format(w,l,m,w*.3,2*(w+.3))


def parse_native(text):
    sections = {"TOP":{}}
    scope = "TOP"
    end = False
    lines = []
    for line in re.sub(r"\n\s*\+\s*"," ",text).splitlines():
        words = line.strip().upper().split()
        if not words or words[0].startswith("*"):
            continue
        check(not end,"Unexpected contents after .end")
        key = words[0]
        if key == ".SUBCKT":
            check(len(words)>1 and scope=="TOP" and words[1] not in sections and
                  PORTS.get(words[1])==" ".join(words[2:]),"Unexpected subcircuit or pin order")
            scope=words[1]
            sections[scope]={}
        elif key == ".ENDS":
            check(scope!="TOP","Unexpected .ends")
            scope="TOP"
        elif key == ".END":
            check(scope=="TOP","Unclosed subcircuit")
            end=True
            continue
        else:
            check(key not in sections[scope], "Duplicate instance in "+scope)
            sections[scope][key] = words[1:]
        lines.append(line.rstrip())
    check(end and set(sections)=={"TOP",*PORTS}, "Incomplete native gain hierarchy")
    return sections, "\n".join(lines)+"\n"


def audit(text, sizing, variant, r):
    sections, body = parse_native(text)
    expected = {
        "TOP":{"XPGA":"VIN B0 B1 DRIVE VDD 0 PGA_STAGE","XRX":"DRIVE RXIN VDD 0 PGA_RECEIVER",
               "VSUP":"VDD 0 DC 'VSUPPLY'", "VSIG":"VIN 0 DC 'VINBIAS' AC 1",
               "VB0":"B0 0 DC 'VSUPPLY*BIT0' "+PULSE, "VB1":"B1 0 DC 'VSUPPLY*BIT1' "+PULSE},
        "PGA_STAGE":{"XSW0":"IN S0 B0 VDD VSS PGA_SWITCH","XSW1":"IN S1 B1 VDD VSS PGA_SWITCH",
                     "RBASE":"SUM IN 120K M=1","R0":"SUM S0 120K M=1","R1":"SUM S1 60K M=1","RF":"OUT SUM 120K M=1"},
        "PGA_RECEIVER":{"RTOP":"VDD BIAS 150K M=1","RBOT":"BIAS VSS 100K M=1","RBIAS":"RXIN BIAS 150K M=1",
                        "RISO":"RXIN DRIVE 2.2K M=1","RLOAD":"MON VSS 56K M=1",
                        "CRX":"RXIN VSS {:.12g}P M=1".format(r["receiver_cap_pF"]).upper()},
        "PGA_SWITCH":{}}
    sizes=geometry(sizing,variant)
    devices={
        "PGA_STAGE":{"XAMP_N":("OUT SUM VSS VSS","N",sizes["XAMP_N"]),"XAMP_P":("OUT SUM VDD VDD","P",sizes["XAMP_P"])},
        "PGA_SWITCH":{"XIP":("ENB EN VDD VDD","P",(1.2,.15,1)),"XIN":("ENB EN VSS VSS","N",(.6,.15,1)),
                      "XTN":("B EN A VSS","N",(2,.15,1)),"XTP":("B ENB A VDD","P",(4,.15,1))},
        "PGA_RECEIVER":{"XBUF":("VDD RXIN MON VSS","N",(4,.5,1))}}
    for scope, instances in sections.items():
        transistors=devices.get(scope,{})
        check(set(instances)==set(expected[scope])|set(transistors),"Unexpected native instances in "+scope)
        for name,wanted in expected[scope].items():
            check(instances[name]==wanted.split(),"Wrong native connectivity or value at "+scope+"/"+name)
        for name,(nodes,pol,(w,l,m)) in transistors.items():
            words=instances[name]
            check(words[:5]==nodes.split()+["SKY130_FD_PR__"+pol+"FET_01V8"],"Wrong native MOS at "+name)
            params=dict(kv.split("=") for kv in words[5:])
            want={"W":w,"L":l,"M":m,"NF":1,"AD":.3*w,"AS":.3*w,"PD":2*(w+.3),"PS":2*(w+.3)}
            check(params.keys()==want.keys() and all(math.isclose(float(params[k]),v,rel_tol=1e-8)
                  for k,v in want.items()),"Wrong MOS geometry at "+name)
    # Both switches are instantiated. Receiver MOS is part of the fixed load.
    area=sum(w*l*m for w,l,m in sizes.values()) + 2*sum(v[2][0]*v[2][1]*v[2][2] for v in devices["PGA_SWITCH"].values())
    return body, {"amplifier_gate_area_um2":area,"receiver_gate_area_um2":2., "core_devices":sizes}


def replace_once(text, pattern, replacement):
    changed,count = re.subn(pattern,lambda m:m.group(1)+replacement+"}",text,flags=re.M)
    check(count==1,"Expected exactly one annotated instance for "+pattern)
    return changed


def parent_leads(views):
    sides={"PGA_STAGE":"left bottom bottom right top bottom","PGA_SWITCH":"left right top top bottom",
           "PGA_RECEIVER":"left right top bottom"}
    directions={"left":(-1,0),"right":(1,0),"top":(0,-1),"bottom":(0,1)}
    placements=(("TB_PGA_GAIN","XPGA","PGA_STAGE","VIN B0 B1 DRIVE VDD 0"),
                ("TB_PGA_GAIN","XRX","PGA_RECEIVER","DRIVE RXIN VDD 0"),
                ("PGA_STAGE","XSW0","PGA_SWITCH","IN S0 B0 VDD VSS"),
                ("PGA_STAGE","XSW1","PGA_SWITCH","IN S1 B1 VDD VSS"))
    leads={}
    for parent,inst,cell,nets in placements:
        sch=(views/(parent+".sch")).read_text(encoding="utf-8")
        sym=(views/(cell+".sym")).read_text(encoding="utf-8")
        placed=re.search(r"(?m)^C \{"+cell+r"\.sym\} (-?\d+) (-?\d+) ([0-3]) ([01]) \{name="+inst+r"\}",sch)
        check(placed,"Missing placed symbol "+inst)
        x,y,rotation,flip=map(int,placed.groups())
        def transform(dx,dy):
            if flip:
                dx=-dx
            for _ in range(rotation):
                dx,dy=-dy,dx
            return dx,dy
        wires=[((int(a),int(b)),(int(c),int(d)),n) for a,b,c,d,n in
               re.findall(r"(?m)^N (-?\d+) (-?\d+) (-?\d+) (-?\d+) \{lab=(\w+)\}",sch)]
        pins=re.findall(r"(?m)^B 5 ([-.\d]+) ([-.\d]+) ([-.\d]+) ([-.\d]+) \{name=(\w+) dir=(\w+)\}",sym)
        check([p[4] for p in pins]==PORTS[cell].split(),"Unexpected symbol ports: "+cell)
        for (a,b,c,d,name,direction),side,net in zip(pins,sides[cell].split(),nets.split()):
            expected="inout" if name in ("VDD","VSS","A","B") else "out" if name in ("OUT","RXIN") else "in"
            dx,dy=transform((float(a)+float(c))/2,(float(b)+float(d))/2)
            ax,ay=x+dx,y+dy
            ux,uy=transform(*directions[side])
            ends=[q if p==(ax,ay) else p for p,q,n in wires if n==net and (ax,ay) in (p,q)]
            check(direction==expected and len(ends)==1 and
                  all((ex-ax)*uy==(ey-ay)*ux and (ex-ax)*ux+(ey-ay)*uy>0 for ex,ey in ends),
                  "Check electrical direction or first parent wire at "+inst+"."+name)
            leads[parent+"/"+inst+"."+name]={"net":net,"pin":[ax,ay],"end":list(ends[0])}
    return leads


def build(stage, xs, sizing, variant, r):
    views=stage/"views"
    views.mkdir()
    for filename in ("ADRV_NMOS.sym","ADRV_PMOS.sym","ADRV_SOURCE.sym"):
        shutil.copyfile(str(HERE.parent/"sky130-adc-driver"/filename),str(views/filename))
    for path in sorted(list(HERE.glob("*.sym"))+list(HERE.glob("*.sch"))):
        text=path.read_text(encoding="utf-8")
        if path.name=="PGA_STAGE.sch":
            for name,values in geometry(sizing,variant).items():
                text=replace_once(text,r"^(C .*\{name="+name+r" )[^}]+\}$",mos_props(*values))
            n,p=(geometry(sizing,variant)[k] for k in ("XAMP_N","XAMP_P"))
            text=re.sub(r"T \{Core W/L \(um\)[^}]*\}",lambda _: "T {Core W/L (um)  N %g/%g x %d,  P %g/%g x %d.}"%(*n,*p),text)
        if path.name=="PGA_RECEIVER.sch":
            text=replace_once(text,r"^(C .*\{name=CRX value=)[^}]+\}$","{:.12g}p".format(r["receiver_cap_pF"]))
        if path.name=="TB_PGA_GAIN.sch":
            text=re.sub(r"T \{Input bias [^}]*\}",lambda _: "T {Input bias %g x VDD, receiver %g pF, AC %g to %g Hz.}"%(
                r["input_common_mode_fraction"],r["receiver_cap_pF"],r["low_Hz"],r["high_Hz"]),text)
        (views/path.name).write_text(text,encoding="utf-8")
    env=dict(os.environ,MIRROR_SCHEMATIC_DIR=str(views))
    command([xs,"-n","-q","-x","-s","--tcl",TCL_PATH,"--netlist_path",views,views/"TB_PGA_GAIN.sch"],views,"xschem.log",env)
    log=(views/"xschem.log").read_text(encoding="utf-8",errors="replace")
    check(not re.search(r"(?im)^\s*(error|warning):|symbol not found|unable to open",log),"Review "+str(views/"xschem.log"))
    source=(views/"TB_PGA_GAIN.spice").read_text(encoding="utf-8")
    body,evidence=audit(source,sizing,variant,r)
    evidence["parent_leads"]=parent_leads(views)
    opener='''#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export MIRROR_SCHEMATIC_DIR="$base/views"
case "${1:-bench}" in
    bench) cell=TB_PGA_GAIN ;;
    stage) cell=PGA_STAGE ;;
    switch) cell=PGA_SWITCH ;;
    receiver) cell=PGA_RECEIVER ;;
    *) echo "Use bench, stage, switch or receiver" >&2; exit 2 ;;
esac
exec "${XSCHEM:-xschem}" --tcl '''+shlex.quote(TCL_PATH)+' "$MIRROR_SCHEMATIC_DIR/$cell.sch"\n'
    (stage/"open.sh").write_text(opener,encoding="utf-8")
    return body,evidence
