"""Characterize the two real SI devices used as references for this benchmark."""

import csv
import hashlib
import math
import shutil
from pathlib import Path

from measure_psf import scalar_sweep
from schematic import HERE, TOPOLOGY
from spectre_run import read_json, run_spectre, save_json, sha256


FIELDS = ("gm", "gds", "cgg", "cgd", "vdsat")
FILES = ("input.scs", "spectre.out", "psf/gate_sweep.dc", "table.csv")


def probe_deck(config, models):
    vdd = config["vdd_V"]
    devices = {d["instance"]: d for d in TOPOLOGY["devices"]}
    lines = [
        "simulator lang=spectre", "global 0", "parameters VGMAG=0",
        'include "{}" section={}'.format(config["model_file"], config["corner"]),
        "opt options temp={:g} reltol=1e-6".format(config["temp_C"]),
        "VGN (GN 0) vsource type=dc dc=VGMAG", "VGP (0 GP) vsource type=dc dc=VGMAG",
    ]
    trace_names, specs = [], []
    for polarity, name, sign in (("N", "MNINP", 1), ("P", "MPQ_N", -1)):
        device = devices[name]
        for i, fraction in enumerate((5/36, 5/18, 0.5)):
            for j, reverse in enumerate((0, 0.15) if sign == 1 else (0,)):
                tag = "{}{}{}".format(polarity, i, j)
                mos, drain, body = "M_" + tag, "D_" + tag, "B_" + tag
                vds, vbs = sign * fraction * vdd, -sign * reverse
                lines.extend([
                    "VD_{0} ({1} 0) vsource type=dc dc={2:g}".format(tag, drain, vds),
                    "VB_{0} ({1} 0) vsource type=dc dc={2:g}".format(tag, body, vbs),
                    "{} ({} G{} 0 {}) {}".format(mos, drain, polarity, body, models[name]),
                ])
                names = ["VD_" + tag + ":p"] + [mos + ":" + key for key in FIELDS]
                trace_names.extend(names)
                specs.append((polarity, vds, vbs, device, names))
    lines.extend(["saveOptions options save=selected", "save " + " ".join(trace_names),
                  "gate_sweep dc param=VGMAG start=0 stop={:.12g} step={:.12g}".format(vdd, vdd/180)])
    return "\n".join(lines) + "\n", specs


def write_table(folder, trace, specs, config):
    columns = scalar_sweep(trace.read_text(encoding="utf-8"), "VGMAG",
                           [name for *_bias, names in specs for name in names])
    bias = columns["VGMAG"]
    if len(bias) != 181 or not math.isclose(bias[-1], config["vdd_V"], rel_tol=1e-7):
        raise ValueError("Incomplete DC sweep at " + str(trace))
    rows = []
    for polarity, vds, vbs, device, names in specs:
        width = device["total_width_um"]["baseline"]
        for vgs, drain, gm, gds, cgg, cgd, vdsat in zip(bias, *(columns[n] for n in names)):
            current = abs(drain)
            rows.append({
                "polarity": polarity, "length_um": device["length_um"], "total_width_um": width,
                "fingers": device["fingers"], "vds_V": vds, "vbs_V": vbs, "abs_vgs_V": vgs,
                "abs_id_uA": current * 1e6, "id_density_uA_per_um": current * 1e6 / width,
                "gm_uS": gm * 1e6, "gds_uS": gds * 1e6,
                "gm_over_id_per_V": gm/current if current >= 1e-10 else None,
                "gm_over_gds": gm/gds if gds > 0 else None, "cgg_fF": cgg * 1e15,
                "cgd_signed_fF": cgd * 1e15, "abs_vdsat_V": abs(vdsat),
                "corner": config["corner"], "temp_C": config["temp_C"],
            })
    with (folder / "table.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def reusable(source, identity, model):
    if not source:
        return None
    try:
        old = read_json(source / "manifest.json")
        if old["identity"] != identity or set(old["files"]) != set(FILES):
            return None
        if (str(model) not in old["dependencies"] or old["files"]["input.scs"] != identity["deck_sha256"]
                or old.get("sweeps") != 9 or old.get("points") != 1629):
            return None
        if any(sha256(source / n) != digest for n, digest in old["files"].items()):
            return None
        if any(sha256(Path(n)) != digest for n, digest in old["dependencies"].items()):
            return None
        return old
    except (KeyError, TypeError, OSError, ValueError):
        return None


def characterize(folder, config, models, version, source=None):
    folder.mkdir()
    deck, specs = probe_deck(config, models)
    identity = {
        "deck_sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
        "extractor_sha256": sha256(Path(__file__)), "reader_sha256": sha256(HERE / "measure_psf.py"),
        "spectre": str(config["spectre"]), "spectre_version": version,
        "pdk_library": config["pdk_library"], "pdk_revision": config.get("pdk_revision"),
    }
    existing = reusable(source, identity, config["model_file"])
    if existing:
        (folder / "psf").mkdir()
        for name in FILES:
            shutil.copyfile(str(source / name), str(folder / name))
            if sha256(folder / name) != existing["files"][name]:
                raise ValueError("The previous characterization changed during reuse")
        manifest = dict(existing, mode="reused", reused_from=str(source))
    else:
        (folder / "input.scs").write_text(deck, encoding="utf-8")
        trace, deps, warnings = run_spectre(config["spectre"], folder, config["model_file"], ["gate_sweep.dc"])
        count = write_table(folder, trace, specs, config)
        manifest = {
            "mode": "measured", "identity": identity, "points": count, "sweeps": len(specs),
            "dependencies": deps, "warnings": warnings, "files": {n: sha256(folder / n) for n in FILES},
        }
    save_json(folder / "manifest.json", manifest)
    return manifest


def sizing_example(table, vdd):
    with table.open(encoding="utf-8", newline="") as stream:
        points = [row for row in csv.DictReader(stream) if row["polarity"] == "N"
                  and math.isclose(float(row["vds_V"]), vdd * 5/18, rel_tol=1e-7)
                  and float(row["vbs_V"]) == 0 and float(row["abs_id_uA"]) >= 1
                  and row["gm_over_id_per_V"]]
    if not points:
        return {"available": False}
    point = min(points, key=lambda row: abs(float(row["gm_over_id_per_V"]) - 12))
    ratio, density = float(point["gm_over_id_per_V"]), float(point["id_density_uA_per_um"])
    current_uA = 330 / ratio
    return {"available": True, "illustrative_static_gm_uS": 330, "gm_over_id_per_V": ratio,
            "id_density_uA_per_um": density, "estimated_id_uA": current_uA,
            "estimated_total_width_um": current_uA/density, "sample_abs_vgs_V": float(point["abs_vgs_V"])}


def phase_lookup(table, operating_point):
    """Return nearby measured NMOS rows, including their actual biases and reference width."""
    with table.open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["polarity"] == "N" and row["gm_over_id_per_V"]]
    axes = {key: sorted({float(row[key]) for row in rows}) for key in ("abs_vgs_V", "vds_V", "vbs_V")}
    if any(not grid or not grid[0] <= operating_point[key] <= grid[-1] for key, grid in axes.items()):
        return []
    drain = min(axes["vds_V"], key=lambda value: abs(operating_point["vds_V"] - value))
    body = operating_point["vbs_V"]
    brackets = {max(v for v in axes["vbs_V"] if v <= body), min(v for v in axes["vbs_V"] if v >= body)}
    selected = []
    for vbs in sorted(brackets):
        curve = [r for r in rows if float(r["vds_V"]) == drain and float(r["vbs_V"]) == vbs]
        gate = operating_point["abs_vgs_V"]
        if not curve or not min(float(r["abs_vgs_V"]) for r in curve) <= gate <= max(float(r["abs_vgs_V"]) for r in curve):
            return []
        row = min(curve, key=lambda r: abs(float(r["abs_vgs_V"]) - gate))
        selected.append({k: float(row[k]) for k in ("abs_vgs_V", "vds_V", "vbs_V", "total_width_um",
                                                     "gm_over_id_per_V", "id_density_uA_per_um", "gm_uS")})
    return selected
