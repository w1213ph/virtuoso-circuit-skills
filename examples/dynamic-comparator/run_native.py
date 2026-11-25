"""Build both SMIC18 comparator views and measure them in a new Cadence workspace."""

import argparse
import math
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from audit_native import audit
from characterize import characterize, phase_lookup, sizing_example
from measure_psf import analyze, sample, scalar_sweep
from schematic import CORE, TB, LIB, HERE, MOS_MASTERS, width_overrides, write_build
from spectre_run import TRANSIENT, execute, read_json, run_spectre, save_json, sha256

SCRIPTS = HERE.parents[1] / "skills/paper-to-virtuoso/scripts"
sys.path.insert(0, str(SCRIPTS))
import check_prelayout


METRICS = (
    ("delay_ps", "worst_delay_ps", "ps", "Worst valid decision delay",
     "From the 50% rising clock to both outputs staying beyond 80%/20% for the evaluation."),
    ("energy_fJ", "mean_core_energy_fJ", "fJ", "Core energy per decision",
     "Mean integral of -VDD times the DUT supply-source current over complete clock periods."),
    ("kickback_mV", "peak_diff_kickback_mV", "mV", "Peak differential input kickback",
     "Peak deviation of the actual input differential from the applied differential during evaluation."),
)
DEFAULT_SCENARIOS = [
    {"name": name, "cload_fF": load, "ramp_ns": ramp}
    for name, load, ramp in (("nom", 20, 0.1), ("load", 35, 0.1),
                             ("heavy", 50, 0.1), ("slow", 20, 5))
]


def load_config(path):
    config = read_json(path)
    if not isinstance(config, dict):
        raise ValueError("The configuration must be a JSON object")
    defaults = {"pdk_library": "smic18mmrf", "pdk_revision": None, "mos_family": "nominal", "virtuoso": "virtuoso",
                "si": "si", "spectre": "spectre", "corner": "tt", "temp_C": 27,
                "vdd_V": 1.8, "vcm_V": 0.9, "diff_mV": 5, "scenarios": DEFAULT_SCENARIOS,
                "limits": {}, "candidate_widths_um": {}}
    extra = set(config) - set(defaults) - {"cds_lib", "model_file"}
    if extra:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(extra)))
    defaults.update(config)
    config = defaults
    width_overrides(config["candidate_widths_um"])
    if not isinstance(config["mos_family"], str) or config["mos_family"] not in MOS_MASTERS:
        raise ValueError("mos_family must be nominal or statistical")
    for key in ("cds_lib", "model_file"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError("Set " + key + " in the local configuration")
        value = (path.parent / Path(config[key]).expanduser()).resolve()
        if not value.is_file() or re.search(r'["\\\n\r]', str(value)):
            raise ValueError("Missing or unrepresentable {} path: {}".format(key, value))
        config[key] = value
    for key in ("virtuoso", "si", "spectre"):
        command = config[key]
        if not isinstance(command, str) or not command:
            raise ValueError("Invalid tool name for " + key)
        resolved = (shutil.which(command) if "/" not in command else
                    str((path.parent / Path(command).expanduser()).absolute()))
        if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
            raise ValueError("Executable unavailable: " + key)
        config[key] = resolved
    for key in ("pdk_library", "corner"):
        if not isinstance(config[key], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", config[key]):
            raise ValueError("Invalid " + key)
    for key in ("temp_C", "vdd_V", "vcm_V", "diff_mV"):
        if not check_prelayout.finite_number(config[key]):
            raise ValueError("Invalid condition " + key)
    margin = config["diff_mV"] / 2000
    if margin <= 0 or not (margin <= config["vcm_V"] <= config["vdd_V"] - margin):
        raise ValueError("Use a positive differential magnitude with both inputs within the supply rails")
    if not isinstance(config["limits"], dict) or set(config["limits"]) - {m[0] for m in METRICS}:
        raise ValueError("Use delay_ps, energy_fJ or kickback_mV in limits")
    if not all(check_prelayout.finite_number(v) and v >= 0 for v in config["limits"].values()):
        raise ValueError("Limits must be finite nonnegative upper bounds")
    return config


def cases(config):
    groups = config["scenarios"]
    if not isinstance(groups, list) or not groups:
        raise ValueError("Specify at least one load and supply-ramp scenario")
    pairs, names = [], set()
    for row in groups:
        if not isinstance(row, dict) or set(row) != {"name", "cload_fF", "ramp_ns"}:
            raise ValueError("Each scenario needs name, cload_fF and ramp_ns")
        name = row["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in names:
            raise ValueError("Use unique lower-case scenario names")
        names.add(name)
        for field in ("cload_fF", "ramp_ns"):
            if not check_prelayout.finite_number(row[field]) or row[field] <= 0:
                raise ValueError("Invalid scenario " + field)
        for suffix, sign in (("pos", 1), ("neg", -1)):
            pairs.append(dict(id=name + "_" + suffix, cload_fF=row["cload_fF"], ramp_ns=row["ramp_ns"],
                              delta_mV=sign * config["diff_mV"], corner=config["corner"],
                              temp_C=config["temp_C"], vdd_V=config["vdd_V"], vcm_V=config["vcm_V"]))
    return pairs


def build(stage, variant, config):
    execute([sys.executable, SCRIPTS / "new_task.py", "--project", "CMP18", "--mode", "design",
             "--out", stage], stage.parent, variant + "-init.log", 30)
    include = str(config["cds_lib"])
    if any(ch.isspace() for ch in include):
        include = '"' + include + '"'
    (stage / "cds.lib").write_text("INCLUDE " + include + "\n", encoding="utf-8")
    (stage / ".cdsinit").write_text("; Local startup for the reference benchmark.\n", encoding="utf-8")
    req = read_json(stage / "requirements.json")
    req.update(purpose="Rebuild and measure the published dynamic comparator", target_library=LIB,
               pdk_library=config["pdk_library"], process_version=config["pdk_revision"],
               supply_v=config["vdd_V"], electrical_changes_authorized=True, new_simulations_authorized=True)
    save_json(stage / "requirements.json", req)
    expected = write_build(stage, variant, config["pdk_library"], config["mos_family"], config["candidate_widths_um"])
    execute([config["virtuoso"], "-nograph", "-log", stage / "build.log", "-restore", stage / "build.il"],
            stage, "build.stdout", 240)
    log = (stage / "build.log").read_text(encoding="utf-8", errors="replace")
    if "CMP_BUILD_OK" not in log or not (stage / LIB).is_dir():
        raise ValueError("Native build did not finish. See " + str(stage / "build.log"))
    netlists = stage / "netlists"
    (netlists / ".cdsinit").write_text("; Isolated native SI process.\n", encoding="utf-8")
    (netlists / "si.env").write_text(
        'simLibName="{}"\nsimCellName="{}"\n'.format(LIB, TB)
        + 'simViewName="schematic"\nsimSimulator="spectre"\nsimNotIncremental=nil\n'
          'simReNetlistAll=t\nsimViewList=list("spectre" "veriloga" "schematic")\n'
          'simStopList=list("spectre" "veriloga")\nsimNetlistHier=t\n', encoding="utf-8")
    execute([config["si"], "-batch", "-cdslib", stage / "cds.lib", "-command", "nl"],
            netlists, "si.stdout", 180)
    native = (netlists / "netlist").read_text(encoding="utf-8")
    views = {(cell, view): read_json(stage / (cell + "-" + view + ".json"))
             for cell, view in ((CORE, "schematic"), (CORE, "symbol"), (TB, "schematic"))}
    report, models = audit(expected, views, native, config["pdk_library"])
    save_json(stage / "evidence/readback.json", report)
    # Use the saved parent deck as a common comparison input across variants.
    bench = native.split("\nends " + CORE + "\n", 1)[1].encode("utf-8")
    shared_bench = stage.parent / "common/testbench.scs"
    if variant == "baseline":
        shared_bench.write_bytes(bench)
    elif shared_bench.read_bytes() != bench:
        raise ValueError("The two native testbenches differ")
    acceptance = read_json(stage / "acceptance.json")
    acceptance.update(target_library=LIB)
    for field, evidence in (("parameter_connection_readback", ["evidence/readback.json", "build.log"]),
                            ("native_netlist", ["netlists/netlist", "netlists/si.stdout"]),
                            ("symbol_interfaces", [CORE + "-symbol.json", "evidence/readback.json"])):
        acceptance[field] = {"status": "verified", "evidence": evidence}
    acceptance["physical_implementation"]["area_basis"] = "Sum of MOS W times L in square micrometres"
    acceptance["physical_implementation"]["gate_area_um2"] = report["gate_area_um2"]
    save_json(stage / "acceptance.json", acceptance)
    (stage / "open.il").write_text("\n".join(
        'geOpen(?lib "{}" ?cell "{}" ?view "{}" ?mode "r")'.format(LIB, cell, view)
        for cell, view in ((CORE, "schematic"), (CORE, "symbol"), (TB, "schematic"))
    ) + "\n", encoding="utf-8")
    return native, models


def build_plan(stage, config, conditions, native, dependencies):
    references = ["../common/" + name for name in
                  ("testbench.scs", "measure_psf.py", "transient.txt", "model-dependencies.json", "tool.json")]
    # Direct input paths also let the existing checker detect later edits to installed models.
    plan = {"schema_version": 1, "inputs": ["netlists/netlist"], "reference_inputs": references + sorted(dependencies),
            "tests": [], "results": {}}
    for case in conditions:
        folder = stage / "simulation" / case["id"]
        folder.mkdir()
        (folder / "input.scs").write_text(
            TRANSIENT.format(native=native, model=config["model_file"], **case), encoding="utf-8")
        for key, _value, unit, metric, measurement in METRICS:
            target = None
            if key in config["limits"]:
                target = {"source": "Upper bound in the local configuration", "confirmed": True,
                          "limits": {"<=": config["limits"][key]}}
            elif key == "delay_ps":
                target = {"source": "4 ns evaluation interval of this benchmark", "confirmed": True,
                          "limits": {"<": 4000}}
            plan["tests"].append({
                "id": case["id"] + "." + key, "metric": metric, "unit": unit,
                "testbench": TB, "analysis": "tran",
                "conditions": dict(case, clock_period_ns=10, clock_edge_ps=100, source_ohm_per_input=1000),
                "measurement": measurement, "target": target,
                "inputs": ["simulation/{}/input.scs".format(case["id"])],
            })
    path = stage / "prelayout.json"
    save_json(path, plan)
    plan, before = check_prelayout.load_records(path, {})
    if any("error" in c for c in before.values()):
        raise ValueError("Incomplete pre-simulation snapshot. See " + str(path))
    save_json(stage / "evidence/before-simulation.json", before)
    return plan, before


def measure(trace, case):
    text = trace.read_text(encoding="utf-8")
    result = analyze(text, case["vdd_V"], case["vcm_V"], case["delta_mV"])
    if not result["precharge_and_decisions_pass"] or result["valid_decisions"] != 3:
        raise ValueError("Require correct precharge and all three signed decisions")
    sources = scalar_sweep(text, "time", ("SRC_P", "SRC_N", "VDD", "INP", "XDUT.XP", "XDUT.TAIL"))
    for cycle in result["cycles"]:
        t = cycle["clock_rise_ns"] * 1e-9 - 50e-12
        p, n, vdd = (sample(sources["time"], sources[name], t) for name in ("SRC_P", "SRC_N", "VDD"))
        if not all(math.isclose(actual, expected, abs_tol=1e-6) for actual, expected in (
                (p - n, case["delta_mV"] / 1000), ((p + n)/2, case["vcm_V"]), (vdd, case["vdd_V"]))):
            raise ValueError("Actual input stimulus or supply differs at the clock edge")
    time = result["cycles"][1]["clock_rise_ns"] * 1e-9 + 100e-12
    gate, drain, source = (sample(sources["time"], sources[k], time) for k in ("INP", "XDUT.XP", "XDUT.TAIL"))
    result["input_device_100ps"] = {"abs_vgs_V": abs(gate - source), "vds_V": drain - source, "vbs_V": -source}
    return result


def simulate_variant(stage, config, conditions, native, dependencies):
    plan, before = build_plan(stage, config, conditions, native, dependencies)
    warning_count = 0
    for case in conditions:
        name = case["id"]
        folder = stage / "simulation" / name
        result = None
        try:
            trace, loaded, warnings = run_spectre(config["spectre"], folder, config["model_file"],
                                                  ("tran.tran", "tran.tran.tran"))
            warning_count += warnings
            if loaded != dependencies:
                raise ValueError("Loaded dependencies changed since characterization")
            result = measure(trace, case)
            detail = {"status": "measured", "warnings": warnings, "trace": str(trace.relative_to(stage)),
                      "metrics": result}
        except (OSError, RuntimeError, ValueError) as exc:
            detail = {"status": "run_failed", "reason": str(exc)}
        save_json(folder / "measurement.json", detail)
        evidence = {str(f.relative_to(stage)): sha256(f) for f in
                    [folder / "measurement.json", folder / "spectre.out", folder / "console.out",
                     *sorted((folder / "psf").glob("tran.tran*"))] if f.is_file()}
        for key, field, unit, _metric, _method in METRICS:
            idx = name + "." + key
            entry = {"status": detail["status"], "context_sha256": before[idx]["context_sha256"],
                     "unit": unit, "evidence": evidence}
            if result is None:
                entry["reason"] = detail["reason"]
            else:
                entry["value"] = result[field]
            plan["results"][idx] = entry
        save_json(stage / "prelayout.json", plan)
        print(stage.name, name, detail["status"], flush=True)
    return warning_count


def check_variant(stage):
    hashes = {}
    current, contexts = check_prelayout.load_records(stage / "prelayout.json", hashes)
    checked = check_prelayout.check(current, stage, contexts, hashes)
    save_json(stage / "evidence/checked.json", checked)
    acceptance = read_json(stage / "acceptance.json")
    sound = all(t["status"] in ("PASS", "TARGET_UNCONFIRMED", "FAIL") for t in checked["tests"])
    acceptance["behavioral_simulation"] = {
        "status": "measured" if sound else "review_required", "evidence": ["prelayout.json", "evidence/checked.json"]}
    save_json(stage / "acceptance.json", acceptance)
    return checked


def write_report(out, config, conditions, comparison, status):
    records = {row["id"]: row for row in comparison.get("tests", [])}
    lines = ["# 动态比较器前仿结果", "",
             "工艺角 {}，温度 {:g} °C，电源 {:g} V，输入共模 {:g} V，差分输入 ±{:g} mV。".format(
                 config["corner"], config["temp_C"], config["vdd_V"], config["vcm_V"], config["diff_mV"]),
             "表内依次为基线和候选。负载指每个输出端的电容，能量取完整时钟周期的 DUT 电源积分。", "",
             "| 工况 | 负载 fF | 电源上升时间 ns | 判决延迟 ps | 每次判决能量 fJ | 差分反冲 mV |",
             "|---|---:|---:|---:|---:|---:|"]
    for case in conditions:
        cells = [case["id"], str(case["cload_fF"]), str(case["ramp_ns"])]
        for key, *_ in METRICS:
            row = records.get(case["id"] + "." + key, {"status": "MEASUREMENT_UNAVAILABLE"})
            cells.append("{:.4g} → {:.4g}".format(row["baseline"], row["candidate"])
                         if row["status"] == "COMPARABLE" else row["status"])
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "完整配对工况 {} / {}。候选超限项目为 {}，另有 {} 项测值待设限值。".format(
                      status["matched_conditions"], len(conditions), "、".join(status["candidate_unmet_limits"]) or "0 项",
                      len(status["candidate_metrics_without_limits"])), "",
                  "## 器件尺寸与工作点", ""])
    for variant in ("baseline", "candidate"):
        stage = out / variant
        area = read_json(stage / "evidence/readback.json")["gate_area_um2"]
        lines.append("- {}的 MOS 栅面积合计为 {:g} µm²。".format("基线" if variant == "baseline" else "候选", area))
        first = read_json(stage / "simulation" / conditions[0]["id"] / "measurement.json")
        if first["status"] == "measured":
            point = first["metrics"]["input_device_100ps"]
            nearby = phase_lookup(out / "characterization/table.csv", point)
            save_json(stage / "evidence/input-sizing.json", {
                "case": conditions[0]["id"], "device": "MNINP", "after_second_rising_clock_ps": 100,
                "operating_point_V": point, "nearby_characterized_rows": nearby})
            location = "  输入管在第二次判决开始 100 ps 时，|VGS|={abs_vgs_V:.3f} V，VDS={vds_V:.3f} V，VBS={vbs_V:.3f} V。".format(**point)
            if nearby:
                ratios = [r["gm_over_id_per_V"] for r in nearby]
                location += "参考器件相近偏置的直流 gm/ID 为 {:.2f} 至 {:.2f} V⁻¹。".format(min(ratios), max(ratios))
            else:
                location += "这个工作点在有效查表范围之外。"
            lines.append(location)
    lines.extend(["", "查表见 `characterization/table.csv`。各版本的 `evidence/input-sizing.json` 保存参考器件尺寸与实际命中的偏置。", "",
                  "## 打开原生视图", "", "在已连接图形显示的 Cadence 主机中选择对应版本运行。", "", "```sh"])
    lines.extend(["(cd {} && {} -restore open.il)".format(shlex.quote(str(out / variant)), shlex.quote(config["virtuoso"]))
                  for variant in ("baseline", "candidate")])
    lines.extend(["```", "", "各版本目录保存原生 OA、网表、仿真前输入摘要、PSF 和日志。"
                  "打开原理图、symbol 与测试台检查全图和局部后，再填写目视验收。", ""])
    if "maestro" in status:
        lines.extend(["## 在 ADE Maestro 中复跑", "",
                      "在所选版本的 Library Manager 中打开 `{}/{}/maestro`。".format(LIB, TB),
                      "正负输入各是一个可编辑测试。原生历史、逐项测值、超限项与警告见下列记录。", "",
                      "- [基线历史核查](baseline/maestro-evidence/maestro-report.json)",
                      "- [候选历史核查](candidate/maestro-evidence/maestro-report.json)", ""])
    (out / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def run(config, conditions, out, reuse, maestro=False):
    common = out / "common"
    common.mkdir()
    shutil.copyfile(str(HERE / "measure_psf.py"), str(common / "measure_psf.py"))
    (common / "transient.txt").write_text(TRANSIENT, encoding="utf-8")
    execute([config["spectre"], "-W"], common, "version.txt", 30)
    version = (common / "version.txt").read_text(encoding="utf-8", errors="replace").strip()
    save_json(common / "tool.json", {"spectre": config["spectre"], "version": version})
    warnings, native_histories = {}, {}
    for variant in ("baseline", "candidate"):
        stage = out / variant
        native, models = build(stage, variant, config)
        if variant == "baseline":
            lut = characterize(out / "characterization", config, models, version, reuse)
            dependencies = lut["dependencies"]
            save_json(common / "model-dependencies.json", dependencies)
            save_json(out / "characterization/sizing-example.json",
                      sizing_example(out / "characterization/table.csv", config["vdd_V"]))
            print("Device table:", lut["mode"], lut["points"], "points", flush=True)
        warnings[variant] = simulate_variant(stage, config, conditions, native, dependencies)
        if maestro:
            from maestro import run_view
            native_histories[variant] = run_view(stage, config, conditions, measure, METRICS)
    reports = {variant: check_variant(out / variant) for variant in ("baseline", "candidate")}
    comparison = check_prelayout.compare(reports["baseline"], reports["candidate"])
    save_json(out / "comparison.json", comparison)
    unmet = [t["id"] for t in reports["candidate"]["tests"] if t["status"] == "FAIL"]
    pending = [t["id"] for t in reports["candidate"]["tests"] if t["status"] == "TARGET_UNCONFIRMED"]
    ok = comparison["status"] == "COMPARABLE" and not unmet
    comparable = {row["id"] for row in comparison.get("tests", []) if row["status"] == "COMPARABLE"}
    matched = sum(all(case["id"] + "." + metric[0] in comparable for metric in METRICS) for case in conditions)
    status = {"status": "completed" if ok else "review_required", "mos_family": config["mos_family"],
              "requested_conditions": len(conditions),
              "matched_conditions": matched, "comparable_metrics": len(comparable),
              "candidate_limits_met": comparison.get("candidate_limits_met", False),
              "candidate_unmet_limits": unmet, "candidate_metrics_without_limits": pending,
              "spectre_warnings": warnings, "table_mode": lut["mode"]}
    if maestro:
        status["maestro"] = {variant: report["status"] for variant, report in native_histories.items()}
        if any(row["unmet"] for row in native_histories["candidate"]["tests"].values()):
            status["status"] = "review_required"
    write_report(out, config, conditions, comparison, status)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Local tool and SMIC18 configuration")
    parser.add_argument("--out", type=Path, required=True, help="New output directory on the Cadence host")
    parser.add_argument("--reuse-lut", type=Path, help="Characterization directory from a previous run")
    parser.add_argument("--maestro", action="store_true", help="Save, reopen and run an editable ADE Maestro testbench")
    args = parser.parse_args()
    try:
        if os.name != "posix":
            raise ValueError("Run the native benchmark on the Linux Cadence host")
        config = load_config(args.config.resolve())
        conditions = cases(config)
        out = args.out.expanduser().resolve()
        out.mkdir(parents=True, mode=0o700)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    save_json(out / "status.json", {"status": "running"})
    try:
        result = run(config, conditions, out, args.reuse_lut.expanduser().resolve() if args.reuse_lut else None,
                     args.maestro)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        result = {"status": "failed", "reason": str(exc)}
    save_json(out / "status.json", result)
    print(result["status"], out, result.get("reason", ""), flush=True)
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
