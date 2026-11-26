"""Replay a measured MOS characterization from its original simulator files."""
import csv
import json
from pathlib import Path

import mos_table
from fetch_models import checksum


def replay(folder, library, model, state, lengths, identity, *, modes=("measured",), reader=None):
    corner, temp, supply = state
    deck, devices = mos_table.scans(library, corner, temp, supply, lengths, model)
    if (folder / "input.spice").read_text(encoding="utf-8") != deck:
        raise ValueError("Characterization deck differs from the process and bias grid: " + str(folder))
    record = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = ["input.spice", "ngspice.log", "table.csv"] + [d[0] + ".dat" for d in devices]
    recipe = dict(identity, lengths_um=list(lengths), input_sha256=checksum(deck.encode("utf-8")),
                  reader_sha256=reader or checksum(Path(mos_table.__file__).read_bytes()))
    if (record["recipe"] != recipe or record["mode"] not in modes or record["corner"] != corner
            or record["temperature_C"] != temp or record["vdd_V"] != supply
            or mos_table.cache_record(folder, recipe, files) is None):
        raise ValueError("Characterization provenance differs: " + str(folder))
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
