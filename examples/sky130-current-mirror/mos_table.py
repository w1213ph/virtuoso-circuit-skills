"""Measure the pinned SKY130 MOS devices and size the mirror from a DC table."""
import csv
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from fetch_models import checksum


FIELDS = ("polarity", "length_um", "width_um", "vds_V", "vbs_V", "abs_vgs_V", "abs_id_uA",
          "id_per_width_uA_um", "gm_id_per_V", "gm_uS", "gds_uS", "gm_gds", "vdsat_V", "cgg_fF", "cgd_signed_fF")
LENGTHS = (0.5, 1.0)
REFERENCE_WIDTH = 2.0
STATES = (("tt", 27, 1.8), ("ss", 85, 1.62), ("ff", -40, 1.8))


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def command(args, cwd, filename, env=None):
    with (cwd / filename).open("wb") as out:
        try:
            result = subprocess.run([str(a) for a in args], cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Tool timed out. See " + str(cwd / filename)) from exc
    if result.returncode:
        raise ValueError("Tool exited {}. See {}".format(result.returncode, cwd / filename))


def simulate(tool, folder, deck):
    (folder / "input.spice").write_text(deck, encoding="utf-8")
    command([tool, "-n", "-b", "input.spice"], folder, "ngspice.log")
    log = (folder / "ngspice.log").read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?im)^\s*(?:fatal|error|warning)\b|simulation interrupted|timestep too small|no such vector", log):
        raise ValueError("Review solver diagnostics in " + str(folder / "ngspice.log"))
    if "analysis_complete" not in log:
        raise ValueError("Missing end of analysis in " + str(folder / "ngspice.log"))


def table(path, names):
    with path.open(encoding="utf-8") as f:
        header = f.readline().lower().split()
        if header[1:] != list(names):
            raise ValueError("Unexpected vector names in {}: {}".format(path, header))
        results = []
        for line in f:
            if not line.strip():
                continue
            values = [float(s) for s in line.split()]
            if len(values) != len(header) or not all(math.isfinite(v) for v in values):
                raise ValueError("Invalid solver data in " + str(path))
            results.append(values)
        if len(results) < 2 or any(a[0] >= b[0] for a, b in zip(results, results[1:])):
            raise ValueError("Incomplete sweep in " + str(path))
    return results


def scans(lib, corner, temp, supply, lengths=LENGTHS):
    lines = ["SKY130 MOS characterization", '.lib "{}" {}'.format(lib, corner), ".temp {}".format(temp),
             "VG gate 0 0", "EINV pgate 0 gate 0 -1"]
    devices, controls = [], []
    for pol, sign, gate in (("n", 1, "gate"), ("p", -1, "pgate")):
        master = "sky130_fd_pr__{}fet_01v8".format(pol)
        for i, length in enumerate(lengths):
            for j, drain in enumerate((0.35, 0.65, 1.2)):
                for k, body in enumerate((0, 0.15)):
                    tag = "{}{}{}{}".format(pol, i, j, k)
                    vds, vbs = sign * drain, -sign * body
                    lines.extend(["VD{0} d{0} 0 {1:g}".format(tag, vds), "VB{0} b{0} 0 {1:g}".format(tag, vbs),
                                  "X{0} d{0} {1} 0 b{0} {2} w={3:g} l={4:g} nf=1".format(
                                      tag, gate, master, REFERENCE_WIDTH, length)])
                    internal = "@m.x{}.m{}".format(tag, master)
                    fields = ["i(vd{})".format(tag)] + [internal + "[" + key + "]" for key in
                                                          ("gm", "gds", "vdsat", "cgg", "cgd")]
                    devices.append((tag, pol, length, vds, vbs, fields))
                    controls.append("save " + " ".join(fields))
    lines += [".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=12", "save v(gate)"] + controls
    lines.append("dc VG 0 {:.6g} 0.01".format(supply))
    for tag, _pol, _l, _d, _b, fields in devices:
        lines.append("wrdata {}.dat v(gate) {}".format(tag, " ".join(fields)))
    lines += ["echo analysis_complete", "quit", ".endc", ".end"]
    return "\n".join(lines) + "\n", devices


def cache_record(source, recipe, names):
    if source is None:
        return None
    try:
        record = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if (record["recipe"] != recipe or set(record["files"]) != set(names)
                or record["files"]["input.spice"] != recipe["input_sha256"]):
            return None
        for name in names:
            if checksum((source / name).read_bytes()) != record["files"][name]:
                return None
        return record
    except (OSError, KeyError, TypeError, ValueError):
        return None


def characterize(root, lib, tool, state, identity, previous=None, lengths=LENGTHS):
    corner, temp, supply = state
    folder = root / corner
    folder.mkdir()
    deck, devices = scans(lib, corner, temp, supply, lengths)
    recipe = dict(identity, lengths_um=list(lengths), input_sha256=checksum(deck.encode("utf-8")),
                  reader_sha256=checksum(Path(__file__).read_bytes()))
    files = ["input.spice", "ngspice.log", "table.csv"] + [tag+".dat" for tag, *_rest in devices]
    source = previous / corner if previous else None
    cached = cache_record(source, recipe, files)
    if cached:
        for name in files:
            if name != "table.csv":
                shutil.copyfile(str(source / name), str(folder / name))
    else:
        simulate(tool, folder, deck)
    rows = []
    for tag, pol, length, vds, vbs, vectors in devices:
        values = table(folder / (tag + ".dat"), ["v(gate)"] + vectors)
        if len(values) != round(supply / 0.01) + 1 or not math.isclose(values[-1][0], supply):
            raise ValueError("Device sweep ended early")
        for mag, gate, current, gm, gds, vdsat, cgg, cgd in values:
            if not math.isclose(gate, mag, abs_tol=1e-10):
                raise ValueError("Measured gate bias differs from requested sweep")
            current = abs(current)
            ratio = abs(gm) / current if current >= 1e-10 else None
            numbers = (pol.upper(), length, REFERENCE_WIDTH, vds, vbs, gate, current*1e6,
                       current*1e6/REFERENCE_WIDTH, ratio, abs(gm)*1e6, gds*1e6,
                       abs(gm)/gds if gds > 0 else None, abs(vdsat), cgg*1e15, cgd*1e15)
            rows.append(dict(zip(FIELDS, numbers)))
    path = folder / "table.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    hashes = {n:checksum((folder / n).read_bytes()) for n in files}
    if cached and hashes != cached["files"]:
        raise ValueError("Previous characterization changed during reuse")
    record = dict(corner=corner, temperature_C=temp, vdd_V=supply, curves=len(devices), points=len(rows),
                  recipe=recipe, files=hashes, mode="reused" if cached else "measured", table_sha256=hashes["table.csv"])
    save_json(folder / "manifest.json", record)
    return rows, record


def choose(rows, length, current_uA=10, target_ratio=15):
    candidates = [r for r in rows if r["polarity"] == "N" and r["length_um"] == length and
                  r["vds_V"] == 0.65 and r["vbs_V"] == 0 and r["gm_id_per_V"] and r["abs_id_uA"] >= 0.1]
    if not candidates:
        raise ValueError("No applicable NMOS sizing rows")
    sample = min(candidates, key=lambda r: abs(r["gm_id_per_V"] - target_ratio))
    width = current_uA / sample["id_per_width_uA_um"]
    width_rounded = round(round(width / 0.05) * 0.05, 2)
    if not 0.42 <= width_rounded <= 12:
        raise ValueError("Sizing estimate outside this example's allowable width range")
    return dict(length_um=length, width_um=width_rounded, continuous_width_um=width, fingers=1,
                mirror_pair_gate_area_um2=round(2 * length * width_rounded, 6),
                reference_current_uA=current_uA, target_gm_id_per_V=target_ratio, selected_row=sample)
