"""Verify native OA readback, SI device parameters and the seven parent pin leads."""

import math
import re

from schematic import CORE, TB, PORTS, TOPOLOGY, MOS_MASTERS


OUTWARD = {"INP": (-1, 0), "INN": (-1, 0), "OUTP": (1, 0), "OUTN": (1, 0),
           "VDD": (0, 1), "CLK": (0, -1), "VSS": (0, -1)}


def check_family(netlist, family):
    models = {d["instance"]: MOS_MASTERS[family][d["type"]] for d in TOPOLOGY["devices"]}
    actual = re.findall(r"(?m)^\s*(M\w+)\s+\([^)\n]+\)\s+(\w+)", netlist)
    if len(actual) != len(models) or dict(actual) != models:
        raise ValueError("Saved comparator MOS masters differ from the configured mos_family")


def outward_leads(shapes, instance, directions=OUTWARD):
    leads = {}
    for term in instance["terms"]:
        origin = term["xy"]
        ends = []
        for shape in shapes:
            points = shape.get("points") or []
            if shape["net"] != term["net"]:
                continue
            for a, b in zip(points, points[1:]):
                if a == origin:
                    ends.append(b)
                elif b == origin:
                    ends.append(a)
        axis = directions[term["name"]]
        if not ends or any(
            not all((b == a if sign == 0 else (b - a) * sign > 0)
                    for a, b, sign in zip(origin, end, axis)) for end in ends
        ):
            raise ValueError("Invalid parent wire at " + term["name"])
        leads[term["name"]] = {"anchor": origin, "first_endpoints": ends}
    if leads.keys() != directions.keys():
        raise ValueError("Incomplete parent port readback")
    return leads


def spice_number(token):
    match = re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)([a-zA-Z]*)",
                         str(token).strip("()"))
    if not match:
        raise ValueError("Unexpected SI dimension " + str(token))
    return float(match[1]) * {"": 1, "u": 1e-6, "n": 1e-9, "p": 1e-12, "m": 1e-3}[match[2]]


def audit(expected, views, netlist, pdk):
    # Isolate the comparator before interpreting instance names in its parent.
    body = re.sub(r"\\\r?\n\s*", " ", netlist)
    start = re.search(r"(?m)^subckt " + CORE + r"\s+([^\n]+)\n", body)
    end = re.search(r"(?m)^ends " + CORE + r"\s*$", body)
    if not start or not end or end.start() <= start.end():
        raise ValueError("Cannot find the native comparator definition")
    subckt = body[start.end():end.start()]
    pin_order = start[1].split()
    parent = re.search(r"(?m)^XDUT\s+\(([^)]+)\)\s+" + CORE + r"\s*$", body[end.end():])
    if (len(pin_order) != len(PORTS) or set(pin_order) != set(PORTS) or not parent
            or parent[1].split() != ["0" if n == "VSS" else n for n in pin_order]):
        raise ValueError("SI top-level DUT terminal order or connections differ")
    report = {"mos_terminals": 0, "gate_area_um2": 0.0, "si_pin_order": pin_order}
    model_lines = {}
    for cell, definition in expected.items():
        view = views[(cell, "schematic")]
        ports = {p["name"]: (p["direction"], p["net"]) for p in view["ports"]}
        if ports != {k: (direction, k) for k, direction in definition["ports"].items()}:
            raise ValueError(cell + " saved schematic port mismatch")
        instances = {i["name"]: i for i in view["instances"] if i["lib"] != "basic"}
        if instances.keys() != definition["instances"].keys():
            raise ValueError(cell + " saved instance list mismatch")
        for name, actual in instances.items():
            planned = definition["instances"][name]
            terms = {t["name"]: t["net"] for t in actual["terms"]}
            if (terms != planned["terms"] or actual["cell"] != planned["cell"]
                    or actual["lib"] != planned["lib"]):
                raise ValueError(cell + "/" + name + " saved terminals or master differ")
            props = {key: value for key, _kind, value in actual["props"]}
            sizes = planned["parameters"]
            if actual["lib"] != pdk:
                if any(props.get(key) != val for key, val in sizes.items()):
                    raise ValueError(name + " saved bench parameters differ")
                continue
            line = re.search(r"(?m)^\s*" + re.escape(name) + r"\s+\(([^)]+)\)\s+(\w+) (.+)$", subckt)
            if not line or line[1].split() != [terms[k] for k in "DGSB"] or line[2] != actual["cell"]:
                raise ValueError(name + " SI MOS model or connections differ")
            values = dict(re.findall(r"\b(\w+)=([^\s]+)", line[3]))
            fw, nf, mult = spice_number(sizes["fw"]), int(sizes["fingers"]), int(sizes["m"])
            length = spice_number(sizes["l"])
            statistical = actual["cell"] in ("n18_ckt", "p18_ckt")
            if statistical and ("mismod" not in values or spice_number(values["mismod"]) != 1):
                raise ValueError(name + " native statistical device has mismatch disabled")
            multiplier_key = "mr" if statistical else "m"
            for key, value in {"w": fw * nf, "l": length, "nf": nf, multiplier_key: mult}.items():
                if key not in values or not math.isclose(spice_number(values[key]), value, rel_tol=1e-9):
                    raise ValueError(name + " SI geometry differs for " + key)
            if "fw" in props and not math.isclose(spice_number(props["fw"]), fw, rel_tol=1e-9):
                raise ValueError(name + " saved finger width differs")
            model_lines[name] = line[2] + " " + line[3]
            report["mos_terminals"] += len(terms)
            report["gate_area_um2"] += length * fw * nf * mult * 1e12
        if cell == TB:
            report["parent_leads"] = outward_leads(view["shapes"], instances["XDUT"])
    symbol = {p["name"]: p["direction"] for p in views[(CORE, "symbol")]["ports"]}
    if symbol != PORTS:
        raise ValueError("Saved symbol directions differ")
    report["gate_area_um2"] = round(report["gate_area_um2"], 9)
    return report, model_lines
