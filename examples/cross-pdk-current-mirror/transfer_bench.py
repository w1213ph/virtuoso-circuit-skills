"""Reconstruct device tables and measure both mirror branches in the native testbench."""
import csv
import json
import math
from pathlib import Path

import measure
import mos_table
import native
from fetch_models import checksum


def library_path(process):
    # Both characterization and circuit cases sit three directories below the run root.
    return "../../../models/" + process + "/mirror.lib.spice"


def op_vectors(model):
    return ["@m.xbias.{}.{}[{}]".format(device, model.inner_n, field)
            for device in ("xref", "xmir") for field in ("gm", "gds", "w", "l")]


def circuit_deck(process, model, state, netlist, req):
    vectors = " ".join(op_vectors(model))
    op = "save " + vectors + "\nop\nwrdata op.dat v(iref) v(iout) i(vbias) i(vforce) " + vectors + "\n"
    return measure.deck(library_path(process), state, netlist, req["reference_uA"], req["max_settling_ns"],
                        model.label, op)


def operating_point(path, model, sizing, req, dc):
    lines = path.read_text(encoding="utf-8").lower().splitlines()
    names = ["v(iref)", "v(iout)", "i(vbias)", "i(vforce)"] + op_vectors(model)
    if len(lines) != 2 or lines[0].split()[1:] != names:
        raise ValueError("Expected one operating point of both native MOS devices in " + str(path))
    numbers = [float(x) for x in lines[1].split()]
    if len(numbers) != 1 + len(names) or not all(math.isfinite(x) for x in numbers):
        raise ValueError("Incomplete operating point in " + str(path))
    gate, output, ibias, iforce = numbers[1:5]
    # Standalone OP and the swept DC solution reach the same bias from different initial guesses.
    if not (math.isclose(gate, dc["reference_node_V"], abs_tol=10e-6)
            and math.isclose(output, 0.8, abs_tol=1e-7)
            and math.isclose(-ibias*1e6, req["reference_uA"], rel_tol=1e-5)
            and math.isclose(-iforce*1e6, dc["output_current_uA_at_0p8V"], rel_tol=1e-5)):
        raise ValueError("Operating point differs from the measured DC fixture")
    branches = {}
    for name, current, vds, offset in (("reference", -ibias, gate, 5), ("output", -iforce, output, 9)):
        gm, gds, width_m, length_m = numbers[offset:offset+4]
        width, length = width_m*1e6, length_m*1e6
        if min(current, gm, gds) <= 0 or not (math.isclose(width, sizing["width_um"], rel_tol=1e-7)
                                                and math.isclose(length, sizing["length_um"], rel_tol=1e-7)):
            raise ValueError("Simulator MOS dimensions in metres or currents differ: " + name)
        branches[name] = dict(width_um=width, length_um=length, vgs_V=gate, vds_V=vds, vbs_V=0.0,
                              current_uA=current*1e6, gm_id_per_V=gm/current, gm_gds=gm/gds,
                              id_per_width_uA_um=current*1e6/width)
    return branches


def replay_table(folder, process, model, state, lengths, identity):
    corner, temp, supply = state
    deck, devices = mos_table.scans(library_path(process), corner, temp, supply, lengths, model)
    if (folder / "input.spice").read_text(encoding="utf-8") != deck:
        raise ValueError("Characterization deck differs from the process and bias grid: " + process + "/" + corner)
    record = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = ["input.spice", "ngspice.log", "table.csv"] + [d[0] + ".dat" for d in devices]
    recipe = dict(identity, lengths_um=list(lengths), input_sha256=checksum(deck.encode("utf-8")),
                  reader_sha256=checksum(Path(mos_table.__file__).read_bytes()))
    if (record["recipe"] != recipe or record["mode"] != "measured"
            or record["corner"] != corner or record["temperature_C"] != temp or record["vdd_V"] != supply
            or mos_table.cache_record(folder, recipe, files) is None):
        raise ValueError("Characterization provenance differs: " + process + "/" + corner)
    rows = mos_table.read_curves(folder, devices, supply)
    with (folder / "table.csv").open(encoding="utf-8", newline="") as f:
        table = csv.DictReader(f)
        if tuple(table.fieldnames or ()) != mos_table.FIELDS:
            raise ValueError("Unexpected lookup columns")
        for row in rows:
            saved = next(table, None)
            if saved is None or any(saved[k] != ("" if row[k] is None else str(row[k])) for k in mos_table.FIELDS):
                raise ValueError("Lookup row differs from the raw simulator curve")
        if next(table, None) is not None:
            raise ValueError("Unexpected extra lookup row")
    if (record["points"] != len(rows) or record["curves"] != len(devices)
            or record["table_sha256"] != record["files"]["table.csv"]):
        raise ValueError("Incomplete lookup table")
    return rows


def measure_design(folder, process, model, states, sizing, req, tools=None):
    views = folder / "views"
    fixture = (req["reference_load_fF"], req["max_settling_ns"])
    if tools:
        native.build(views, sizing, tools["xschem"], *fixture, model=model)
    for name, source in native.view_sources(sizing["width_um"], sizing["length_um"], *fixture, model=model):
        if (views / name).read_text(encoding="utf-8") != source:
            raise ValueError("Editable schematic or symbol differs from the sized design: " + name)
    netlist = (views / "TB_BIAS_MIRROR.spice").read_text(encoding="utf-8")
    readback, _top = native.audit(netlist, sizing["width_um"], sizing["length_um"], *fixture, model=model)
    readback["parent_leads"] = native.leads(views)
    cases = {}
    for state in states:
        corner, _temp, supply = state
        case = folder / corner
        deck = circuit_deck(process, model, state, netlist, req)
        if tools:
            case.mkdir()
            mos_table.simulate(tools["ngspice"], case, deck)
        elif (case / "input.spice").read_text(encoding="utf-8") != deck:
            raise ValueError("Circuit deck differs from the native netlist or PDK: " + str(case))
        dc = measure.metrics(case, supply, req["reference_uA"], req["max_error_pct"], req["max_settling_ns"])
        branches = operating_point(case / "op.dat", model, sizing, req, dc)
        passed = dc["worst_error_pct"] <= req["max_error_pct"] and dc["settling_ns"] <= req["max_settling_ns"]
        cases[corner] = dict(metrics=dc, operating_point=branches, passed=passed)
    return dict(sizing=sizing, native_readback=readback, corners=cases,
                passed=all(v["passed"] for v in cases.values()),
                worst_error_pct=max(v["metrics"]["worst_error_pct"] for v in cases.values()),
                worst_settling_ns=max(v["metrics"]["settling_ns"] for v in cases.values()))
