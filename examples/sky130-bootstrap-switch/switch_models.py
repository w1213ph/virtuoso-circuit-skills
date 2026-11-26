"""Prepare the pinned SKY130 thick-oxide MOS and M3 MIM models alongside the 1.8 V cards."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sky130-current-mirror"))
import fetch_models

LOCK = json.loads((HERE / "models.lock.json").read_text(encoding="utf-8"))
HV = ("nfet_g5v0d10v5", "pfet_g5v0d10v5")
MIM = "sky130_fd_pr__cap_mim_m3_1"
MIM_FILE = "cells/cap_mim_m3/" + MIM + ".model.spice"
PARAMETERS = {
    "models/r+c/res_typical__cap_typical__lin.spice": ("camimc", "cpmimc"),
    "models/r+c/res_typical__cap_typical.spice": ("tol_m3", "rm3", "rcvia3"),
    "models/sky130_fd_pr__model__r+c.model.spice": ("m3_dw", "tc1rm3", "tc2rm3", "tc1rvia3", "tc2rvia3"),
}


def library(root):
    # Import the exact numeric coefficients used by the MIM card. The remaining
    # resistor subcircuits in these files do not belong to this device subset.
    params = []
    for path, names in PARAMETERS.items():
        text = (root / path).read_text(encoding="utf-8")
        for name in names:
            values = re.findall(r"(?im)^\s*(?:\.param|\+)\s+" + name + r"\s*=\s*([-+.\deE]+[munpf]?)\s*(?:\$[^\n]*)?$", text)
            if len(values) != 1:
                raise ValueError("Missing or ambiguous MIM coefficient " + name)
            params.append(".param " + name + "=" + values[0])
    lines = ["* Public SKY130 sampling switch, " + LOCK["revision"]]
    for corner in fetch_models.CORNERS:
        lines.extend([".lib " + corner, *params, '.include "' + MIM_FILE + '"'])
        for device in HV:
            prefix = 'cells/{}/sky130_fd_pr__{}__'.format(device, device)
            lines.extend('.include "' + prefix + section + '.corner.spice"' for section in (corner, "mismatch"))
        lines.append(".endl " + corner)
    return ("\n".join(lines) + "\n").encode("ascii")


def check_extra(root):
    for name, expected in LOCK["files"].items():
        if fetch_models.checksum((root / name).read_bytes()) != expected:
            raise ValueError("Pinned switch model changed: " + name)
    file = root / "switch.lib.spice"
    if file.read_bytes() != library(root):
        raise ValueError("Switch library differs from its pinned recipe")
    return file


def prepare_extra(dest, source=None):
    if dest.exists():
        return check_extra(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dest.parent)) as temp:
        stage = Path(temp) / "device-models"
        stage.mkdir()
        def copy(name):
            raw = (source / name).read_bytes() if source else fetch_models.fetch(name, LOCK)
            if fetch_models.checksum(raw) != LOCK["files"][name]:
                raise ValueError("Unverified source model " + name)
            file = stage / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(raw)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(copy, LOCK["files"]))
        (stage / "switch.lib.spice").write_bytes(library(stage))
        check_extra(stage)
        try:
            os.rename(str(stage), str(dest))
        except OSError:
            if not dest.is_dir():
                raise
    return check_extra(dest)
