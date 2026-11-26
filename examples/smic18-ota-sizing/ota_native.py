"""Build in a new OA library, inspect the saved terminal maps and export the native Spectre netlists."""
import math
import re
from collections import Counter

from audit_native import outward_leads, spice_number
from schematic import q
from spectre_run import execute, read_json
from ota_views import BIAS, CMFB, CORE, INPUT, OUTPUT, PROBED, TB, LIB, INTERFACE, sources


def netlist(folder, site, cell):
    target = folder / cell
    target.mkdir()
    (target / ".cdsinit").write_text("; Isolated SI export\n", encoding="utf-8")
    (target / "si.env").write_text(
        f'simLibName="{LIB}"\nsimCellName="{cell}"\nsimViewName="schematic"\nsimSimulator="spectre"\n'
        'simNotIncremental=nil\nsimReNetlistAll=t\nsimViewList=list("spectre" "veriloga" "schematic")\n'
        'simStopList=list("spectre" "veriloga")\nsimNetlistHier=t\n', encoding="utf-8")
    execute([site["si"], "-batch", "-cdslib", folder / "cds.lib", "-command", "nl"], target, "si.stdout", 180)


def build(folder, site, sizes):
    folder.mkdir()
    script, _ = sources(site["pdk_library"], sizes)
    (folder / "cds.lib").write_text("INCLUDE " + q(str(site["cds_lib"])) + "\n", encoding="utf-8")
    (folder / ".cdsinit").write_text("; Isolated OTA case\n", encoding="utf-8")
    (folder / "build.il").write_text(script, encoding="utf-8")
    execute([site["virtuoso"], "-nograph", "-restore", folder / "build.il", "-log", folder / "build.log"],
            folder, "build.stdout", 240)
    for cell in (TB, CORE):
        netlist(folder, site, cell)
    (folder / "open.il").write_text(
        "".join(f'geOpen(?lib "{LIB}" ?cell "{cell}" ?view "{view}" ?mode "r")\n'
                for cell, view in ((CORE, "schematic"), (CORE, "symbol"), (TB, "schematic"))), encoding="utf-8")


def views(folder, site, sizes):
    script, expected = sources(site["pdk_library"], sizes)
    if (folder / "build.il").read_text(encoding="utf-8") != script:
        raise ValueError("Native drawing source differs from the frozen sizes")
    log = (folder / "build.log").read_text(encoding="utf-8", errors="replace")
    if "FDOTA_BUILD_OK" not in log:
        raise ValueError("Native build did not finish")
    measured = {}
    for cell, instances in expected.items():
        if re.findall(r"CMP_CHECK " + cell + r" (\([^\n]*\))", log) != ["(0 0)"]:
            raise ValueError("Missing clean Check & Save for " + cell)
        data = read_json(folder / (cell + "-schematic.json"))
        interface = INTERFACE.get(cell, {})
        if {p["name"]:p["direction"] for p in data["ports"]} != {n:t[0] for n,t in interface.items()}:
            raise ValueError("Unexpected native ports in " + cell)
        if interface:
            symbol = read_json(folder / (cell + "-symbol.json"))
            if {p["name"]:(p["direction"],p["xy"]) for p in symbol["ports"]} != {
                n:(t[0],list(t[1])) for n,t in interface.items()
            }:
                raise ValueError("Unexpected symbol anchors or directions in " + cell)
        actual = {i["name"]:i for i in data["instances"] if i["lib"] != "basic"}
        if actual.keys() != instances.keys():
            raise ValueError("Unexpected instance list in " + cell)
        for name, spec in instances.items():
            i = actual[name]
            terms = {t["name"]:t["net"] for t in i["terms"]}
            if (i["lib"],i["cell"],i["xy"],i["orient"],terms) != (
                spec["lib"],spec["cell"],list(spec["xy"]),spec["orient"],spec["terms"]
            ):
                raise ValueError("{} / {} native connectivity differs: {} vs {}".format(cell, name, terms, spec["terms"]))
            if spec["lib"] == LIB:
                outward_leads(data["shapes"], i, {n:t[2] for n,t in INTERFACE[spec["cell"]].items()})
        measured[cell] = actual
    return measured


def exported(folder, cell):
    target = folder / cell
    if not re.search(r"(?m)^End netlisting ", (target / "si.stdout").read_text(encoding="utf-8", errors="replace")):
        raise ValueError("SI did not complete for " + cell)
    text = (target / "netlist").read_text(encoding="utf-8")
    return re.sub(r"\\\r?\n\s*", " ", text)


def cards(body):
    result = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith(("//", "parameters ")):
            continue
        match = re.fullmatch(r"\s*(\w+)\s+\(([^)\n]*)\)\s+(\w+)\s*(.*?)\s*", line)
        if not match or match[1] in result:
            raise ValueError("Unexpected or duplicated native instance: " + line)
        pairs = dict(re.findall(r"(\w+)=(\(?[^\s)]+\)?)", match[4]))
        result[match[1]] = (match[2].split(), match[3], pairs)
    return result


def hierarchy(text):
    defs = {}
    pattern = re.compile(r"(?ms)^subckt (\w+) ([^\n]+)\n(.*?)^ends \1[ \t]*$")
    for m in pattern.finditer(text):
        if m[1] in defs:
            raise ValueError("Duplicated subcircuit " + m[1])
        defs[m[1]] = (m[2].split(), cards(m[3]))
    return defs, cards(pattern.sub("", text))


def cdf_value(props, key):
    token = str(props[key])
    for suffix, multiplier in (("K", 1e3), ("M", 1e6), ("f", 1e-15)):
        if token.endswith(suffix):
            return float(token[:-1]) * multiplier
    return spice_number(token)


def audit(folder, site, sizes):
    actual = views(folder, site, sizes)
    _, design = sources(site["pdk_library"], sizes)
    bench = exported(folder, TB)
    defs, parent = hierarchy(bench)
    plain_defs, plain = hierarchy(exported(folder, CORE))
    audited = {}
    probes = defs[PROBED][1].copy()
    if probes.pop("PCM", None) != (["CMG","PG"], "iprobe", {}):
        raise ValueError("CM feedback probe differs from the intended zero-volt series element")
    for name, (nets, master, params) in probes.items():
        probes[name] = (["PG" if n=="CMG" else n for n in nets], master, params)
    if probes != plain or {n:v for n,v in defs.items() if n != PROBED} != plain_defs:
        raise ValueError("Shorting the test probe must reproduce the independent ordinary OTA netlist")
    areas = dict(mos_gate_um2=0.0, resistor_strip_um2=0.0, mim_plate_um2=0.0)
    for cell in (INPUT, OUTPUT, BIAS, CMFB, PROBED, TB):
        instances = parent if cell == TB else defs[cell][1]
        plans = {k:v for k,v in design[cell].items() if v["cell"] != "gnd"}
        if instances.keys() != plans.keys():
            raise ValueError("Unexpected SI instance inventory in " + cell)
        for name, (nets, master, params) in instances.items():
            spec = plans[name]
            terminals = {k:("0" if v=="gnd!" else v) for k,v in spec["terms"].items()}
            props = {k:v for k, _, v in actual[cell][name]["props"]}
            device = spec["cell"]
            if device in ("n18", "p18"):
                order = list("DGSB")
                values = {k:spice_number(params[k]) for k in ("w","l","nf","m")}
                if (master != device or values["nf"] != 1 or values["m"] != 1
                        or not all(math.isclose(values[k], cdf_value(props,k)) for k in ("w","l"))
                        or not math.isclose(values["w"], spice_number(spec["parameters"]["fw"]))
                        or not math.isclose(values["l"], spice_number(spec["parameters"]["l"]))):
                    raise ValueError("MOS model, width or length differs for " + name)
                areas["mos_gate_um2"] += values["w"] * values["l"] * 1e12
            elif device == "mim2":
                order = ["PLUS","MINUS"]
                if (master != device or spice_number(params["m"]) != 1 or not all(
                    math.isclose(spice_number(params[k]), sizes["plate_um"]*1e-6) and
                    math.isclose(cdf_value(props,k), spice_number(params[k])) for k in ("l","w"))):
                    raise ValueError("MIM dimensions differ for " + name)
                areas["mim_plate_um2"] += spice_number(params["l"]) * spice_number(params["w"]) * 1e12
                audited[name] = dict(capacitance_pF=cdf_value(props,"c")*1e12, side_um=sizes["plate_um"])
            elif device == "rhrpo":
                order, resistors = defs[master]
                if set(order) != {"PLUS","MINUS"} or spice_number(params["m"]) != 1:
                    raise ValueError("Unexpected resistor wrapper")
                length, width = [spice_number(params[k]) * 1e6 for k in ("segL","segW")]
                if not (math.isclose(length, cdf_value(props,"segL")*1e6) and math.isclose(width, 2)):
                    raise ValueError("Resistor strip geometry differs")
                # Walk from one end to the other. Reject parallel, disconnected and extra segments.
                edges = []
                for pair, model, p in resistors.values():
                    if model != device or p != {"m":"1","l":"segL","w":"segW"} or len(pair)!=2 or pair[0]==pair[1]:
                        raise ValueError("Unexpected process resistor model")
                    edges.append(pair)
                count = spec["parameters"]["segments"]
                degree = Counter(n for pair in edges for n in pair)
                if (len(edges) != count or degree["PLUS"]!=1 or degree["MINUS"]!=1
                        or any(d!=2 for n,d in degree.items() if n not in order)):
                    raise ValueError("Unexpected series resistor structure")
                node = "PLUS"
                while True:
                    edge = next((e for e in edges if node in e), None)
                    if edge is None:
                        break
                    edges.remove(edge)
                    node = edge[1] if edge[0] == node else edge[0]
                if edges or node != "MINUS":
                    raise ValueError("Open resistor chain")
                resistance = cdf_value(props, "r")
                if not math.isclose(resistance, float(spec["parameters"]["r"]), rel_tol=.001):
                    raise ValueError("CDF resistance differs from the sizing target")
                areas["resistor_strip_um2"] += length * width * count
                audited[name] = dict(ohm=resistance, segments=count, segment_length_um=length, segment_width_um=width)
            elif spec["lib"] == LIB:
                order = defs[master][0]
                if master != device or set(order) != set(INTERFACE[device]) or len(set(order))!=len(order):
                    raise ValueError("Unexpected native hierarchy for " + name)
            else:
                order = ["PLUS","MINUS","NC+","NC-"] if device=="vcvs" else ["PLUS","MINUS"]
                masters = {"vpulse":"vsource", "vcvs":"vcvs", "cap":"capacitor", "iprobe":"iprobe"}
                if master != masters[device]:
                    raise ValueError("Unexpected fixture or probe model")
                if device=="vcvs" and params.get("gain")!=spec["parameters"]["egain"]:
                    raise ValueError("Incorrect feedback or common-mode gain")
                if device=="cap" and not math.isclose(cdf_value(params,"c"),2e-12):
                    raise ValueError("Expected 2 pF on each output")
                if device=="vpulse":
                    keys = {"vdc":"dc","v1":"val0","v2":"val1","td":"delay","tr":"rise","tf":"fall",
                            "pw":"width","per":"period","acm":"mag"}
                    if params.get("type")!="pulse" or any(params.get(keys[k])!=v for k,v in spec["parameters"].items()):
                        raise ValueError("Incorrect native source parameters for " + name)
            if nets != [terminals[n] for n in order]:
                raise ValueError("SI terminal order or node differs for " + cell + "/" + name)
    return bench, dict(area={k:round(v,6) for k,v in areas.items()}, passives=audited)
