"""Materialize the chosen dimensions and audit the editable Xschem hierarchy and its netlist."""
import math
import os
import re
import shlex

from fetch_models import HERE
from measure import DEFAULT_REFERENCE_LOAD_FF, MAX_SETTLING_NS, reference_pulse
from mos_table import command


VIEWS = ("BIAS_MIRROR.sch", "BIAS_MIRROR.sym", "NMOS18.sym", "TB_BIAS_MIRROR.sch")
TCL_PATH = "append XSCHEM_LIBRARY_PATH :[file join $XSCHEM_SHAREDIR xschem_library] :$::env(MIRROR_SCHEMATIC_DIR)"


def leads(directory):
    symbol = (directory / "BIAS_MIRROR.sym").read_text(encoding="utf-8")
    parent = (directory / "TB_BIAS_MIRROR.sch").read_text(encoding="utf-8")
    inst = re.search(r"(?m)^C \{BIAS_MIRROR\.sym\} (-?\d+) (-?\d+) ([0-3]) ([01]) \{name=XBIAS\}", parent)
    if not inst:
        raise ValueError("Missing placed mirror in the testbench")
    x, y, rotation, flip = map(int, inst.groups())

    def transform(dx, dy):
        if flip:
            dx = -dx
        for _ in range(rotation):
            dx, dy = -dy, dx
        return dx, dy

    wires = []
    for row in re.findall(r"(?m)^N ([-\d]+) ([-\d]+) ([-\d]+) ([-\d]+) \{lab=([^}]+)\}", parent):
        a, b, c, d = map(int, row[:4])
        wires.append(((a,b), (c,d), row[4]))
    pins = {}
    directions = {"IREF":(-1,0), "IOUT":(1,0), "VSS":(0,1)}
    for line in re.findall(r"(?m)^B 5 .+$", symbol):
        coords = list(map(float, line.split()[2:6]))
        name = re.search(r"\bname=(\w+)", line)[1]
        dx, dy = transform((coords[0] + coords[2])/2, (coords[1] + coords[3])/2)
        anchor = (x+dx, y+dy)
        ux, uy = transform(*directions[name])
        net = "0" if name == "VSS" else name
        ends = [q if p == anchor else p for p,q,n in wires if n == net and anchor in (q,p)]
        if not ends or any((qx-anchor[0])*uy != (qy-anchor[1])*ux or
                           (qx-anchor[0])*ux + (qy-anchor[1])*uy <= 0 for qx,qy in ends):
            raise ValueError("Parent lead is missing or points inward at " + name)
        pins[name] = {"anchor":anchor, "outward_vector":(ux,uy), "endpoints":ends}
    if pins.keys() != directions.keys():
        raise ValueError("Missing mirror symbol pins")
    return pins


def audit(netlist, width, length, reference_load_fF=DEFAULT_REFERENCE_LOAD_FF, max_settling_ns=MAX_SETTLING_NS):
    joined = re.sub(r"\n\s*\+\s*", " ", netlist)
    lines = [line.strip().lower() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("*")]
    header = ".subckt bias_mirror iref iout vss"
    if lines.count(header) != 1 or lines[-1:] != [".end"]:
        raise ValueError("Native mirror definition or terminal order differs")
    top = lines[:lines.index(header)]
    expected_top = ["vbias vdd 0 dc 'vddtest'", "irefdrv vdd iref dc 'iset' " + reference_pulse(max_settling_ns).lower(),
                    "cref iref 0 {:.12g}f m=1".format(reference_load_fF), "xbias iref iout 0 bias_mirror",
                    "vforce iout 0 dc 'vouttest'"]
    if sorted(top) != sorted(expected_top):
        raise ValueError("Native testbench connections or stimuli differ")
    body = lines[lines.index(header)+1:]
    if len(body) != 4 or body[-2:] != [".ends", ".end"]:
        raise ValueError("Expected exactly two four-terminal devices")
    expected = {"xref":["iref","iref","vss","vss"], "xmir":["iout","iref","vss","vss"]}
    terms = {}
    for row in body[:2]:
        parts = row.split()
        name = parts[0]
        if name not in expected or parts[1:5] != expected[name] or parts[5] != "sky130_fd_pr__nfet_01v8":
            raise ValueError("Native device or terminal mismatch: " + name)
        values = dict(s.split("=",1) for s in parts[6:])
        parameters = dict(w=width, l=length, nf=1, ad=width*0.30, **{"as":width*0.30},
                          pd=2*(width+0.30), ps=2*(width+0.30))
        if values.keys() != parameters.keys() or any(
                not math.isclose(float(values[k]), val, rel_tol=1e-9) for k,val in parameters.items()):
            raise ValueError("Native device dimensions differ: " + name)
        terms[name] = dict(zip("DGSB", parts[1:5]))
    if terms.keys() != expected.keys():
        raise ValueError("Missing native device")
    return {"device_connections":terms, "width_um":width, "length_um":length, "reference_load_fF":reference_load_fF,
            "parent_port_order":["IREF","IOUT","VSS"]}, top


def build(directory, sizing, xschem, reference_load_fF=DEFAULT_REFERENCE_LOAD_FF, max_settling_ns=MAX_SETTLING_NS):
    directory.mkdir()
    width, length = sizing["width_um"], sizing["length_um"]
    for name in VIEWS:
        content = (HERE / name).read_text(encoding="utf-8")
        if name == "BIAS_MIRROR.sch":
            for token, value in (("'MIR_W'",width), ("'MIR_L'",length), ("'MIR_W*0.30'",width*0.30),
                                 ("'2*(MIR_W+0.30)'",2*(width+0.30))):
                if token not in content:
                    raise ValueError("Missing dimension in schematic: " + token)
                content = content.replace(token, "{:.8g}".format(value))
            content = content.replace("MIR_W and MIR_L set both device dimensions in micrometres.",
                                      "Each NMOS: W = {:g} um, L = {:g} um, one finger.".format(width, length))
        elif name == "TB_BIAS_MIRROR.sch":
            for old, new in (("name=CREF value=100f", "name=CREF value={:.12g}f".format(reference_load_fF)),
                             ("The 100 fF reference load", "The {:.12g} fF reference load".format(reference_load_fF)),
                             (reference_pulse(MAX_SETTLING_NS), reference_pulse(max_settling_ns))):
                if content.count(old) != 1:
                    raise ValueError("Missing or ambiguous testbench fixture: " + old)
                content = content.replace(old, new, 1)
        (directory / name).write_text(content, encoding="utf-8")
    env = dict(os.environ, MIRROR_SCHEMATIC_DIR=str(directory))
    command([xschem,"-n","-q","-x","-s","--tcl",TCL_PATH,"--netlist_path",directory,
             directory / "TB_BIAS_MIRROR.sch"], directory, "xschem.log", env)
    log = (directory / "xschem.log").read_text(encoding="utf-8", errors="replace")
    if re.search("(?im)^error:|^warning:|unable to open|symbol not found|^skipping", log):
        raise ValueError("Review native Xschem diagnostics in " + str(directory))
    path = directory / "TB_BIAS_MIRROR.spice"
    netlist = path.read_text(encoding="utf-8")
    readback, top = audit(netlist, width, length, reference_load_fF, max_settling_ns)
    readback["parent_leads"] = leads(directory)
    opener = directory.parent / "open.sh"
    opener.write_text(
        '#!/bin/sh\nset -eu\n'
        'base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'export MIRROR_SCHEMATIC_DIR="$base/views"\n'
        'case "${1:-bench}" in\n'
        '  bench) target=TB_BIAS_MIRROR.sch ;;\n  core) target=BIAS_MIRROR.sch ;;\n'
        '  symbol) target=BIAS_MIRROR.sym ;;\n  *) echo "Use bench, core, or symbol" >&2; exit 2 ;;\nesac\n'
        + "exec " + shlex.quote(xschem) + " --tcl " + shlex.quote(TCL_PATH) + ' "$MIRROR_SCHEMATIC_DIR/$target"\n',
        encoding="utf-8")
    return netlist, readback, top
