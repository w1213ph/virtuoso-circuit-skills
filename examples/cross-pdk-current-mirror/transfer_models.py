"""Pinned GF180MCU ngspice library for deterministic TT, SS and FF measurements."""
import json
from pathlib import Path

from fetch_models import LIB, checksum, fetch


LOCK = json.loads(Path(__file__).with_name("gf180-models.lock.json").read_text(encoding="utf-8"))


def wrapper():
    lines = ["* GF180MCU 3.3 V MOS, deterministic corners: " + LOCK["revision"]]
    for corner, section in (("tt", "typical"), ("ss", "ss"), ("ff", "ff")):
        lines += [".lib " + corner, '.include "models/ngspice/design.ngspice"',
                  ".param sw_stat_global=0 sw_stat_mismatch=0",
                  '.lib "models/ngspice/sm141064.ngspice" ' + section, ".endl " + corner]
    return ("\n".join(lines) + "\n").encode("utf-8")


def prepare(out, source=None):
    out.mkdir(parents=True)
    for name, expected in LOCK["files"].items():
        data = (source / name).read_bytes() if source else fetch(name, LOCK)
        if checksum(data) != expected:
            raise ValueError("GF180 model input differs from its lock: " + name)
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (out / LIB).write_bytes(wrapper())
    return out / LIB
