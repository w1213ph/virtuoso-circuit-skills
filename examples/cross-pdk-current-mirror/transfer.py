"""Size and simulate the same ADC bias interface in SKY130 and GF180MCU, or replay its evidence."""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "sky130-current-mirror"
sys.path.insert(0, str(SHARED))

import fetch_models
import mos_table
import run as mirror
import transfer_bench as bench
import transfer_models
from mos_model import GF180, SKY130


PROCESSES = (("sky130", SKY130, mos_table.STATES, fetch_models.LOCK),
             ("gf180", GF180, (("tt", 27, 3.3), ("ss", 85, 2.97), ("ff", -40, 3.3)), transfer_models.LOCK))
LENGTHS = (("short", 0.50), ("long", 1.00))
WIDTH_RANGE = (0.50, 20.0)
TARGET = 15.0
DEFAULT_REQ = SHARED / "adc-bias.example.json"
# Hash of the SKY130 wrapper generated from this revision's checksummed upstream options.
SKY130_LIBRARY_SHA256 = "c30ecfba52f7afa906e5542fb457449877281d757d1ec2b871f345c4b02cd15a"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_files(out):
    return {p.relative_to(out).as_posix(): mirror.digest(p) for p in sorted(out.rglob("*"))
            if p.is_file() and p.relative_to(out).parts[0] not in ("models", "evidence.json")}


def run_or_check_design(out, process, model, states, name, sizing, req, tools):
    folder = out / process / name
    if tools:
        folder.mkdir()
        mos_table.save_json(folder / "sizing.json", sizing)
    elif read_json(folder / "sizing.json") != sizing:
        raise ValueError("Sizing record differs from the specified source lookup: " + process + "/" + name)
    return bench.measure_design(folder, process, model, states, sizing, req, tools)


def evaluate(out, req, tools=None):
    report = dict(processes={}, copied_control=None)
    sources = {}
    for process, model, states, lock in PROCESSES:
        root = out / process
        if tools:
            root.mkdir()
            (root / "lut").mkdir()
            shutil.copyfile(str(out / "models" / process / fetch_models.LIB), str(root / "model-library.spice"))
        library = (root / "model-library.spice").read_bytes()
        if process == "sky130" and fetch_models.checksum(library) != SKY130_LIBRARY_SHA256:
            raise ValueError("SKY130 wrapper differs from the pinned upstream geometry scaling and corners")
        if process == "gf180" and library != transfer_models.wrapper():
            raise ValueError("GF180 wrapper differs from the pinned deterministic corner recipe")
        lock_path = SHARED / "models.lock.json" if process == "sky130" else HERE / "gf180-models.lock.json"
        identity = dict(process=process, model_revision=lock["revision"], model_lock_sha256=mirror.digest(lock_path),
                        library_sha256=fetch_models.checksum(library), ngspice_version_sha256=mirror.digest(out / "ngspice-version.txt"))
        designs, points = {}, {}
        lengths = tuple(length for _, length in LENGTHS)
        for state in states:
            corner = state[0]
            if tools:
                rows, _record = mos_table.characterize(root / "lut", bench.library_path(process), tools["ngspice"],
                                                       state, identity, lengths=lengths, model=model)
            else:
                rows = bench.replay_table(root / "lut" / corner, process, model, state, lengths, identity)
            points[corner] = len(rows)
            if corner == "tt":
                for name, length in LENGTHS:
                    sizing = mos_table.choose(rows, length, req["reference_uA"], TARGET, WIDTH_RANGE)
                    sizing.update(source_process=process, source_corner="tt")
                    designs[name] = sizing
        results = {name:run_or_check_design(out, process, model, states, name, sizing, req, tools)
                   for name,sizing in sorted(designs.items())}
        selected = min((n for n in results if results[n]["passed"]),
                       key=lambda n:designs[n]["mirror_pair_gate_area_um2"], default=None)
        report["processes"][process] = dict(revision=lock["revision"], states=states, points=points,
                                            model=model.nmos, designs=results, selected=selected)
        if selected:
            sources[process] = designs[selected]
        if process == "gf180" and "sky130" in sources:
            copy = dict(sources["sky130"], copied_from="sky130/" + report["processes"]["sky130"]["selected"])
            report["copied_control"] = run_or_check_design(out, process, model, states, "copy-sky130", copy, req, tools)
    report.update(requirements=req, target_gm_id_per_V=TARGET, width_range_um=WIDTH_RANGE,
                  qualified_in_both=all(p["selected"] is not None for p in report["processes"].values()))
    return report


def markdown(report):
    req = report["requirements"]
    lines = ["# 同一 ADC 偏置接口的跨工艺尺寸复核", "",
             "外部参考 {reference_uA:g} µA，参考节点 {reference_load_fF:g} fF。输出 0.40–1.20 V 时，"
             "镜像误差上限 {max_error_pct:g}%。输出固定 0.80 V，参考电流到达 99% 后，"
             "须在 {max_settling_ns:g} ns 内进入并保持在输出稳态值的 ±1%。".format(**req), "",
             "| 工艺 / 尺寸来源 | 单管 W/L (µm) | 两管 ΣWL (µm²) | 查表 ID/W (µA/µm) | TT 参考支路 gm/ID (V⁻¹) | 三工况最大误差 (%) | 最慢建立 (ns) | 结果 |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    def line(label, case):
        size = case["sizing"]
        row = size["selected_row"]
        return ("| {} | {:.2f} / {:.2f} | {:.2f} | {:.3f} | {:.2f} | {:.3f} | {:.2f} | {} |".format(
            label, size["width_um"], size["length_um"], size["mirror_pair_gate_area_um2"], row["id_per_width_uA_um"],
            case["corners"]["tt"]["operating_point"]["reference"]["gm_id_per_V"], case["worst_error_pct"],
            case["worst_settling_ns"], "达标" if case["passed"] else "超限"))
    for proc, data in report["processes"].items():
        for name, _length in LENGTHS:
            lines.append(line(proc + " / " + name, data["designs"][name]))
    if report["copied_control"]:
        lines.append(line("gf180 / 照搬 SKY130 尺寸与查表行", report["copied_control"]))
    lines += ["", "查表行取自各自来源的 TT、VDS = 0.65 V、VBS = 0 V、2 µm 参考管。"
              "原理图和 ngspice 工作点分别回读每只管的 W、L；ΣWL 合计两管的栅面积。", ""]
    for proc, data in report["processes"].items():
        chosen = data["selected"]
        if chosen:
            base = proc + "/" + chosen
            lines += ["**{}** 在两档本工艺候选中选中栅面积较小且全部达标的 {}。".format(proc, chosen) +
                      " [核心]({0}/views/BIAS_MIRROR.sch) · [父级测试台]({0}/views/TB_BIAS_MIRROR.sch) · "
                      "[查表工作点与尺寸]({0}/sizing.json)".format(base), ""]
        else:
            lines += ["**{} 的两个本工艺候选均未满足全部要求。**".format(proc), ""]
    lines.extend(["[全部工况、两支路工作点与父级端口回读](summary.json) · [原始文件的 SHA-256](evidence.json)", ""])
    return "\n".join(lines)


def run(args):
    req, _defaults = mirror.load_requirements(args.requirements)
    if req.get("assumed_fields") or req["selection"] != "min_gate_area":
        raise ValueError("Supply confirmed electrical requirements and selection=min_gate_area for this comparison")
    tools = {k:mirror.resolve_tool(k) for k in ("ngspice", "xschem")}
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    mos_table.save_json(out / "requirements.json", req)
    mos_table.command([tools["ngspice"], "-n", "--version"], out, "ngspice-version.txt")
    mos_table.command([tools["xschem"], "--version"], out, "xschem-version.txt")
    fetch_models.prepare(out / "models/sky130", args.sky130_models)
    transfer_models.prepare(out / "models/gf180", args.gf180_models)
    report = evaluate(out, req, tools)
    mos_table.save_json(out / "summary.json", report)
    (out / "RESULTS.md").write_text(markdown(report), encoding="utf-8")
    mos_table.save_json(out / "evidence.json", evidence_files(out))
    return report


def verify(out):
    if read_json(out / "evidence.json") != evidence_files(out):
        raise ValueError("Evidence files differ from their recorded SHA-256 inventory")
    req, _defaults = mirror.load_requirements(out / "requirements.json")
    if req.get("assumed_fields") or req["selection"] != "min_gate_area":
        raise ValueError("Comparison requires confirmed electrical limits and min_gate_area")
    report = evaluate(out, req)
    if read_json(out / "summary.json") != json.loads(json.dumps(report)) or (
            out / "RESULTS.md").read_text(encoding="utf-8") != markdown(report):
        raise ValueError("Published result differs from the independent raw-data replay")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action")
    run_cli = commands.add_parser("run", help="Build native views, characterize both PDKs and simulate")
    run_cli.add_argument("--requirements", type=Path, default=DEFAULT_REQ)
    run_cli.add_argument("--sky130-models", type=Path, help="Offline upstream SKY130 inputs matching models.lock.json")
    run_cli.add_argument("--gf180-models", type=Path, help="Offline upstream GF180 tree matching gf180-models.lock.json")
    run_cli.add_argument("--out", type=Path, required=True, help="New output directory")
    commands.add_parser("verify", help="Recompute from the artifact using only Python").add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.action:
        parser.error("Choose run or verify")
    try:
        args.out = args.out.expanduser().resolve()
        summary = run(args) if args.action == "run" else verify(args.out)
        for proc, data in summary["processes"].items():
            print(proc, "selected:", data["selected"], "| measured MOS points:", sum(data["points"].values()))
        sys.exit(0 if summary["qualified_in_both"] else 1)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        parser.exit(2, str(exc) + "\n")
