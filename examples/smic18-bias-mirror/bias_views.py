"""Editable SMIC18 four-terminal current mirror, symbol and loaded native parent."""

import math
import re
from pathlib import Path

from audit_native import outward_leads, spice_number
from schematic import draw_wire, note, pin, place, pt, q, view_procedure
from spectre_run import execute, read_json


LIB = "BIAS18_BENCH"
CORE = "BIAS18_MIRROR"
TB = "TB_BIAS18_MIRROR"
PORTS = {"IREF": "input", "IOUT": "output", "VSS": "inputOutput"}
ANCHORS = {"IREF": (0, 1.375), "IOUT": (3, 1.375), "VSS": (1.5, 0)}
OUTWARD = {"IREF": (-1, 0), "IOUT": (1, 0), "VSS": (0, -1)}
SHARED = Path(__file__).resolve().parents[1] / "dynamic-comparator"
VIEWS = ((CORE, "schematic"), (CORE, "symbol"), (TB, "schematic"))


def core(pdk, width, length, current):
    ops, instances = [], {}
    props = {"l": "{:g}n".format(1000 * length), "fw": "{:g}u".format(width), "fingers": "1", "m": "1"}
    for name, x, orientation, drain in (("MREF", 2.75, "MY", "IREF"), ("MOUT", 4.75, "R0", "IOUT")):
        place(
            ops,
            instances,
            name,
            pdk,
            "n18",
            (x, 2.5),
            orientation,
            {"D": drain, "G": "IREF", "S": "VSS", "B": "VSS"},
            props,
            use_cdf=True,
        )
    for name, xy, orient in (
        ("IREF", (0.875, 3.375), "R0"),
        ("IOUT", (6.625, 3.375), "R0"),
        ("VSS", (3.75, 0.875), "R270"),
    ):
        ops.append(pin(name, xy, orient, PORTS))
    for points in (
        [(0.875, 3.375), (2.5, 3.375), (3.75, 3.375), (3.75, 2.5)],
        [(2.5, 3.375), (2.5, 2.6875)],
        [(2.75, 2.5), (3.75, 2.5), (4.75, 2.5)],
        [(5, 2.6875), (5, 3.375), (6.625, 3.375)],
        [(2.5, 2.5), (1.8125, 2.5), (1.8125, 1.625), (2.5, 1.625)],
        [(5, 2.5), (5.6875, 2.5), (5.6875, 1.625), (5, 1.625)],
        [(2.5, 2.3125), (2.5, 1.625), (3.75, 1.625), (5, 1.625), (5, 2.3125)],
        [(3.75, 1.625), (3.75, 0.875)],
    ):
        ops.append(draw_wire(points))
    ops += [
        note(0.875, 4.375, "1:1 NMOS bias current mirror", 0.15),
        note(0.875, 3.875, "IREF: external {:g} uA".format(current), 0.105),
        note(4.8, 3.875, "IOUT: current sink", 0.105),
        note(0.875, 0.375, "Each MOS: W = {:g} um, L = {:g} um, one finger".format(width, length), 0.105),
        note(0.875, 0.10, "Pair gate area = {:g} um^2".format(2 * width * length), 0.105),
    ]
    return ops, instances


def symbol():
    ops = []
    for points in (
        [(0.25, 0.25), (2.75, 0.25), (2.75, 2), (0.25, 2), (0.25, 0.25)],
        [(0, 1.375), (0.75, 1.375), (0.75, 0.75)],
        [(0.625, 0.90), (0.75, 0.75), (0.875, 0.90)],
        [(3, 1.375), (2.25, 1.375), (2.25, 0.75)],
        [(2.125, 0.90), (2.25, 0.75), (2.375, 0.90)],
        [(0.75, 0.75), (2.25, 0.75)],
        [(1.5, 0.75), (1.5, 0)],
    ):
        ops.append(
            'dbCreateLine(cv list("device" "drawing") list({}))'.format(" ".join(pt(*p) for p in points))
        )
    for name, (x, y) in ANCHORS.items():
        ops.append(
            "cmpSymbolPin({} {} list({} {}))".format(
                q(name), q(PORTS[name]), pt(x - 0.0125, y - 0.0125), pt(x + 0.0125, y + 0.0125)
            )
        )
    for x, y, text, role, kind, size in (
        (0.72, 1.67, "IREF", "pin name", "normalLabel", 0.12),
        (2.20, 1.67, "IOUT", "pin name", "normalLabel", 0.12),
        (1.78, 0.43, "VSS", "pin name", "normalLabel", 0.11),
        (1.5, 1.19, "1:1", "logical label", "normalLabel", 0.19),
        (1.5, 2.58, "[@instanceName]", "instance label", "NLPLabel", 0.14),
        (1.5, 2.30, "[@cellName]", "logical label", "NLPLabel", 0.12),
    ):
        ops.append(
            'schCreateSymbolLabel(cv {} {} {} "centerCenter" "R0" "roman" {} {})'.format(
                pt(x, y), q(role), q(text), size, q(kind)
            )
        )
    order = "list(" + " ".join(q(n) for n in PORTS) + ")"
    ops += [
        'dbCreateRect(cv list("instance" "drawing") list(0:0 3:2))',
        'dbReplaceProp(cv "portOrder" "ILList" {})'.format(order),
        'unless(schSymbolToPinList({} {} "symbol") error("Missing symbol pins"))'.format(q(LIB), q(CORE)),
        'unless(equal(schGetPinOrder(cv) {}) error("Unexpected symbol pin order"))'.format(order),
    ]
    return ops


def bench(req):
    ops, instances = [], {}

    def device(name, master, xy, plus, minus, props):
        place(ops, instances, name, "analogLib", master, xy, "R0", {"PLUS": plus, "MINUS": minus}, props)

    place(
        ops, instances, "XDUT", LIB, CORE, (7, 3.5), "R0", {"IREF": "IREF", "IOUT": "IOUT", "VSS": "gnd!"}, {}
    )
    device("VSUP", "vdc", (2, 7.875), "VDD", "gnd!", {"vdc": "VDDVAL"})
    device(
        "IBIAS",
        "ipulse",
        (4, 7.875),
        "VDD",
        "IREF",
        {
            "idc": "ISTART",
            "i1": "ISTART",
            "i2": "ISET",
            "td": "10n",
            "tr": "1n",
            "tf": "1n",
            "pw": "THOLD",
            "per": "TPER",
        },
    )
    device("CREF", "cap", (5.375, 3.875), "IREF", "gnd!", {"c": "{:.12g}f".format(req["reference_load_fF"])})
    device("VFORCE", "vdc", (12.125, 4.875), "IOUT", "gnd!", {"vdc": "VOUT"})
    ops += [
        draw_wire([(2, 7.875), (2, 8.5), (4, 8.5), (4, 7.875)], "VDD", (2.65, 8.5)),
        draw_wire([(4, 7.5), (4, 4.875), (5.375, 4.875), (7, 4.875)], "IREF", (5.875, 4.875)),
        draw_wire([(5.375, 4.875), (5.375, 3.875)]),
        draw_wire([(10, 4.875), (12.125, 4.875)], "IOUT", (10.7, 4.875)),
    ]
    for i, (x, top, bottom) in enumerate(
        ((2, 7.5, 6.875), (5.375, 3.5, 2.875), (8.5, 3.5, 2.5), (12.125, 4.5, 3.875))
    ):
        place(ops, instances, "GND" + str(i + 1), "analogLib", "gnd", (x, bottom), "R0", {"gnd!": "gnd!"}, {})
        ops.append(draw_wire([(x, top), (x, bottom)]))
    ops += [
        note(1.5, 9.125, "ADC bias mirror testbench", 0.17),
        note(5.75, 8.5, "IBIAS: external {:g} uA reference, 1 ns turn-on".format(req["reference_uA"]), 0.12),
        note(10.75, 6.3, "VFORCE sets output voltage", 0.12),
        note(
            1.5,
            1.75,
            "Reference-node load: {:g} fF.  DC error budget: {:g}% over 0.40 to 1.20 V.".format(
                req["reference_load_fF"], req["max_error_pct"]
            ),
            0.11,
        ),
        note(
            1.5,
            1.375,
            "At VOUT = 0.80 V, IOUT must settle to +/-1% within {:g} ns of IREF reaching 99%.".format(
                req["max_settling_ns"]
            ),
            0.11,
        ),
    ]
    return ops, instances


def sources(pdk, width, length, req):
    core_ops, core_instances = core(pdk, width, length, req["reference_uA"])
    bench_ops, bench_instances = bench(req)
    text = (
        "\n".join(
            [
                (SHARED / "native_helpers.il").read_text(encoding="utf-8"),
                view_procedure("biasCore", CORE, "schematic", core_ops, LIB),
                view_procedure("biasSymbol", CORE, "symbol", symbol(), LIB),
                view_procedure("biasBench", TB, "schematic", bench_ops, LIB),
                f"""procedure(biasBuild() let((lib dest)
        cmpCheckPDK({q(pdk)}) dest=strcat(getWorkingDir() "/{LIB}")
        when(ddGetObj("{LIB}") || isDir(dest) error("Target library exists"))
        lib=ddCreateLib("{LIB}" dest)
        unless(lib && equal(lib~>readPath dest) error("Cannot create local bias library"))
        techBindTechFile(lib {q(pdk)}) biasCore() biasSymbol() biasBench()
        printf("BIAS_BUILD_OK\\n") t))
        unless(errset(biasBuild() t) exit(3)) exit(0)""",
            ]
        )
        + "\n"
    )
    return text, {CORE: core_instances, TB: bench_instances}


def build(folder, config, width, length, req):
    folder.mkdir()
    script, _ = sources(config["pdk_library"], width, length, req)
    (folder / "build.il").write_text(script, encoding="utf-8")
    (folder / "cds.lib").write_text("INCLUDE " + q(str(config["cds_lib"])) + "\n", encoding="utf-8")
    (folder / ".cdsinit").write_text("; Isolated bias project\n", encoding="utf-8")
    execute(
        [config["virtuoso"], "-nograph", "-log", folder / "build.log", "-restore", folder / "build.il"],
        folder,
        "build.stdout",
        240,
    )
    if (
        "BIAS_BUILD_OK" not in (folder / "build.log").read_text(encoding="utf-8", errors="replace")
        or not (folder / LIB).is_dir()
    ):
        raise ValueError("Native build incomplete: " + str(folder))
    netdir = folder / "netlists"
    netdir.mkdir()
    (netdir / ".cdsinit").write_text("; Isolated SI\n", encoding="utf-8")
    (netdir / "si.env").write_text(
        'simLibName="{}"\nsimCellName="{}"\nsimViewName="schematic"\nsimSimulator="spectre"\n'.format(LIB, TB)
        + 'simNotIncremental=nil\nsimReNetlistAll=t\nsimViewList=list("spectre" "veriloga" "schematic")\n'
        'simStopList=list("spectre" "veriloga")\nsimNetlistHier=t\n',
        encoding="utf-8",
    )
    execute(
        [config["si"], "-batch", "-cdslib", folder / "cds.lib", "-command", "nl"], netdir, "si.stdout", 180
    )
    (folder / "open.il").write_text(
        "\n".join('geOpen(?lib "{}" ?cell "{}" ?view "{}" ?mode "r")'.format(LIB, c, v) for c, v in VIEWS)
        + "\n",
        encoding="utf-8",
    )


def readback(folder, pdk, width, length, req):
    script, expected = sources(pdk, width, length, req)
    if (folder / "build.il").read_text(encoding="utf-8") != script:
        raise ValueError("Native drawing source differs from the planned dimensions and testbench")
    log = (folder / "build.log").read_text(encoding="utf-8", errors="replace")
    for cell in (CORE, TB):
        if re.findall(r"CMP_CHECK " + cell + r" (\([^\n]*\))", log) != ["(0 0)"]:
            raise ValueError("Missing clean Check & Save result: " + cell)
    si_log = (folder / "netlists/si.stdout").read_text(encoding="utf-8", errors="replace")
    if "BIAS_BUILD_OK" not in log or not re.search(r"(?m)^End netlisting ", si_log):
        raise ValueError("Incomplete Virtuoso build or SI export")
    views = {(c, v): read_json(folder / (c + "-" + v + ".json")) for c, v in VIEWS}
    if {p["name"]: (p["direction"], p["xy"]) for p in views[CORE, "symbol"]["ports"]} != {
        n: (PORTS[n], list(xy)) for n, xy in ANCHORS.items()
    }:
        raise ValueError("Saved symbol directions or anchors differ")
    cards = {}
    netlist = (folder / "netlists/netlist").read_text(encoding="utf-8")
    flat = re.sub(r"\\\r?\n\s*", " ", netlist)
    definition = re.search(r"(?ms)^subckt " + CORE + r" ([^\n]+)\n(.*?)^ends " + CORE + r"\s*$", flat)
    if (
        not definition
        or set(definition[1].split()) != PORTS.keys()
        or len(definition[1].split()) != len(PORTS)
    ):
        raise ValueError("Missing native mirror or unexpected port order")
    parent = flat[definition.end() :]
    order = definition[1].split()
    dut = re.search(r"(?m)^XDUT\s+\(([^)]+)\)\s+" + CORE + r"\s*$", parent)
    if not dut or dut[1].split() != ["0" if key == "VSS" else key for key in order]:
        raise ValueError("SI parent connections differ")
    fixture = {
        "VFORCE": ("IOUT 0", "vsource", {"dc": "VOUT", "type": "dc"}),
        "VSUP": ("VDD 0", "vsource", {"dc": "VDDVAL", "type": "dc"}),
        "IBIAS": (
            "VDD IREF",
            "isource",
            {
                "dc": "ISTART",
                "type": "pulse",
                "val0": "ISTART",
                "val1": "ISET",
                "period": "TPER",
                "delay": "10n",
                "rise": "1n",
                "fall": "1n",
                "width": "THOLD",
            },
        ),
        "CREF": ("IREF 0", "capacitor", {"c": "{:.12g}f".format(req["reference_load_fF"])}),
    }
    components = re.findall(r"(?m)^(\w+)\s+\(([^)]+)\)\s+(\w+)([^\n]*)$", parent)
    if len(components) != len(fixture) + 1 or {c[0] for c in components} != set(fixture) | {"XDUT"}:
        raise ValueError("Unexpected top-level SI components")
    for name, nets, master, pairs in components:
        if name != "XDUT" and (nets, master, dict(p.split("=", 1) for p in pairs.split())) != fixture[name]:
            raise ValueError("SI fixture differs from the agreed schematic: " + name)
    native_devices = re.findall(r"(?m)^\s*(\w+)\s+\(([^)]+)\)\s+(\w+)\s*(.*?)$", definition[2])
    if {d[0] for d in native_devices} != {"MREF", "MOUT"} or len(native_devices) != 2:
        raise ValueError("Expected exactly two native MOS instances")
    for cell in (CORE, TB):
        view = views[cell, "schematic"]
        actual = {i["name"]: i for i in view["instances"] if i["lib"] != "basic"}
        if actual.keys() != expected[cell].keys() or {
            t["name"]: (t["direction"], t["net"]) for t in view["ports"]
        } != ({k: (v, k) for k, v in PORTS.items()} if cell == CORE else {}):
            raise ValueError("Native instance or port inventory differs: " + cell)
        for name, entry in actual.items():
            plan = expected[cell][name]
            if (entry["lib"], entry["cell"], entry["xy"], entry["orient"]) != (
                plan["lib"],
                plan["cell"],
                list(plan["xy"]),
                plan["orient"],
            ) or {t["name"]: t["net"] for t in entry["terms"]} != plan["terms"]:
                raise ValueError("OA master, placement or terminal mismatch: " + name)
            props = {key: value for key, _kind, value in entry["props"]}
            if cell == TB and any(str(props.get(k)) != str(v) for k, v in plan["parameters"].items()):
                raise ValueError("Saved testbench parameter differs: " + name)
            if cell == CORE:
                if not {"w", "l", "fw"} <= props.keys():
                    raise ValueError("Missing saved transistor dimensions: " + name)
                dimensions = dict(plan["parameters"], w=plan["parameters"]["fw"])
                if any(
                    k in props and not math.isclose(spice_number(props[k]), spice_number(v), rel_tol=1e-8)
                    for k, v in dimensions.items()
                ):
                    raise ValueError("Saved transistor dimensions differ: " + name)
    lead = outward_leads(
        views[TB, "schematic"]["shapes"],
        next(i for i in views[TB, "schematic"]["instances"] if i["name"] == "XDUT"),
        OUTWARD,
    )
    for name, nets, model, parameters in native_devices:
        expected_nets = [expected[CORE][name]["terms"][p] for p in "DGSB"]
        values = dict(re.findall(r"\b(\w+)=([^\s]+)", parameters))
        if (
            model != "n18"
            or nets.split() != expected_nets
            or any(
                not math.isclose(spice_number(values[k]), v, rel_tol=1e-8)
                for k, v in (("w", width * 1e-6), ("l", length * 1e-6), ("nf", 1), ("m", 1))
            )
        ):
            raise ValueError("SI MOS terminals, model or dimensions differ: " + name)
        cards[name] = model + " " + parameters.strip()
    if cards["MREF"] != cards["MOUT"]:
        raise ValueError("The two sized SI device cards differ")
    return (
        netlist,
        cards["MREF"],
        dict(
            port_order=order,
            parent_leads=lead,
            width_um=width,
            length_um=length,
            fingers=1,
            multiplier=1,
            gate_area_um2=round(2 * width * length, 8),
            reference_load_fF=req["reference_load_fF"],
        ),
    )
