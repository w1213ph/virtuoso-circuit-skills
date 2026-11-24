"""Build, size and measure a SKY130 current mirror in a fresh Xschem/ngspice project."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from fetch_models import HERE, LOCK, checksum, prepare, verify
from measure import MAX_ERROR_PCT, MAX_SETTLING_NS, VECTORS, deck, metrics
from mos_table import LENGTHS, STATES, characterize, choose, command, save_json, simulate
from native import build

sys.path.insert(0, str(HERE.parents[1] / "skills/paper-to-virtuoso/scripts"))
import check_prelayout


MEASURES = (
    ("ratio", "worst_error_pct", "%", MAX_ERROR_PCT, "Worst absolute error of IOUT/IREF on the 0.40–1.20 V sweep."),
    ("settling", "settling_ns", "ns", MAX_SETTLING_NS, "Time from 99% reference turn-on until output stays within 1% of its own 0.8 V DC current."),
)


def digest(path):
    return checksum(path.read_bytes())


def resolve_tool(value):
    path = shutil.which(value)
    if not path:
        raise ValueError("Executable unavailable: " + value)
    return str(Path(path).resolve())


def plan_variant(stage, cases, lib, native, shared):
    plan = {"schema_version":1, "inputs":["sizing.json", "views/TB_BIAS_MIRROR.spice"],
            "reference_inputs":shared, "tests":[], "results":{}}
    for state in cases:
        corner, temp, vdd = state
        folder = stage / corner
        folder.mkdir()
        (folder / "input.spice").write_text(deck(lib, state, native), encoding="utf-8")
        conditions = {"corner":corner, "temperature_C":temp, "vdd_V":vdd, "reference_uA":10,
                      "reference_load_fF":100, "dc_output_V":[0.4,1.2], "dc_step_mV":10, "transient_output_V":0.8,
                      "reference_pulse_rise_ns":1}
        for name, field, unit, target, definition in MEASURES:
            plan["tests"].append({"id":corner+"."+name, "metric":field, "unit":unit, "testbench":"TB_BIAS_MIRROR",
                                  "analysis":"dc" if name=="ratio" else "tran", "measurement":definition, "conditions":conditions,
                                  "inputs":[corner+"/input.spice"], "target":{"source":"Declared design exercise",
                                  "confirmed":True, "limits":{"<=":target}}})
    save_json(stage / "prelayout.json", plan)
    plan, before = check_prelayout.load_records(stage / "prelayout.json", {})
    if any("error" in row for row in before.values()):
        raise ValueError("Incomplete simulation input snapshot")
    save_json(stage / "before-simulation.json", before)
    return plan, before


def measure_variant(stage, cases, native, lib, ngspice, shared):
    plan, before = plan_variant(stage, cases, lib, native, shared)
    for state in cases:
        corner, _temp, vdd = state
        folder = stage / corner
        result = {}
        try:
            simulate(ngspice, folder, (folder / "input.spice").read_text(encoding="utf-8"))
            result = metrics(folder, vdd)
            detail = {"status":"measured", "metrics":result}
        except (OSError, ValueError) as exc:
            detail = {"status":"run_failed", "reason":str(exc)}
        save_json(folder / "measurement.json", detail)
        evidence = {str((folder / n).relative_to(stage).as_posix()):digest(folder / n) for n in
                    ("measurement.json","dc.dat","transient.dat","ngspice.log") if (folder / n).is_file()}
        for name, field, unit, _target, _definition in MEASURES:
            idx = corner+"."+name
            record = {"status":detail["status"], "unit":unit, "context_sha256":before[idx]["context_sha256"], "evidence":evidence}
            if result:
                record["value"] = result[field]
            else:
                record["reason"] = detail["reason"]
            plan["results"][idx] = record
        save_json(stage / "prelayout.json", plan)
        print(stage.name, corner, detail["status"], flush=True)


def report(out, variants, records, gm):
    lines = ["# SKY130 偏置电流镜前仿", "", "目标为镜像外部 10 µA 参考电流。输出在 0.40–1.20 V 时误差上限为 5%，"
             "参考端接 100 fF，开启后应在 200 ns 内稳定。两项阈值均为本案例的教学规格。", "",
             "| 版本 | 单管 W/L，µm | 两管栅面积合计，µm² | 查表参考 gm/ID，V⁻¹ |",
             "|---|---:|---:|---:|"]
    for name, sizing in variants.items():
        lines.append("| {} | {:g} / {:g} | {:g} | {:.2f} |".format(
            name, sizing["width_um"], sizing["length_um"], sizing["mirror_pair_gate_area_um2"], sizing["selected_row"]["gm_id_per_V"]))
    lines.extend(["", "| 工况 | 版本 | 最坏镜像误差 % | 首个持续满足 5% 的输出电压 mV | 稳定时间 ns | 输出电阻 MΩ |",
                  "|---|---|---:|---:|---:|---:|"])
    for corner, temp, vdd in STATES:
        for name in variants:
            row = json.loads((out / name / corner / "measurement.json").read_text(encoding="utf-8"))
            if row["status"] == "measured":
                v = row["metrics"]
                limit = v["compliance_mV"] if v["compliance_mV"] is not None else "截至 1.20 V 未满足"
                lines.append("| {}，{} °C，{:g} V | {} | {:.3f} | {} | {:.2f} | {:.3f} |".format(
                    corner, temp, vdd, name, v["worst_error_pct"], limit, v["settling_ns"], v["output_resistance_Mohm"]))
            else:
                lines.append("| {} | {} | {} | — | — | — |".format(corner, name, row["status"]))
    lines.extend(["", "输出电压从零扫描到 1.50 V。表中最低电压要求从该点到 1.20 V 的所有采样均满足 5% 误差。"
                  "输出电阻取 0.8 V 两侧的电压电流斜率。稳定时间以输出最终 DC 测值为基准。", "",
                  "每个版本的 `checked.json` 保存逐项判定和失败原因。", ""])
    for name, checks in records.items():
        failed=[t["id"] for t in checks["tests"] if t["status"]!="PASS"]
        lines.append("- {} 已通过 {} / {} 项。{}".format(name,len(checks["tests"])-len(failed),len(checks["tests"]),
                    "待处理 " + "、".join(failed) + "。" if failed else ""))
    lines.extend(["", "三组工况共记录 {:,} 个器件工作点。每组表记录实际偏置、几何、gm/ID、电流密度、gm/gds、"
                  "本征电容和 VDSAT。实际宽度由 TT 数据取整，每个工况随后按相同尺寸实测。".format(sum(v["points"] for v in gm)),
                  "", "原生核心、symbol、测试台和 Xschem 网表都在对应版本的 `views/`。在本结果目录下运行下列命令。", "",
                  "```sh", "sh candidate/open.sh bench", "sh candidate/open.sh core", "```",
                  "", "外部参考电流和输出电压由测试台提供。栅面积只计两只 MOS。当前覆盖为三组确定性前仿，"
                  "输入参考源本身、随机失配、完整 PVT 和版图占地须在对应工程验证。", ""])
    (out / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def run(out, model_dir, ngspice, xschem, previous):
    if model_dir is None:
        model_dir = out / "models"
        lib = prepare(model_dir)
    else:
        lib = verify(model_dir)
    if '"' in str(lib) or "\n" in str(lib):
        raise ValueError("Model path not representable in a SPICE include")
    common, tables = out / "common", out / "characterization"
    common.mkdir()
    tables.mkdir()
    command([ngspice,"-n","--version"], common, "ngspice-version.txt")
    identity = {"model_lock_sha256":digest(HERE / "models.lock.json"), "library_sha256":digest(lib),
                "ngspice_version_sha256":digest(common / "ngspice-version.txt"), "ngspice_binary_sha256":digest(Path(ngspice))}
    save_json(common / "recipe.json", dict(models=LOCK["revision"], ngspice=ngspice, xschem=xschem,
              measurement_sha256=digest(HERE/"measure.py"), table_reader_sha256=digest(HERE/"mos_table.py"),
              fixture_vectors=VECTORS))
    gm, variants = [], {}
    for state in STATES:
        rows, result = characterize(tables, lib, ngspice, state, identity, previous)
        gm.append(result)
        if state[0] == "tt":
            variants = {name:choose(rows, length) for name, length in zip(("baseline","candidate"),LENGTHS)}
        print("Device table", state[0], result["mode"], result["points"], flush=True)
    shared = ["../common/bench.txt","../common/recipe.json","../common/ngspice-version.txt",str(lib)]
    shared += [str(model_dir / name) for name in sorted(LOCK["files"])]
    for name, sizing in variants.items():
        stage = out / name
        stage.mkdir()
        save_json(stage / "sizing.json", sizing)
        native, checked, bench = build(stage / "views", sizing, xschem)
        save_json(stage / "native-readback.json", checked)
        payload = ("\n".join(bench)+"\n").encode()
        if name=="baseline":
            (common / "bench.txt").write_bytes(payload)
        elif (common / "bench.txt").read_bytes()!=payload:
            raise ValueError("Native baseline and candidate testbenches differ")
        measure_variant(stage, STATES, native, lib, ngspice, shared)
    records = {}
    for name in variants:
        stage = out / name
        hashes = {}
        plan, contexts = check_prelayout.load_records(stage/"prelayout.json", hashes)
        records[name] = check_prelayout.check(plan, stage, contexts, hashes)
        save_json(stage / "checked.json", records[name])
    comparison = check_prelayout.compare(records["baseline"], records["candidate"])
    save_json(out / "comparison.json", comparison)
    status = {"status":"completed" if comparison.get("candidate_limits_met") else "review_required",
              "comparison":comparison["status"], "candidate_limits_met":comparison.get("candidate_limits_met", False),
              "characterization":gm}
    report(out, variants, records, gm)
    return status


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, required=True, help="A new output directory")
    cli.add_argument("--models", type=Path, help="Existing verified models. Omit to download the pinned public inputs.")
    cli.add_argument("--reuse-lut", type=Path, help="Previous characterization directory for the same models")
    cli.add_argument("--ngspice", default="ngspice")
    cli.add_argument("--xschem", default="xschem")
    args = cli.parse_args()
    try:
        ngspice, xschem = resolve_tool(args.ngspice), resolve_tool(args.xschem)
        out = args.out.expanduser().resolve()
        out.mkdir(mode=0o700, parents=True)
    except (OSError, ValueError) as exc:
        cli.error(str(exc))
    save_json(out / "status.json", {"status":"running"})
    try:
        result = run(out, args.models.expanduser().resolve() if args.models else None, ngspice, xschem,
                     args.reuse_lut.expanduser().resolve() if args.reuse_lut else None)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        result = {"status":"failed", "reason":str(exc)}
    save_json(out / "status.json", result)
    print(result["status"], out, result.get("reason",""), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__=="__main__":
    raise SystemExit(main())
