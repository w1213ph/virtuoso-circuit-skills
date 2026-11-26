"""Measure complementary MOS curves at proposed interstage biases and budget two-stage OTA gain."""
import argparse
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / name) for name in ("smic18-bias-mirror", "dynamic-comparator", "sky130-current-mirror")]
from measure_psf import scalar_sweep
from spectre_run import read_json, run_spectre, save_json, sha256
import pair_views


STATES = (("tt", 27, 1.80), ("ss", 85, 1.62), ("ff", -40, 1.80))
FIELDS = ("gm", "gds", "vdsat", "cgg", "cgd")


def sweep(folder, state, site, cards, points, fresh):
    corner, temp, supply = state
    lines = ["simulator lang=spectre", "global 0", 'include "{}" section={}'.format(site["model_file"], corner),
             "simulator lang=spectre", "parameters VGMAG=0", "opts options temp={} reltol=1e-6".format(temp),
             "VGN (GN 0) vsource dc=VGMAG", "VGP (0 GP) vsource dc=VGMAG"]
    vectors = {}
    for i, ((length, kind), (vds, vbs)) in enumerate(points.items()):
        tag = kind + str(i)
        lines.extend(["V{0} (D{0} 0) vsource dc={1:.12g}".format(tag, vds),
                      "VB{0} (B{0} 0) vsource dc={1:.12g}".format(tag, vbs),
                      "M{0} (D{0} G{1} 0 B{0}) {2}".format(tag, kind, cards[length, kind])])
        vectors[length, kind] = ["V" + tag + ":p"] + ["M" + tag + ":" + field for field in FIELDS]
    names = [signal for signals in vectors.values() for signal in signals]
    lines += ["saveOptions options save=selected", "save " + " ".join(names),
              "transfer dc param=VGMAG start=0 stop={:g} step={:.12g}".format(supply, supply / 180)]
    deck = "\n".join(lines) + "\n"
    path = folder / "psf/transfer.dc"
    if fresh:
        folder.mkdir()
        (folder / "input.scs").write_text(deck, encoding="utf-8")
        trace, dependencies, warnings = run_spectre(site["spectre"], folder, Path(site["model_file"]), ["transfer.dc"])
        if trace != path or warnings:
            raise ValueError("Review Spectre diagnostics: " + str(folder))
        save_json(folder / "run.json", dict(dependencies=dependencies, raw_sha256=sha256(path),
                                           input_sha256=sha256(folder / "input.scs"), log_sha256=sha256(folder / "spectre.out")))
    record = read_json(folder / "run.json")
    deps = record["dependencies"]
    log = (folder / "spectre.out").read_text(encoding="utf-8", errors="replace")
    if ((folder / "input.scs").read_text(encoding="utf-8") != deck or sha256(path) != record["raw_sha256"]
            or sha256(folder / "input.scs") != record["input_sha256"] or sha256(folder / "spectre.out") != record["log_sha256"]
            or not re.search(r"spectre completes with 0 errors?, 0 warnings?", log, re.I)
            or str(site["model_file"]) not in deps or any(sha256(Path(k)) != v for k, v in deps.items())):
        raise ValueError("Device sweep, solver log or model dependency differs: " + str(folder))
    table = scalar_sweep(path.read_text(encoding="utf-8"), "VGMAG", names)
    axis = table["VGMAG"]
    if len(axis) != 181 or any(abs(v - supply * i / 180) > 1e-7 for i, v in enumerate(axis)):
        raise ValueError("Incomplete device curve: " + str(folder))
    selected = {}
    for key, signals in vectors.items():
        rows = []
        for gate, measured, gm, gds, vdsat, cgg, cgd in zip(axis, *(table[s] for s in signals)):
            current = abs(measured)
            if current > 1e-8 and gm > 0 and gds > 0:
                rows.append(dict(id_per_width_uA_um=current * 0.5e6, gm_id_V=gm / current, gds_id_V=gds / current,
                                 gate_V=gate, gm_gds=gm / gds, vdsat_V=abs(vdsat), cgg_fF=cgg * 1e15,
                                 cgd_signed_fF=cgd * 1e15))
        if not rows:
            raise ValueError("No conducting MOS operating point: " + str(key))
        selected[key] = min(rows, key=lambda r: abs(r["gm_id_V"] - 15))
        selected[key].update(vds_V=points[key][0], vbs_V=points[key][1])
    return selected, len(axis) * len(vectors)


def common_source_db(driver, load):
    # At equal branch currents, both devices contribute to output conductance.
    return 20 * math.log10(driver["gm_id_V"] / (driver["gds_id_V"] + load["gds_id_V"]))


def evaluate(out, site, fresh):
    cards = pair_views.prepare(out / "oa", site, fresh)
    cases = {}
    for state in STATES:
        corner, temp, supply = state
        folder = out / corner
        if fresh:
            folder.mkdir()
        points = {(l, k): (sign * supply / 2, 0.0) for l in pair_views.LENGTHS for k, sign in (("N", 1), ("P", -1))}
        output, output_count = sweep(folder / "output", state, site, cards, points, fresh)
        source = supply / 4
        inputs = {}
        for length in pair_views.LENGTHS:
            interstage = supply - output[length, "P"]["gate_V"]
            if not source < interstage < supply:
                raise ValueError("Output-stage gate bias is outside the proposed first-stage range")
            inputs[length, "N"] = (interstage - source, -source)
            inputs[length, "P"] = (interstage - supply, 0.0)
        first, input_count = sweep(folder / "input", state, site, cards, inputs, fresh)
        budgets = {}
        for length in pair_views.LENGTHS:
            n1, p1 = first[length, "N"], first[length, "P"]
            n2, p2 = output[length, "N"], output[length, "P"]
            gain1, gain2 = common_source_db(n1, p1), common_source_db(p2, n2)
            budgets[str(length)] = dict(first_dB=gain1, second_dB=gain2, product_dB=gain1 + gain2,
                                        input_pair_vbs_V=-source, input_vcm_V=source + n1["gate_V"],
                                        second_stage_gate_V=supply - p2["gate_V"], first_N=n1, first_load_P=p1,
                                        output_load_N=n2, output_P=p2)
        cases[corner] = dict(temperature_C=temp, supply_V=supply, raw_points=input_count + output_count, budgets=budgets)
    return dict(stage="device_gain_budget", target_gm_id_per_V=15, reference_width_um=2,
                pdk_library=site["pdk_library"], pdk_revision=site["pdk_revision"],
                model_sha256=sha256(Path(site["model_file"])), corners=cases)


def main():
    from bias import config_for_site
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="Local SMIC18 site configuration")
    args = parser.parse_args()
    fresh = args.action == "run"
    try:
        site = config_for_site(args.config.resolve())
        out = args.out.expanduser().resolve()
        if fresh:
            out.mkdir(parents=True, exist_ok=False)
        report = evaluate(out, site, fresh)
        if fresh:
            save_json(out / "summary.json", report)
        elif read_json(out / "summary.json") != report:
            raise ValueError("Saved gain budget differs from the native devices and raw sweeps")
        for corner, case in report["corners"].items():
            print(corner, case["raw_points"], "device points | estimated two-stage gain (dB):",
                  ", ".join("{:.2f}".format(b["product_dB"]) for b in case["budgets"].values()))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
