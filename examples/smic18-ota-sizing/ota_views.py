"""Draw a differential two-stage OTA, its feedback probe variant and its loaded bench."""
import math
from pathlib import Path

from schematic import MOS_MASTERS, draw_wire, note, pin, place, pt, q, view_procedure


LIB = "FDOTA18_BENCH"
BIAS, INPUT, OUTPUT, CMFB, CORE, PROBED = (
    "FDOTA18_" + name for name in ("BIAS", "INPUT", "OUTPUT", "CMFB", "OTA", "OTA_STB")
)
TB = "TB_FDOTA18_OTA"
SHARED = Path(__file__).resolve().parents[1] / "dynamic-comparator"
LEFT, RIGHT, TOP, BOTTOM = (-1, 0), (1, 0), (0, 1), (0, -1)


def ports(left, right, bottom=()):
    # Port order, origin and outward normal for both the symbol and its parent.
    result = {}
    for names, x, axis, direction in ((left, 0, LEFT, "input"), (right, 4, RIGHT, "output")):
        for i, name in enumerate(names):
            result[name] = (direction, (x, 1.5 if len(names) == 1 else 2.0 - i), axis)
    for name, x in bottom:
        result[name] = ("input", (x, 0), BOTTOM)
    result["VDD"] = ("inputOutput", (2, 3), TOP)
    result["VSS"] = ("inputOutput", (3, 0), BOTTOM)
    return result


INTERFACE = {
    BIAS: ports((), ("BN",)), INPUT: ports(("INP", "INN"), ("XP", "XN"), (("PG", 0.875), ("BN", 1.875))),
    OUTPUT: ports(("XP", "XN"), ("OUTP", "OUTN"), (("BN", 1),)),
    CMFB: ports(("OUTP", "OUTN"), ("PG",), (("VCM", 0.875), ("BN", 1.875))),
    CORE: ports(("INP", "INN"), ("OUTP", "OUTN"), (("VCM", 1),)),
}
INTERFACE[PROBED] = INTERFACE[CORE]


def interfaces(sizes):
    if "source_trim" not in sizes:
        return INTERFACE
    from ota_trim_views import CODE_PINS
    result = INTERFACE.copy()
    for cell in (INPUT,CORE,PROBED):
        result[cell] = dict(INTERFACE[cell],**{name:("input",(x,0),BOTTOM) for name,x in CODE_PINS})
    return result


def dimensions(lookup, length, current_scale, plate_um):
    row = lookup["corners"]["tt"]["budgets"][str(length)]
    branch = 2 * current_scale
    output = 3 * branch

    def width(current, role):
        density = row[role]["id_per_width_uA_um"]
        return max(0.45, round(current / density / 0.05) * 0.05)

    return dict(length_um=length, plate_um=plate_um, branch_uA=branch,
                input=width(branch, "first_N"), load=width(branch, "first_load_P"),
                driver=width(output, "output_P"), sink=width(output, "output_load_N"),
                reference=width(branch, "output_load_N"), tail=width(2 * branch, "output_load_N"),
                bias_ohm=(1.8 - row["output_load_N"]["gate_V"]) * 1e6 / branch,
                null_ohm=1e6 / (output * row["output_P"]["gm_id_V"]))


class Drawing:
    def __init__(self, cell, pdk, sizes):
        self.cell, self.pdk, self.sizes = cell, pdk, sizes
        self.interface = interfaces(sizes)
        self.ops, self.instances = [], {}

    def wire(self, *points, net=None, label=None, justify="lowerLeft"):
        self.ops.append(draw_wire(points, net, label, justify))

    def input_stage_lead(self, start, end, name, label):
        if "source_trim" in self.sizes:
            self.wire(start,end,net=name,label=label)
        else:
            self.wire(start,end)

    def text(self, x, y, text, size=0.14):
        self.ops.append(note(x, y, text, size))

    def port(self, name, x, y, orientation):
        self.ops.append(pin(name, (x, y), orientation, {n: v[0] for n, v in self.interface[self.cell].items()}))

    def device(self, name, lib, cell, xy, orientation, terms, props=None, cdf=False):
        place(self.ops, self.instances, name, lib, cell, xy, orientation, terms, props or {}, use_cdf=cdf)

    def mos(self, name, kind, x, y, gate_side, nets, group, pair=None, geometry=None):
        # x is the channel column. Mirroring exchanges the side of the gate and body stubs.
        length = self.sizes["length_um"]
        if group=="cm_input":
            length = self.sizes.get("cm_length_um",length)
        width,length = geometry if geometry is not None else self.sizes.get("paired_geometry",{}).get(pair,(self.sizes[group],length))
        master = MOS_MASTERS[self.sizes.get("mos_family","nominal")][{"n":"NMOS","p":"PMOS"}[kind]]
        self.device(name, self.pdk, master, (x + 0.25 * gate_side, y), "R0" if gate_side < 0 else "MY",
                    dict(zip(("D", "G", "S", "B"), nets)),
                    dict(l="{:g}u".format(length), fw="{:g}u".format(width),
                         fingers="1", m="1"), True)
        end = (x - gate_side * 0.75, y)
        self.wire((x, y), end, net=nets[3], label=end, justify="lowerRight" if gate_side > 0 else "lowerLeft")
        self.text(x + 0.17, y + 0.26, "{}  {:g}/{:g}".format(name,width,length),0.105)

    def resistor(self, name, xy, plus, minus, value, label_x=None, width_um=2):
        segments = math.ceil(value / 45000)
        self.device(name, self.pdk, "rhrpo", xy, "R0", {"PLUS": plus, "MINUS": minus},
                    dict(segments=segments, connection="Series", calculatedParam="Length", m="1",
                         segW="{:g}u".format(width_um), r="{:.7g}".format(value)), True)
        if name == "RB":
            self.text(xy[0]+1.1, xy[1]-0.125, "{}  {:.3g} MOhm".format(name, value/1e6), 0.115)
        else:
            self.text(xy[0]+0.15 if label_x is None else label_x, xy[1]-0.25,
                      "{}  {:.3g} kOhm".format(name, value/1000), 0.1)

    def block(self, name, cell, xy, terms):
        self.device(name, LIB, cell, xy, "R0", terms)

    def stub(self, xy, axis, net, distance=0.875):
        x, y = xy
        end = (x + axis[0] * distance, y + axis[1] * distance)
        self.wire(xy, end, net=net, label=end, justify="lowerRight" if axis == LEFT else "lowerLeft")


def symbol(cell,interface=INTERFACE):
    ops = []

    def line(*points):
        ops.append('dbCreateLine(cv list("device" "drawing") list({}))'.format(" ".join(pt(*p) for p in points)))

    def label(x, y, text, size=0.11, role="logical label", kind="normalLabel", justify="centerCenter"):
        ops.append('schCreateSymbolLabel(cv {} {} {} {} "R0" "roman" {} {})'.format(
            pt(x, y), q(role), q(text), q(justify), size, q(kind)))

    # A compact clipped triangle carries two separate output pins and straight outward leads.
    amplifier = cell in (CORE, PROBED)
    if amplifier:
        line((0.5, 0.35), (0.5, 2.65), (3.5, 2.05), (3.5, 0.95), (0.5, 0.35))
    else:
        line((0.5, 0.45), (0.5, 2.55), (3.5, 2.55), (3.5, 0.45), (0.5, 0.45))
    titles = {BIAS: "IREF", INPUT: "gm", OUTPUT: "Av", CMFB: "CMFB", CORE: "OTA", PROBED: "OTA"}
    label(2.0, 1.52, titles[cell], 0.25)
    for name, (direction, (x, y), axis) in interface[cell].items():
        ops.append("cmpSymbolPin({} {} list({} {}))".format(q(name), q(direction),
                   pt(x - 0.0125, y - 0.0125), pt(x + 0.0125, y + 0.0125)))
        if axis in (LEFT, RIGHT):
            edge = 0.5 if axis == LEFT else 3.5
            line((x, y), (edge, y))
            label(edge + (0.12 if axis == LEFT else -0.12), y + (-0.18 if amplifier and axis==RIGHT and y==2 else 0.09), name,
                  role="pin name", justify="lowerLeft" if axis == LEFT else "lowerRight")
        else:
            top = axis == TOP
            edge = (2.65 - 0.2 * (x - 0.5) if top else 0.35 + 0.2 * (x - 0.5)) if amplifier else (2.55 if top else 0.45)
            line((x, y), (x, edge))
            if not top and "T0" in interface[cell]:
                label(x+.045,.105 if name.startswith("T") else .225,name,.09,role="pin name",justify="lowerLeft")
            else:
                label(x + 0.10, 2.68 if top else 0.14, name, role="pin name", justify="lowerLeft")
    label(3.75, 2.83, "[@instanceName]", 0.115, "instance label", "NLPLabel", "lowerRight")
    ops.append('dbCreateRect(cv list("instance" "drawing") list(0:0 4:3))')
    order = "list(" + " ".join(q(n) for n in interface[cell]) + ")"
    ops.extend(['dbReplaceProp(cv "portOrder" "ILList" {})'.format(order),
                'unless(schSymbolToPinList({} {} "symbol") error("Missing symbol"))'.format(q(LIB), q(cell)),
                'unless(equal(schGetPinOrder(cv) {}) error("Incorrect symbol port order"))'.format(order)])
    return ops


def bias(d):
    d.port("VDD", 4, 7, "R90")
    d.port("VSS", 4, 0.75, "R270")
    d.port("BN", 7, 3.5, "R0")
    d.resistor("RB", (4, 5.75), "VDD", "BN", d.sizes["bias_ohm"])
    d.mos("MREF", "n", 4, 2.5, -1, ("BN", "BN", "VSS", "VSS"), "reference")
    d.wire((4, 7), (4, 5.75))
    d.wire((4, 5.375), (4, 3.5), (4, 2.6875))
    d.wire((4, 3.5), (7, 3.5))
    d.wire((3.75, 2.5), (3.125, 2.5), (3.125, 3.125), (4, 3.125))
    d.wire((4, 2.3125), (4, 0.75))
    d.text(1.75, 7.75, "Supply-referenced bias", 0.20)
    d.text(1.75, 0.1, "RB starts the diode branch as VDD rises.")


def input_stage(d):
    trimmed = "source_trim" in d.sizes
    for side, x, sign, edge in (("P", 4, -1, 1), ("N", 12, 1, 15)):
        inp, node = "IN" + side, "X" + side
        source = "S"+side+"0" if trimmed else "TAIL"
        d.mos("MIN" + side, "n", x, 3.75, sign, (node, inp, source, "VSS"), "input", "input_pair")
        d.mos("ML" + side, "p", x, 6.75, -sign, (node, "PG", "VDD", "VDD"), "load")
        d.wire((x, 3.9375), (x, 5), (x, 6.5625))
        d.port(node, edge, 5, "R180" if sign < 0 else "R0")
        d.input_stage_lead((x,5),(edge,5),node,(edge-sign*.625,5))
        d.port(inp, edge, 3.75, "R0" if sign < 0 else "R180")
        d.input_stage_lead((x+.25*sign,3.75),(edge,3.75),inp,(edge-sign*.625,3.75))
    d.wire((4.25, 6.75), (7.5, 6.75), (11.75, 6.75))
    d.port("PG", 7.5, 5.625, "R90")
    d.input_stage_lead((7.5,5.625),(7.5,6.75),"PG",(7.5,5.875))
    d.wire((4, 6.9375), (4, 7.875), (8, 7.875), (12, 7.875), (12, 6.9375))
    d.port("VDD", 8, 8.625, "R90")
    d.input_stage_lead((8,8.625),(8,7.875),"VDD",(8,8.25))
    source_y,delta = 3.5625,0
    if trimmed:
        from ota_trim_views import source_bank
        source_y = source_bank(d)
        delta = source_y-3.5625
    d.wire((4, source_y), (4, 2.875+delta), (8, 2.875+delta), (12, 2.875+delta), (12, source_y),
           net="TAIL", label=(6, 2.875+delta))
    d.mos("MTAIL", "n", 8, 1.75+delta, -1, ("TAIL", "BN", "VSS", "VSS"), "tail")
    d.wire((8, 2.875+delta), (8, 1.9375+delta))
    d.port("BN", 5.5, 1.75+delta, "R0")
    d.input_stage_lead((5.5,1.75+delta),(7.75,1.75+delta),"BN",(6,1.75+delta))
    d.port("VSS", 8, 0.625+delta, "R270")
    d.input_stage_lead((8,1.5625+delta),(8,.625+delta),"VSS",(8,.875+delta))
    d.text(1, 8.75, "Differential input pair", 0.20)


def output_stage(d):
    for side, x, sign, edge in (("P", 5, -1, 1), ("N", 13, 1, 17)):
        node, out, z = "X" + side, "OUT" + side, "Z" + side
        d.mos("MO" + side, "p", x, 6.5, sign, (out, node, "VDD", "VDD"), "driver")
        d.mos("MS" + side, "n", x, 2, -sign, (out, "BN", "VSS", "VSS"), "sink")
        d.wire((x, 6.3125), (x, 3), (x, 2.1875))
        xp = x + sign * 1.875
        d.port(node, edge, 6.5, "R0" if sign < 0 else "R180")
        d.wire((edge, 6.5), (xp, 6.5), (x + 0.25 * sign, 6.5))
        dim = "{:g}u".format(d.sizes["plate_um"])
        d.device("CC" + side, d.pdk, "mim2", (xp, 5.625), "R0", {"PLUS": node, "MINUS": z},
                 dict(calculatedParam="Capacitance", w=dim, l=dim, m="1"), True)
        d.text(xp+0.15, 5.4, "CC{}  {:g} um square".format(side, d.sizes["plate_um"]), 0.095)
        d.resistor("RZ" + side, (xp, 4.25), z, out, d.sizes["null_ohm"])
        d.wire((xp, 6.5), (xp, 5.625))
        d.wire((xp, 5.25), (xp, 4.25), net=z, label=(xp, 4.75))
        d.wire((xp, 3.875), (xp, 3))
        d.port(out, edge, 3, "R180" if sign < 0 else "R0")
        d.wire((edge, 3), (xp, 3), (x, 3))
    d.wire((5.25, 2), (8, 2), (12.75, 2))
    d.port("BN", 8, 1.25, "R90")
    d.wire((8, 1.25), (8, 2))
    for y, end, net, ori in ((6.6875, 7.5, "VDD", "R90"), (1.8125, 0.75, "VSS", "R270")):
        py = end + (0.75 if net == "VDD" else -0.75)
        d.wire((5, y), (5, end), (9, end), (13, end), (13, y))
        d.port(net, 9, py, ori)
        d.wire((9, end), (9, py))
    d.text(1, 8.5, "Second stage and series Miller compensation", 0.20)


def common_mode(d):
    # Each output has a matched sense/reference pair. Sense drains and reference drains sum separately.
    detector_role = "cm_input" if "cm_input" in d.sizes else "input"
    for side, x in (("P", 5), ("N", 15)):
        tail = "TC" + side
        for name, col, sign, gate, drain in (("MDET", x - 2, -1, "OUT" + side, "PG"),
                                             ("MCREF", x + 2, 1, "VCM", "RCG")):
            d.mos(name + side, "n", col, 4, sign, (drain, gate, tail, "VSS"), detector_role)
            d.stub((col + sign * 0.25, 4), (sign, 0), gate, 1)
            rail = 6 if drain == "PG" else 5.125
            d.wire((col, 4.1875), (col, rail))
        d.wire((x-2, 3.8125), (x-2, 3), (x, 3), (x+2, 3), (x+2, 3.8125), net=tail, label=(x-1, 3))
        d.mos("MCT" + side, "n", x, 1.875, -1, (tail, "BN", "VSS", "VSS"), "reference", "cm_tails")
        d.wire((x, 3), (x, 2.0625))
        d.stub((x-0.25, 1.875), LEFT, "BN")
    bridge = d.sizes.get("cm_bridge_ohm")
    for name, x, drain, y in (("MCM", 3, "PG", 6), ("MDMY", 17, "RCG", 5.125)):
        sign = -1 if x == 3 else 1
        if bridge:
            sign = -sign
        gate = "RCG" if bridge else drain
        d.mos(name, "p", x, 7.5, sign, (drain, gate, "VDD", "VDD"), "load", "cm_loads")
        d.wire((x, 7.3125), (x, y))
        if not bridge:
            d.wire((x+0.25*sign, 7.5), (x+sign, 7.5), (x+sign, 6.625), (x, 6.625))
    if bridge:
        d.wire((3.25, 7.5), (10, 7.5), (16, 7.5), (16.75, 7.5))
        d.wire((16, 7.5), (16, 6.625), (17, 6.625))
        d.resistor("RREC", (10, 7), "RCG", "PG", bridge, label_x=10.5, width_um=1)
        d.wire((10, 7.5), (10, 7))
        d.wire((10, 6.625), (10, 6))
    d.wire((3, 6), (13, 6), net="PG", label=(5, 6))
    d.wire((7, 5.125), (17, 5.125), net="RCG", label=(10, 5.125))
    d.port("PG", 0.75, 6.625, "R180")
    d.wire((0.75, 6.625), (3 if bridge else 2, 6.625))
    d.wire((3, 7.6875), (3, 8.5), (10, 8.5), (17, 8.5), (17, 7.6875))
    d.wire((5, 1.6875), (5, 0.75), (10, 0.75), (15, 0.75), (15, 1.6875))
    for name, x, y, axis in (("VDD", 10, 9.25, BOTTOM), ("VSS", 10, 0, TOP)):
        d.port(name, x, y, "R90" if name == "VDD" else "R270")
        d.wire((x, y), (x, y + axis[1]*0.75))
    for name, x, y in (("OUTP", 0.75, 4), ("OUTN", 10.75, 4)):
        d.port(name, x, y, "R0")
        d.wire((x, y), (x+1, y))
    for name, x, y in (("VCM", 8.875, 4), ("BN", 3, 1.875)):
        d.port(name, x, y, "R180" if name == "VCM" else "R0")
        d.wire((x, y), (8.25 if name == "VCM" else 3.875, y))
    d.text(1.0, 9.5, "Output common-mode detector", 0.2)
    if bridge:
        d.text(4.25, 8, "PG drives the input loads. RREC sets CM feedback gain.", 0.14)
    else:
        d.text(6, 6.55, "PG sums the two sense currents.")


def amplifier(d):
    probed = d.cell == PROBED
    for name, cell, xy in (("XI", INPUT, (7, 9)), ("XO", OUTPUT, (16, 9)),
                           ("XB", BIAS, (1, 2)), ("XC", CMFB, (10, 2))):
        terms = {n: n for n in d.interface[cell]}
        if name == "XC" and probed:
            terms["PG"] = "CMG"
        d.block(name, cell, xy, terms)
        for n, (_, (px, py), axis) in d.interface[cell].items():
            if n in ("VDD", "VSS", "BN"):
                d.stub((xy[0]+px, xy[1]+py), axis, n)
            elif n in ("T0","T1","T2","T3"):
                x,y = xy[0]+px,xy[1]+py
                end = y-(1.5 if n in ("T0","T2") else 1.0)
                d.wire((x,y),(x,end),net=n,label=(x,end+.1875))
                d.port(n,x,end,"R270")
    for i, letter in enumerate(("P", "N")):
        y = 11 - i
        d.port("IN"+letter, 2.75, y, "R0")
        d.wire((2.75, y), (7, y), net="IN"+letter, label=(3.125,y))
        d.wire((11, y), (16, y), net="X"+letter, label=(13, y))
        d.port("OUT"+letter, 24, y, "R0")
        d.wire((20, y), (24, y), net="OUT"+letter, label=(21.5, y))
        d.stub((10, 4-i), LEFT, "OUT"+letter, 1.75)
    d.port("VCM", 10.875, 0.375, "R90")
    d.wire((10.875, 2), (10.875, 0.375), net="VCM", label=(10.875,0.75))
    d.port("VDD", 9, 14, "R90")
    d.wire((9, 14), (9, 12.875))
    d.port("VSS", 4, 0, "R270")
    d.wire((4, 0), (4, 1.125))
    if probed:
        d.device("PCM", "analogLib", "iprobe", (15.5625, 3.8125), "R0", {"PLUS":"CMG", "MINUS":"PG"})
        d.wire((14, 3.5), (15.5, 3.5), net="CMG", label=(14.5, 3.5))
        start = (15.625, 3.5)
    else:
        start = (14, 3.5)
    d.wire(start, (17.5, 3.5), (17.5, 6.75), (7.875, 6.75), (7.875, 9), net="PG", label=(13, 6.75))
    d.text(1, 14.5, "Two-stage fully differential OTA", 0.22)
    d.text(1, 6.25, "Supply-derived bias", 0.14)
    d.text(10, 6.25, "Output common-mode feedback", 0.14)
    if probed:
        d.text(15, 2.45, "PCM: zero-volt CM loop probe", 0.12)


def bench(d):
    large_step = d.sizes.get("fullscale_bench",False)
    amplitude = "300m" if large_step else "50m"
    common_step = "0" if large_step else "20m"
    input_gain = "ICMGAIN" if large_step else "0.6"
    terms = {n: n for n in d.interface[CORE]}
    terms["VSS"] = "gnd!"
    d.block("XDUT", PROBED, (10, 5), terms)
    # The two output loads are real bench capacitors. Ideal independent and controlled
    # sources only implement external stimuli, the input splitter and selectable unity feedback.
    for i, letter in enumerate(("P", "N")):
        y, x = 7 - i, 17.5 - 2*i
        d.stub((10, y), LEFT, "IN"+letter, 1.75)
        d.wire((14, y), (x, y), (x, 4.5), net="OUT"+letter, label=(x-1, y))
        d.device("CL"+letter, "analogLib", "cap", (x, 4.5), "R0", {"PLUS":"OUT"+letter, "MINUS":"gnd!"}, {"c":"2p"})
        ground(d, x, 4.125, "L"+letter)
    d.stub((12, 8), TOP, "VDD")
    d.stub((11, 5), BOTTOM, "VCM")
    ground(d, 13, 5, "D")
    for name, x, plus, props in (
        ("VSUP", 2, "VDD", dict(vdc="VDDVAL", v1="0", v2="VDDVAL", td="0", tr="1u", tf="10n", pw="20u", per="40u")),
        ("VD", 5, "CMD", dict(vdc="0", acm="1", v1="-"+amplitude, v2=amplitude, td="4u", tr="5n", tf="5n", pw="2u", per="20u")),
        ("VOFF", 8, "OFF", dict(vdc="0", v1="0", v2=common_step, td="8u", tr="5n", tf="5n", pw="5u", per="20u")),
    ):
        d.device(name, "analogLib", "vpulse", (x, 10), "R0", {"PLUS":plus,"MINUS":"gnd!"}, props)
        labels = {"VSUP":"VSUP  power ramp","VD":"VD  +/-300 mV" if large_step else "VD  +/-50 mV",
                  "VOFF":"VOFF  0 V" if large_step else "VOFF  20 mV"}
        d.text(x+0.22,9.75,labels[name],0.105)
        d.stub((x, 10), TOP, plus)
        ground(d, x, 9.625, name)
    controls = [
        ("EVIC", 2, 5.25, "VIC", "gnd!", "VDD", "gnd!", input_gain),
        ("EVCM", 5, 5.25, "VCM", "OFF", "VDD", "gnd!", "0.5"),
        ("EFB", 2, 1.5, "SUM", "CMD", "OUTP", "OUTN", "-BETA"),
        ("EIP", 7, 1.5, "INP", "VIC", "ERR", "gnd!", "0.5"),
        ("EIN", 11, 1.5, "INN", "VIC", "ERR", "gnd!", "-0.5"),
    ]
    if "source_trim" in d.sizes:
        from ota_trim_views import CODE_PINS, bit_gain
        for bit,(gate,offset) in enumerate(CODE_PINS):
            d.stub((10+offset,5),BOTTOM,gate,1.125 if bit%2==0 else .625)
            controls.append(("EB"+str(bit),2+4*bit,-2,gate,"gnd!","VDD","gnd!",
                             bit_gain(d.sizes["source_trim"]["code"],bit)))
        d.text(1,-.875,"Static trim word T3..T0 (T0 = LSB). Each bit drives a CMOS gate.",.12)
    for name, x, y, plus, minus, sensep, sensen, gain in controls:
        d.device(name, "analogLib", "vcvs", (x, y), "R0",
                 {"PLUS":plus, "MINUS":minus, "NC+":sensep, "NC-":sensen}, {"egain":gain})
        d.text(x+0.18, y-0.25, "{}  {}".format(name, gain), 0.10)
        d.stub((x, y), TOP, plus, 0.75)
        d.stub((x, y-0.375), BOTTOM, minus, 0.625)
        # Control pins are separated by 0.125. Spread their labels onto two levels.
        d.wire((x-0.125, y-0.125), (x-0.625, y-0.125), (x-0.625, y+0.375),
               net=sensep, label=(x-0.625, y+0.375), justify="lowerRight")
        d.wire((x-0.125, y-0.25), (x-0.625, y-0.25), (x-0.625, y-0.75),
               net=sensen, label=(x-0.625, y-0.75), justify="lowerRight")
    d.device("PDM", "analogLib", "iprobe", (4.5, 1.8125), "R0", {"PLUS":"SUM", "MINUS":"ERR"})
    d.stub((4.4375, 1.5), LEFT, "SUM", 0.625)
    d.stub((4.5625, 1.5), RIGHT, "ERR", 0.625)
    d.text(1, 12, "Full-scale differential settling" if large_step else "Loaded differential OTA testbench", 0.22)
    d.text(11, 10.5, "2 pF per output to ground. VCM target = VDD/2.", 0.14)
    d.text(1,-0.25,"BETA = 1. ICMGAIN = input common-mode voltage / VDDVAL." if large_step else
           "BETA = 0 for differential gain. BETA = 1 for both loops and step response.")


def ground(d, x, y, name):
    d.device("GND"+name, "analogLib", "gnd", (x, y-0.625), "R0", {"gnd!":"gnd!"})
    d.wire((x, y), (x, y-0.625))


def sources(pdk, sizes):
    scripts = [(SHARED / "native_helpers.il").read_text(encoding="utf-8")]
    expected, calls = {}, []
    for i, (cell, draw) in enumerate(((BIAS,bias), (INPUT,input_stage), (OUTPUT,output_stage), (CMFB,common_mode),
                                      (CORE,amplifier), (PROBED,amplifier), (TB,bench))):
        d = Drawing(cell, pdk, sizes)
        draw(d)
        expected[cell] = d.instances
        for view, ops in ([("schematic", d.ops)] if cell == TB else [("schematic", d.ops), ("symbol", symbol(cell,d.interface))]):
            proc = "fdView{}{}".format(i, view)
            scripts.append(view_procedure(proc, cell, view, ops, LIB))
            calls.append(proc+"()")
    if "source_trim" in sizes:
        from ota_trim_views import effective_cdf_source
        scripts.append(effective_cdf_source(LIB,INPUT))
        calls.append("fdReadTrimCDF()")
    scripts.append(f'''procedure(fdBuild() let((lib dest)
      cmpCheckPDK({q(pdk)}) dest=strcat(getWorkingDir() "/{LIB}")
      when(ddGetObj("{LIB}") || isDir(dest) error("Target library already exists"))
      lib=ddCreateLib("{LIB}" dest)
      unless(lib && equal(lib~>readPath dest) error("Cannot create isolated library"))
      techBindTechFile(lib {q(pdk)}) {" ".join(calls)}
      printf("FDOTA_BUILD_OK\\n") t))
      unless(errset(fdBuild() t) exit(3)) exit(0)''')
    return "\n".join(scripts) + "\n", expected
