"""Draw the published comparator and its bench on the SMIC18 n18/p18 symbol grid."""

import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
LIB = "CMP18_BENCH"
CORE = "CMP18_CMP_DYNAMIC"
TB = "TB_CMP18_CMP_DYNAMIC"
TOPOLOGY = json.loads((HERE / "topology.json").read_text(encoding="utf-8"))
PORTS = TOPOLOGY["ports"]


def q(value):
    return json.dumps(value)


def pt(x, y):
    return f"list({x:g} {y:g})"


def draw_wire(points, net=None, at=None, justification="lowerLeft"):
    path = "list(" + " ".join(pt(*p) for p in points) + ")"
    if net is None:
        return f"cmpWire({path} nil nil nil)"
    return f"cmpWire({path} {q(net)} {pt(*(at or points[0]))} {q(justification)})"


def note(x, y, text, size=0.14):
    return f'dbCreateLabel(cv list("text" "drawing") {pt(x, y)} {q(text)} "lowerLeft" "R0" "roman" {size})'


def pin(name, origin, orient):
    direction = PORTS[name]
    master = {"input": "ipin", "output": "opin", "inputOutput": "iopin"}[direction]
    return (f'schCreatePin(cv dbOpenCellViewByType("basic" {q(master)} "symbol" "" "r")'
            f' {q(name)} {q(direction)} nil {pt(*origin)} {q(orient)})')


def place(operations, instances, name, library, master, origin, orient, connections, parameters, use_cdf=False):
    operations.append(f'cmpInst({q(library)} {q(master)} {q(name)} {pt(*origin)} {q(orient)})')
    instances[name] = {
        "lib": library, "cell": master, "xy": origin, "orient": orient,
        "terms": connections, "parameters": parameters,
    }
    if use_cdf:
        pairs = " ".join(f"list({q(k)} {q(v)})" for k, v in parameters.items())
        operations.append(f"cmpSetParams(cv {q(name)} list({pairs}))")
    else:
        for key, value in parameters.items():
            operations.append(f'cmpProperty({q(name)} {q(key)} {q(value)})')


MOS_MASTERS = {"nominal": {"NMOS": "n18", "PMOS": "p18"},
               "statistical": {"NMOS": "n18_ckt", "PMOS": "p18_ckt"}}


WIDTH_GROUPS = {"input_pair": ("MNINP", "MNINN"), "tail": ("MTAIL",),
                "latch_n": ("MNQ_P", "MNQ_N"), "latch_p": ("MPQ_P", "MPQ_N"),
                "internal_reset": ("MPR_XL", "MPR_XR"), "output_reset": ("MPR_QL", "MPR_QR")}


def width_overrides(groups):
    if not isinstance(groups, dict) or set(groups) - set(WIDTH_GROUPS):
        raise ValueError("Specify candidate widths by symmetric device group: " + ", ".join(WIDTH_GROUPS))
    widths = {}
    for group, width in groups.items():
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
            raise ValueError("Expected a finite positive total width in micrometres for " + group)
        widths.update(dict.fromkeys(WIDTH_GROUPS[group], width))
    return widths


def core_schematic(variant, pdk, mos_family="nominal", candidate_widths_um=None):
    ops, instances = [], {}
    widths = width_overrides({} if candidate_widths_um is None else candidate_widths_um) if variant == "candidate" else {}
    # XY is the centre of the diffusion terminals. The symbol origin is at the gate.
    placement = {
        "MNINP": (6, 3.5, "R0"), "MNINN": (14, 3.5, "MY"),
        "MTAIL": (10, 1.5, "R0"),
        "MNQ_N": (6, 6.5, "MY"), "MNQ_P": (14, 6.5, "R0"),
        "MPQ_N": (6, 9.5, "MY"), "MPQ_P": (14, 9.5, "R0"),
        "MPR_XL": (3, 5.25, "R0"), "MPR_XR": (17, 5.25, "MY"),
        "MPR_QL": (3, 9.5, "R0"), "MPR_QR": (17, 9.5, "MY"),
    }
    devices = {d["instance"]: d for d in TOPOLOGY["devices"]}
    if devices.keys() != placement.keys():
        raise ValueError("The declared devices and drawing do not match")
    for name, (x, y, orientation) in placement.items():
        device = devices[name]
        fingers, multiplier = device["fingers"], device["multiplier"]
        fw = widths.get(name, device["total_width_um"][variant]) / (fingers * multiplier)
        props = {"l": f'{device["length_um"] * 1000:g}n', "fw": f"{fw:g}u",
                 "fingers": str(fingers), "m": str(multiplier)}
        master = MOS_MASTERS[mos_family][device["type"]]
        origin = (x + 0.25 if orientation == "MY" else x - 0.25, y)
        place(ops, instances, name, pdk, master, origin, orientation,
              device["terminals"], props, use_cdf=True)

    for name, origin, orient in [
        ("INP", (1.875, 3.5), "R0"), ("INN", (18.125, 3.5), "R180"),
        ("OUTN", (1.875, 7.8125), "R180"), ("OUTP", (18.125, 7.8125), "R0"),
        ("CLK", (8.75, 0.5), "R90"), ("VSS", (10, 0.5), "R270"),
        ("VDD", (10, 12.125), "R90"),
    ]:
        ops.append(pin(name, origin, orient))

    def w(points, net=None, at=None, justification="lowerLeft"):
        ops.append(draw_wire(points, net, at, justification))

    w([(1.875, 3.5), (5.75, 3.5)], "INP", (2.125, 3.5))
    w([(14.25, 3.5), (18.125, 3.5)], "INN", (17.875, 3.5), "lowerRight")
    w([(6, 3.6875), (6, 6.3125)], "XP", (6, 4.25))
    w([(14, 3.6875), (14, 6.3125)], "XN", (14, 4.25))
    w([(6, 6.6875), (6, 9.3125)])
    w([(14, 6.6875), (14, 9.3125)])
    w([(1.875, 7.8125), (6, 7.8125)], "OUTN", (2.125, 7.8125))
    w([(14, 7.8125), (18.125, 7.8125)], "OUTP", (17.875, 7.8125), "lowerRight")
    w([(6, 3.3125), (6, 2.625), (14, 2.625), (14, 3.3125)], "TAIL", (10.375, 2.625))
    w([(10, 2.625), (10, 1.6875)])
    w([(10, 1.3125), (10, 0.5)], "VSS", (10, 0.8125))
    w([(8.75, 0.5), (8.75, 1.5), (9.75, 1.5)], "CLK", (8.75, 1.125))

    w([(3, 9.6875), (3, 11.25), (17, 11.25), (17, 9.6875)], "VDD", (10.5, 11.25))
    for x in (6, 14):
        w([(x, 9.6875), (x, 11.25)])
    w([(10, 11.25), (10, 12.125)])
    for x, output_x in ((3, 6), (17, 14)):
        w([(x, 9.3125), (x, 7.8125)])
        w([(x, 5.0625), (x, 4.75), (output_x, 4.75)])
        w([(x, 5.4375), (x, 6.0)], "VDD", (x, 6.0))

    w([(6.25, 9.5), (7.5, 9.5), (7.5, 6.5), (6.25, 6.5)])
    w([(13.75, 9.5), (12.5, 9.5), (12.5, 6.5), (13.75, 6.5)])
    w([(7.5, 8.3125), (14, 8.3125)], "OUTP", (8.0, 8.3125))
    w([(12.5, 7.3125), (6, 7.3125)], "OUTN", (12.0, 7.3125), "lowerRight")

    # Short body and common clock stubs keep the current paths visible.
    for x in (3, 17):
        for y in (5.25, 9.5):
            sign = 1 if x == 3 else -1
            w([(x, y), (x + 0.75 * sign, y)], "VDD", (x + 0.75 * sign, y),
              "lowerRight" if sign < 0 else "lowerLeft")
            w([(x - 0.25 * sign, y), (x - 1.125 * sign, y)], "CLK",
              (x - 1.125 * sign, y), "lowerRight" if sign > 0 else "lowerLeft")
    for x in (6, 14):
        outside = -1 if x == 6 else 1
        for y, net, sign in [
            (9.5, "VDD", outside), (6.5, "VSS", outside), (3.5, "VSS", -outside)
        ]:
            w([(x, y), (x + 0.75 * sign, y)], net, (x + 0.75 * sign, y),
              "lowerRight" if sign < 0 else "lowerLeft")
    w([(10, 1.5), (10.75, 1.5)], "VSS", (10.75, 1.5))

    ops += [
        note(1.875, 12.875, "Dynamic differential comparator", 0.20),
        note(7.8125, 9.875, "Cross-coupled latch", 0.14),
        note(7.875, 4.8125, "Differential input pair"),
        note(1.875, 0.0, "CLK low: preset XP/XN and OUTP/OUTN.  CLK high: evaluate."),
        note(1.875, -0.375, "INP > INN resolves OUTP high.  Body connections are explicit."),
    ]
    return ops, instances


def symbol():
    ops = []
    def line(points):
        ops.append('dbCreateLine(cv list("device" "drawing") list(' + " ".join(pt(*p) for p in points) + "))")
    def label(x, y, value, role="logical label", kind="normalLabel", size=0.1, justify="centerCenter"):
        ops.append(f'schCreateSymbolLabel(cv {pt(x, y)} {q(role)} {q(value)} {q(justify)} "R0" "roman" {size} {q(kind)})')
    anchors = {
        "INP": (0, 1.875), "INN": (0, 0.625), "OUTP": (3.25, 1.875),
        "OUTN": (3.25, 0.625), "CLK": (0.875, 0), "VDD": (1.625, 2.5), "VSS": (1.75, 0),
    }
    for points in [
        [(0.625, 0.375), (0.625, 2.125), (2.55, 1.25), (0.625, 0.375)],
        [(0, 1.875), (0.625, 1.875)], [(0, 0.625), (0.625, 0.625)],
        [(2.55, 1.25), (2.8, 1.25), (2.8, 1.875), (3.25, 1.875)],
        [(2.15, 1.0625), (2.45, 1.0625), (2.45, 0.625), (3.25, 0.625)],
        [(0.875, 0), (0.875, 0.4885)], [(1.625, 2.5), (1.625, 1.6705)],
        [(1.75, 0), (1.75, 0.8864)],
    ]:
        line(points)
    for x, y, value in [(0.85, 1.875, "+"), (0.85, 0.625, "-"), (1.51, 1.25, "CMP")]:
        label(x, y, value, size=0.15)
    for name, (x, y) in anchors.items():
        p1, p2 = pt(x - 0.0125, y - 0.0125), pt(x + 0.0125, y + 0.0125)
        ops.append(f'cmpSymbolPin({q(name)} {q(PORTS[name])} list({p1} {p2}))')
    for x, y, name in [
        (0.29, 2.0, "INP"), (0.29, 0.75, "INN"),
        (3.04, 2, "OUTP"), (3.04, 0.75, "OUTN"), (1.075, 0.2, "CLK"),
        (1.39, 2.3, "VDD"), (2.0, 0.2, "VSS"),
    ]:
        label(x, y, name, role="pin name")
    label(1.8125, 3, "[@instanceName]", role="instance label", kind="NLPLabel", size=0.12, justify="lowerLeft")
    label(1.8125, 2.775, "[@cellName]", kind="NLPLabel", size=0.095, justify="lowerLeft")
    ops.append('dbCreateRect(cv list("instance" "drawing") list(0:0 3.25:2.5))')
    names = "list(" + " ".join(q(port) for port in PORTS) + ")"
    ops.append(f'dbReplaceProp(cv "portOrder" "ILList" {names})')
    ops.append(f'unless(schSymbolToPinList("{LIB}" "{CORE}" "symbol") error("Symbol pin list failed"))')
    ops.append(f'unless(equal(schGetPinOrder(cv) {names}) error("Effective pin order differs"))')
    return ops


def testbench():
    ops, instances = [], {}
    def component(name, master, xy, pins, parameters, orientation="R0"):
        place(ops, instances, name, "analogLib", master, xy, orientation, pins, parameters)

    dut = {"INP": "INP", "INN": "INN", "OUTP": "OUTP", "OUTN": "OUTN",
           "CLK": "CLK", "VDD": "VDD", "VSS": "gnd!"}
    place(ops, instances, "XDUT", LIB, CORE, (8, 4), "R0", dut, {})
    grounds = []
    for suffix, y, polarity in [("P", 5.875, "+"), ("N", 4.625, "-")]:
        vin = "IN" + suffix
        vout = "OUT" + suffix
        source = "SRC_" + suffix
        component("VIN_" + suffix, "vdc", (3, y), {"PLUS": source, "MINUS": "gnd!"},
                  {"vdc": "VCM" + polarity + "VDIFF/2"})
        component("RS_" + suffix, "res", (4.875, y), {"PLUS": source, "MINUS": vin},
                  {"r": "RSRC"}, "R90")
        component("CL_" + suffix, "cap", (14.375, y), {"PLUS": vout, "MINUS": "gnd!"},
                  {"c": "CLVAL"})
        for points, net, at in [
            ([(3, y), (4.875, y)], source, (3.625, y)),
            ([(5.25, y), (8, y)], vin, (6.5, y)),
            ([(11.25, y), (14.375, y)], vout, (12.5, y)),
        ]:
            ops.append(draw_wire(points, net, at))
        grounds += [((3, y - 0.375), (3, y - 0.625)), ((14.375, y - 0.375), (14.375, y - 0.625))]

    component("VSUP", "vpulse", (5.75, 8.25), {"PLUS": "VDD", "MINUS": "gnd!"},
              {"v1": "0", "v2": "VDDVAL", "td": "0", "tr": "TRSUP", "tf": "100p",
               "pw": "1u", "per": "2u"})
    component("VCLK", "vpulse", (6.25, 2), {"PLUS": "CLK", "MINUS": "gnd!"},
              {"v1": "0", "v2": "VDDVAL", "td": "8n", "tr": "100p", "tf": "100p",
               "pw": "4n", "per": "10n"})
    ops += [
        draw_wire([(5.75, 8.25), (5.75, 8.75), (9.625, 8.75), (9.625, 6.5)], "VDD", (5.75, 8.75)),
        draw_wire([(8.875, 4), (8.875, 2.875), (6.25, 2.875), (6.25, 2)], "CLK", (8.875, 2.875)),
    ]
    grounds += [((5.75, 7.875), (5.75, 7.5)), ((6.25, 1.625), (6.25, 1.25)), ((9.75, 4), (9.75, 3.1875))]
    for i, (start, end) in enumerate(grounds):
        component(f"VGND{i + 1}", "gnd", end, {"gnd!": "gnd!"}, {})
        ops.append(draw_wire([start, end]))
    ops += [
        note(2, 9.125, "Dynamic comparator transient testbench", 0.20),
        note(2, 7.125, "Sources: VCM +/- VDIFF/2, each through RSRC"),
        note(11.75, 6.875, "CLVAL per output"),
        note(2, 0.625, "CLK: 100 MHz, preset then evaluate.  Supply rise: TRSUP."),
        note(2, 0.25, "Measure both input signs, delay, core supply energy and kickback."),
    ]
    return ops, instances

def view_procedure(name, cell, view, ops):
    view_type = "schematicSymbol" if view == "symbol" else "schematic"
    lines = [
        f"procedure({name}() let((cv result)",
        f'when(ddGetObj("{LIB}" "{cell}" "{view}") error("View already exists"))',
        f'cv=dbOpenCellViewByType("{LIB}" "{cell}" "{view}" "{view_type}" "w")',
        'unless(cv && cv~>DBUPerUU==160 error("This drawing requires 160 DBU/UU"))',
        *ops,
    ]
    if view == "schematic":
        lines.extend([
            'result=schCheck(cv)',
            f'printf("CMP_CHECK {cell} %L\\n" result)',
            'unless(equal(result list(0 0)) error("Review Check & Save errors or warnings"))',
        ])
    lines.extend([
        'unless(dbSave(cv) error("Cannot save cell view"))',
        'dbClose(cv)',
        f'cv=dbOpenCellViewByType("{LIB}" "{cell}" "{view}" "" "r")',
        f'cmpReadView(cv "{cell}-{view}.json")',
        'dbClose(cv) t))',
    ])
    return "\n".join(lines)


def write_build(stage, variant, pdk, mos_family="nominal", candidate_widths_um=None):
    core_ops, core = core_schematic(variant, pdk, mos_family, candidate_widths_um)
    tb_ops, tb = testbench()
    text = "\n".join([
        (HERE / "native_helpers.il").read_text(encoding="utf-8"),
        view_procedure("cmpCore", CORE, "schematic", core_ops),
        view_procedure("cmpSymbol", CORE, "symbol", symbol()),
        view_procedure("cmpBench", TB, "schematic", tb_ops),
        f"""procedure(cmpBuild() let((lib dest)
          cmpCheckPDK({q(pdk)})
          dest=strcat(getWorkingDir() "/{LIB}")
          when(ddGetObj("{LIB}") || isDir(dest) error("Benchmark library name already exists"))
          lib=ddCreateLib("{LIB}" dest)
          unless(lib && equal(lib~>readPath dest) error("Cannot create isolated comparator library"))
          techBindTechFile(lib {q(pdk)})
          cmpCore() cmpSymbol() cmpBench()
          printf("CMP_BUILD_OK\\n")
          t))
        unless(errset(cmpBuild() t) exit(3))
        exit(0)""",
    ])
    (stage / "build.il").write_text(text + "\n", encoding="utf-8")
    return {CORE: {"ports": PORTS, "instances": core}, TB: {"ports": {}, "instances": tb}}
