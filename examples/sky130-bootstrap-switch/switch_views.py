"""Editable Xschem views, outward-facing parent pins and native netlist readback."""
import os
import re
import shlex
from pathlib import Path

from switch_design import DELAY_NS, EDGE_NS, MODELS, budget, require
from switch_models import HERE, MIM
from mos_table import command
from native import TCL_PATH

ADC = HERE.parent / "sky130-adc-driver"
PORTS = "IN CLK CLKB SAMPLE VDD VSS".split()
HEADER = "v {xschem version=3.4.4 file_version=1.2}\nG {}\nK {}\nV {}\nS {}\nE {}"


class Sheet:
    def __init__(self):
        self.lines = [HEADER]
        self.labels = 0

    def note(self, x, y, message, scale=.28):
        self.lines.append(f"T {{{message}}} {x} {y} 0 0 {scale} {scale} {{}}")

    def wire(self, net, *points):
        for (x,y),(xx,yy) in zip(points,points[1:]):
            require((x==xx) != (y==yy), "Wire must be a nonzero orthogonal segment")
            self.lines.append(f"N {x} {y} {xx} {yy} {{lab={net}}}")

    def part(self, symbol, x, y, properties, rot=0, flip=0):
        self.lines.append(f"C {{{symbol}.sym}} {x} {y} {rot} {flip} {{{properties}}}")

    def label(self, net, x, y, rot=0, flip=0):
        self.labels += 1
        self.part("devices/lab_pin",x,y,f"name=L{self.labels} lab={net}",rot,flip)

    def port(self, net, x, y, rot=0):
        kind = "iopin" if net in ("VDD","VSS") else "opin" if net=="SAMPLE" else "ipin"
        self.part("devices/"+kind,x,y,f"name=P_{net} lab={net}",rot)

    def fet(self, design, name, x, y, rot=0):
        _,typ,g = design["devices"][name]
        self.part("BSW_"+typ,x,y,("name="+name+" w={w:g} l={l:g} ad={ad:g} pd={pd:g} mult={mult:g}").format(**g),rot)

    def text(self):
        return "\n".join(self.lines)+"\n"


def boosted(d):
    s=Sheet()
    s.note(60,-110,"Bootstrapped sampling switch",.48)
    s.note(60,-58,"Bootstrap capacitor and precharge")
    s.note(810,-58,"Gate drive and discharge")
    top=[(90,95),(90,40),(330,40)]
    top.extend([(650,40)] if d["well_side_um"] else [(410,40),(650,40),(725,40)])
    s.wire("TOP",*top)
    s.label("TOP",90,40)
    s.part("BSW_MIM",90,125,"name=XCAP side="+str(d["side_um"]))
    s.wire("BOT",(90,155),(90,225)); s.label("BOT",90,225)
    for name,x,body in (("XPRE",330,410),("XLINK",650,725)):
        s.fet(d,name,x,150)
        s.wire("TOP",(x,40),(x,110))
        if d["well_side_um"]:
            s.wire("WELL",(x+30,150),(body,150));s.label("WELL",body,150,0,1)
        else:s.wire("TOP",(x+30,150),(body,150),(body,40))
    s.wire("VDD",(330,190),(330,215)); s.port("VDD",330,215,1)
    s.wire("GATE",(290,150),(240,150),(240,280),(650,280),(810,280),(970,280),(970,320))
    s.wire("GATE",(650,190),(650,280))
    s.wire("CLKB",(610,150),(550,150)); s.label("CLKB",550,150)
    s.label("GATE",810,280,1)
    s.fet(d,"XOFF",970,360)
    s.wire("CLKB",(930,360),(895,360)); s.label("CLKB",895,360)
    s.wire("VSS",(1000,360),(1040,360),(1040,420),(970,420))
    s.wire("VSS",(970,400),(970,420),(970,450)); s.port("VSS",970,450,1)
    s.note(60,365,"Signal path",.30)
    s.port("IN",60,515); s.port("SAMPLE",1080,515)
    s.wire("IN",(60,515),(160,515),(770,515))
    s.fet(d,"XMAIN",810,515,1)
    s.wire("SAMPLE",(850,515),(1080,515))
    s.wire("GATE",(810,280),(810,475))
    s.wire("VSS",(810,545),(810,580)); s.label("VSS",810,580)
    s.note(455,595,"Bottom-plate tracking and reset")
    s.fet(d,"XBOTN",350,700,1); s.fet(d,"XBOTP",350,885,3)
    s.wire("IN",(160,515),(160,700),(160,885),(310,885))
    s.wire("IN",(160,700),(310,700))
    s.wire("BOT",(390,700),(490,700),(490,800),(650,800),(650,825))
    s.wire("BOT",(390,885),(490,885),(490,800)); s.label("BOT",490,700,0,1)
    s.wire("CLK",(350,660),(350,630)); s.port("CLK",350,630,1)
    s.wire("CLKB",(350,925),(350,960)); s.port("CLKB",350,960,3)
    s.wire("VSS",(350,730),(350,752)); s.label("VSS",350,752)
    s.wire("VDD",(350,855),(350,833)); s.label("VDD",350,833)
    s.fet(d,"XZERO",650,865)
    s.wire("CLKB",(610,865),(560,865)); s.label("CLKB",560,865)
    s.wire("VSS",(680,865),(740,865),(740,930),(650,930))
    s.wire("VSS",(650,905),(650,930),(650,960)); s.label("VSS",650,960)
    s.note(780,685,"CLK high: BOT follows IN.")
    s.note(780,730,"Hold: XOFF discharges GATE.")
    s.note(780,775,"XPRE recharges the capacitor.")
    s.note(780,850,"XMAIN W/L = {:g}/{:g} um".format(d["devices"]["XMAIN"][2]["w"],d["devices"]["XMAIN"][2]["l"]))
    if d["well_side_um"]:
        s.note(1160,-58,"PMOS well bias")
        for name,x,source,gate in (("XWREF",1270,"VDD","GATE"),("XWBOOST",1570,"TOP","CLKB")):
            s.fet(d,name,x,190)
            s.wire(source,(x,150),(x,90));s.label(source,x,90)
            s.wire(gate,(x-40,190),(x-75,190));s.label(gate,x-75,190)
            s.wire("WELL",(x+30,190),(x+85,190),(x+85,290),(x,290))
            s.wire("WELL",(x,230),(x,290),(x,350))
        s.wire("WELL",(1270,350),(1410,350),(1570,350));s.label("WELL",1410,350,1)
        s.wire("WELL",(1410,350),(1410,410));s.part("BSW_MIM",1410,440,"name=XWELL side="+str(d["well_side_um"]))
        s.wire("VDD",(1410,470),(1410,530));s.label("VDD",1410,530)
        s.note(1170,620,"Hold: XWREF connects the well to VDD.")
        s.note(1170,660,"Track: XWBOOST connects it to TOP.")
        s.note(1170,700,"XWELL couples the supply ramp to the well.")
    return s.text()


def ordinary(d):
    s=Sheet();s.note(55,-25,"Transmission-gate reference",.44)
    s.port("IN",65,230);s.port("SAMPLE",575,230)
    s.wire("IN",(65,230),(160,230),(160,140),(260,140));s.wire("IN",(160,230),(160,320),(260,320))
    s.wire("SAMPLE",(340,140),(450,140),(450,230),(575,230));s.wire("SAMPLE",(340,320),(450,320),(450,230))
    s.fet(d,"XSN",300,140,1);s.fet(d,"XSP",300,320,3)
    s.wire("CLK",(300,100),(300,60));s.port("CLK",300,60,1)
    s.wire("CLKB",(300,360),(300,415));s.port("CLKB",300,415,3)
    s.wire("VSS",(300,170),(300,195),(245,195));s.port("VSS",245,195,2)
    s.wire("VDD",(300,290),(300,265),(375,265));s.port("VDD",375,265)
    return s.text()


def sources(r):
    return {"VVDD":"PWL(0 0 200n 'VRAIL')",
            "BSIG":"V=min(time/200n,1)*(VCM+AMPL*sin(6.283185307179586*FTONE*time))",
            "VCLK":"PULSE(0 'VRAIL' {:g}n {:g}n {:g}n {:g}n {:.12g}n)".format(
                DELAY_NS,EDGE_NS,EDGE_NS,r["track_ns"]-EDGE_NS,budget(r)["period_ns"]),
            "BCLKB":"V=v(vdd)-v(clk)"}


def bench(r):
    s=Sheet();s.note(55,10,"Sampling with a finite source and a held load",.44)
    s.note(55,70,"Input and source resistance");s.note(595,70,"Hold capacitor")
    s.part("BSW_SOURCE",90,270,'name=BSIG value="'+sources(r)["BSIG"]+'"')
    s.wire("SOURCE",(90,240),(90,200),(185,200));s.label("SOURCE",90,200)
    s.part("BSW_RES",215,200,f'name=RDRV value={r["source_ohm"]:g}')
    s.wire("IN",(245,200),(345,200));s.label("IN",270,200,1)
    s.part("BSW_SWITCH",445,200,"name=XSW")
    s.wire("SAMPLE",(545,200),(690,200),(690,250));s.label("SAMPLE",690,200,0,1)
    s.part("devices/capa",690,280,f'name=CSH value={r["load_pF"]:g}p')
    def ground(x,top,end):
        s.wire("0",(x,top),(x,end));s.part("devices/gnd",x,end,f"name=G{x}_{end} lab=0")
    ground(90,300,335);ground(690,310,340)
    for net,x,y,end,flip in (("CLK",400,110,85,0),("VDD",475,110,85,1),("CLKB",400,290,345,0)):
        s.wire(net,(x,y),(x,end));s.label(net,x,end,0,flip)
    ground(475,290,335)
    s.note(55,395,"Supply ramp and complementary clock")
    for name,net,x in (("VVDD","VDD",105),("VCLK","CLK",365),("BCLKB","CLKB",635)):
        s.part("BSW_SOURCE",x,525,'name='+name+' value="'+sources(r)[name]+'"')
        s.wire(net,(x,495),(x,460));s.label(net,x,460,0,1);ground(x,555,590)
    s.note(55,635,"Read SAMPLE {:g} ns after the falling edge ends.".format(r["read_after_off_ns"]))
    return s.text()


def view_sources(d,r):
    views={f.name:f.read_text(encoding="utf-8") for f in sorted(HERE.glob("BSW_*.sym"))}
    for typ,model in MODELS.items():
        base=(ADC/("ADRV_"+("NMOS" if typ.startswith("N") else "PMOS")+".sym")).read_text()
        views["BSW_"+typ+".sym"]=re.sub(r"[np]fet_01v8",model,base)
    views["BSW_SOURCE.sym"]=(ADC/"ADRV_SOURCE.sym").read_text()
    if d["name"]=="transmission":
        views["BSW_SWITCH.sym"]="\n".join(l for l in views["BSW_SWITCH.sym"].splitlines()
                if not l.startswith(("L 4 -10 -22","L 4 -10 -32","L 4 15 -38","L 4 21 -38","L 4 21 -32")))+"\n"
    views.update({"BSW_SWITCH.sch":ordinary(d) if d["name"]=="transmission" else boosted(d),"TB_BSW_SAMPLE.sch":bench(r)})
    return views


def parent_check(views):
    sym=views["BSW_SWITCH.sym"];tb=views["TB_BSW_SAMPLE.sch"]
    placed=re.search(r"(?m)^C \{BSW_SWITCH.sym\} (\d+) (\d+) 0 0 \{name=XSW\}$",tb)
    require(placed,"Missing upright XSW in parent");ox,oy=map(int,placed.groups())
    wires=[(tuple(map(int,(x,y))),tuple(map(int,(xx,yy))),net) for x,y,xx,yy,net in re.findall(
        r"(?m)^N (\d+) (\d+) (\d+) (\d+) \{lab=(\w+)\}",tb)]
    pins=re.findall(r"(?m)^B 5 ([-.\d]+) ([-.\d]+) ([-.\d]+) ([-.\d]+) \{name=(\w+) dir=(\w+)\}",sym)
    require([p[4] for p in pins]==PORTS,"Symbol pin order changed")
    for (x,y,xx,yy,pin,kind),(dx,dy) in zip(pins,[(-1,0),(0,-1),(0,1),(1,0),(0,-1),(0,1)]):
        at=((float(x)+float(xx))/2+ox,(float(y)+float(yy))/2+oy)
        ends=[b if at==a else a for a,b,net in wires if at in (a,b) and net==("0" if pin=="VSS" else pin)]
        require(len(ends)==1 and kind==("out" if pin=="SAMPLE" else "inout" if pin in ("VSS","VDD") else "in"),"Wrong parent pin "+pin)
        a,b=ends[0][0]-at[0],ends[0][1]-at[1]
        require(a*dy==b*dx and a*dx+b*dy>0,"Parent wire must leave outward at "+pin)


def audit(netlist,d,r):
    top={};core={};scope=top;seen=False
    for raw in re.sub(r"\n\s*\+\s*"," ",netlist).lower().splitlines():
        row=raw.strip().split()
        if not row or row[0].startswith("*") or row[0]==".end":continue
        if row[0]==".subckt":
            require(not seen and row[1:]==["bsw_switch"]+[s.lower() for s in PORTS],"Wrong subcircuit pin order")
            scope=core;seen=True
        elif row[0]==".ends":scope=top
        else:
            require(row[0] not in scope,"Duplicate component "+row[0]);scope[row[0]]=row[1:]
    expected={"xsw":["in","clk","clkb","sample","vdd","0","bsw_switch"],"rdrv":["source","in",f'{r["source_ohm"]:g}'],
              "csh":["sample","0",f'{r["load_pF"]:g}p',"m=1"]}
    for name,net in (("VVDD","vdd"),("BSIG","source"),("VCLK","clk"),("BCLKB","clkb")):
        expected[name.lower()]=[net,"0"]+sources(r)[name].lower().split()
    require(top==expected,"Wrong native parent sources, load or wiring: "+str(top))
    caps={n:side for n,side in (("xcap",d["side_um"]),("xwell",d["well_side_um"])) if side}
    require(seen and set(core)=={n.lower() for n in d["devices"]}|set(caps),"Wrong switch components")
    for name,(pins,typ,g) in d["devices"].items():
        row=core[name.lower()];require(row[:5]==pins.lower().split()+["sky130_fd_pr__"+MODELS[typ]],"Wrong D/G/S/B at "+name+": "+str(row[:5]))
        wanted=dict(w=g["w"],l=g["l"],m=g["mult"],nf=1,ad=g["ad"],pd=g["pd"]);wanted.update({"as":g["ad"],"ps":g["pd"]})
        pairs=[s.split("=") for s in row[5:]];params={k:float(v) for k,v in pairs}
        require(len(pairs)==len(wanted) and set(params)==set(wanted) and all(abs(params[k]-v)<1e-8 for k,v in wanted.items()),"Wrong geometry at "+name)
    for name,side in caps.items():
        a,b=("top","bot") if name=="xcap" else ("well","vdd")
        require(core[name]==[a,b,MIM,f"w={side}",f"l={side}","mf=1"],"Wrong MIM plate at "+name)


def build(folder,tool,d,r):
    folder.mkdir(parents=True)
    views=view_sources(d,r);parent_check(views)
    for name,src in views.items():(folder/name).write_text(src,encoding="utf-8")
    env=dict(os.environ,MIRROR_SCHEMATIC_DIR=str(folder))
    command([tool,"-n","-q","-x","-s","--tcl",TCL_PATH,"--netlist_path",folder,folder/"TB_BSW_SAMPLE.sch"],folder,"xschem.log",env)
    require(not re.search(r"(?im)^error:|^warning:|unable to open|symbol not found|^skipping",(folder/"xschem.log").read_text()),"Read the Xschem log in "+str(folder))
    file=folder/"TB_BSW_SAMPLE.spice"
    native="\n".join(l for l in file.read_text().splitlines() if not l.strip().startswith("**"))+"\n"
    audit(native,d,r);file.write_text(native,encoding="utf-8")
    return native


OPENER = ('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
          'export MIRROR_SCHEMATIC_DIR="$base/views"\ncase "${1:-bench}" in\n'
          ' bench) view=TB_BSW_SAMPLE.sch ;;\n core) view=BSW_SWITCH.sch ;;\n symbol) view=BSW_SWITCH.sym ;;\n'
          ' *) echo "Use bench, core or symbol" >&2; exit 2 ;;\nesac\nexec "${XSCHEM:-xschem}" --tcl '+shlex.quote(TCL_PATH)+' "$MIRROR_SCHEMATIC_DIR/$view"\n')
