"""Build a finite, reproducible gm/ID search from measured TT curves."""
import math
import shlex

import mos_table
import native


LENGTHS = (("short", 0.50), ("long", 1.00))
WIDTH_RANGE = (0.50, 20.0)
TARGETS = (10, 12, 15, 18, 20, 22)


def recipe():
    return dict(version=1, lengths_um=[v for _, v in LENGTHS], gm_id_targets_per_V=list(TARGETS),
                width_range_um=list(WIDTH_RANGE), width_step_um=0.05,
                source=dict(corner="tt", polarity="N", vds_V=0.65, vbs_V=0), selection="min_gate_area")


def candidates(rows, process, current):
    designs, trials, geometries = {}, {}, {}
    # Keep the familiar short/long names when another target rounds to the same geometry.
    for label, length in LENGTHS:
        for target in (15,) + tuple(t for t in TARGETS if t != 15):
            name = label if target == 15 else "{}-g{}".format(label, target)
            sizing = mos_table.choose(rows, length, current, target, (0, math.inf))
            sizing.update(source_process=process, source_corner="tt")
            width = sizing["width_um"]
            entry = dict(target_gm_id_per_V=target, length_um=length, width_um=width,
                         continuous_width_um=sizing["continuous_width_um"], selected_row=sizing["selected_row"])
            if not WIDTH_RANGE[0] <= width <= WIDTH_RANGE[1]:
                entry["status"] = "outside_width_range"
            elif (length, width) in geometries:
                entry.update(status="same_geometry", design=geometries[length, width])
            else:
                entry.update(status="simulated", design=name)
                geometries[length, width] = name
                designs[name] = sizing
            trials[name] = entry
    return designs, trials


def selected_opener(name):
    return ('#!/bin/sh\nset -eu\n'
            'base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'export MIRROR_SCHEMATIC_DIR="$base/' + name + '/views"\n'
            'case "${1:-bench}" in\n  bench) cell=TB_BIAS_MIRROR.sch ;;\n'
            '  core) cell=BIAS_MIRROR.sch ;;\n  symbol) cell=BIAS_MIRROR.sym ;;\n'
            '  *) echo "Use bench, core, or symbol" >&2; exit 2 ;;\nesac\n'
            'exec "${XSCHEM:-xschem}" --tcl ' + shlex.quote(native.TCL_PATH) + ' "$MIRROR_SCHEMATIC_DIR/$cell"\n')


def worst_traces(case):
    metrics = {name: value["metrics"] for name, value in case["corners"].items()}
    dc = max(metrics, key=lambda c: metrics[c]["worst_error_pct"])
    def settling_key(c):
        row = metrics[c]
        return (row["settling_ns"] is None, row.get("settling_lower_bound_ns", row["settling_ns"]))
    return dc, max(metrics, key=settling_key)


def diagnostic_candidate(designs, req):
    """Choose a failed build to inspect without calling it a qualified design."""
    def priority(name):
        case = designs[name]
        error = case["worst_error_pct"]
        time = case["worst_settling_ns"]
        return (max(0, error - req["max_error_pct"]), time if time is not None else math.inf,
                case["sizing"]["mirror_pair_gate_area_um2"], name)
    return min(designs, key=priority, default=None)


def result_lines(report):
    lines = ["", "## 按当前规格搜索尺寸", "",
             "每种工艺在 0.50、1.00 µm 两档沟道长度上各查 6 个 gm/ID 目标（10、12、15、18、20、22 V⁻¹）。"
             "按实测 ID/W 求 W，再以 0.05 µm 取整。单指 W 限于 0.50–20 µm，相同 W/L 只测一次。"
             "每个入围尺寸均用独立的原理图、父级网表和三个工况检验。两管栅面积为 2WL。", ""]
    req = report["requirements"]
    for proc, data in report["processes"].items():
        lines += ["### " + proc, ""]
        selected = data["selected"]
        if selected:
            size = data["designs"][selected]["sizing"]
            base = proc + "/" + selected
            lines += ["**{}** 在本次实测范围内达标且两管栅面积最小，单管 W/L 为 {:.2f}/{:.2f} µm，"
                      "两管合计 {:.2f} µm²。".format(selected, size["width_um"], size["length_um"], size["mirror_pair_gate_area_um2"]),
                      "从结果目录运行 `sh " + proc + "/open.sh bench` 打开父级，将 `bench` 换成 `core` 或 `symbol` 查看核心或符号。"
                      " [核心]({0}/views/BIAS_MIRROR.sch) · [父级]({0}/views/TB_BIAS_MIRROR.sch) · [选中的表行和尺寸]({0}/sizing.json)".format(base), ""]
        elif data["designs"]:
            diagnostic = data["diagnostic"]
            base = proc + "/" + diagnostic
            lines += ["**本次搜索的合法尺寸全部超限。** 可先打开 {} 排查。优先看直流误差达标且建立最快的一版，"
                      "直流均超限时先看误差最小的一版。".format(diagnostic),
                      "从结果目录运行 `sh " + base + "/open.sh bench`。"
                      " [超限的核心]({0}/views/BIAS_MIRROR.sch) · [超限的父级测试台]({0}/views/TB_BIAS_MIRROR.sch)".format(base), ""]
        else:
            lines += ["**全部查表尺寸都超出单指宽度范围，没有本工艺候选可仿真。**", ""]
        if data["designs"]:
            lines += ["| 候选 | W/L (µm) | 两管栅面积 (µm²) | 最大误差、输出电压、工况 | 最慢建立、工况 | 判定 |",
                      "|---|---:|---:|---|---|---|"]
        for name, case in data["designs"].items():
            lines.append(case_line(proc, name, case, req))
        excluded = ["{} ({:.2f} µm)".format(n,t["width_um"]) for n,t in data["trials"].items()
                    if t["status"] == "outside_width_range"]
        duplicates = [n + " → " + t["design"] for n,t in data["trials"].items() if t["status"] == "same_geometry"]
        if excluded:
            lines += ["", "超出单指宽度范围的查表结果：" + "、".join(excluded) + "。"]
        if duplicates:
            lines += ["", "合并为相同 W/L 的候选：" + "、".join(duplicates) + "。"]
        lines.append("")
    if report["copied_control"]:
        lines += ["### 将 SKY130 所选尺寸直接用于 GF180 的对照", "",
                  "| 尺寸来源 | W/L (µm) | 两管栅面积 (µm²) | 最大误差、输出电压、工况 | 最慢建立、工况 | 判定 |",
                  "|---|---:|---:|---|---|---|", case_line("gf180", "copy-sky130", report["copied_control"], req), ""]
    lines += ["ΣWL 只计两只 MOS 的栅面积。本次前仿覆盖表内离散尺寸和指定的三个工况。",
              "[输入规格](requirements.json) · [搜索设置](search.json) · [查表记录、工作点和每个工况的结论](summary.json) · [原始文件校验清单](evidence.json)", ""]
    return lines


def case_line(process, name, case, req):
    size = case["sizing"]
    dc_corner, tran_corner = worst_traces(case)
    dc = case["corners"][dc_corner]["metrics"]
    tran = case["corners"][tran_corner]["metrics"]
    reasons = []
    if dc["worst_error_pct"] > req["max_error_pct"]:
        reasons.append("直流误差超限")
    if tran["settling_ns"] is None:
        elapsed = "&gt;{:.2f} ns".format(tran["settling_lower_bound_ns"])
        reasons.append("观察结束仍未建立")
    else:
        elapsed = "{:.2f} ns".format(tran["settling_ns"])
        if tran["settling_ns"] > req["max_settling_ns"]:
            reasons.append("建立超时")
    base = process + "/" + name
    return ("| [{name}]({base}/sizing.json) | {w:.2f} / {l:.2f} | {area:.2f} | "
            "[{error:.3f}% @ {v:.2f} V · {dc}]({base}/{dc}/dc.dat) | "
            "[{elapsed} · {tr}]({base}/{tr}/transient.dat) | {result} |").format(
                name=name, base=base, w=size["width_um"], l=size["length_um"], area=size["mirror_pair_gate_area_um2"],
                error=dc["worst_error_pct"], v=dc["worst_vout_V"], dc=dc_corner, elapsed=elapsed, tr=tran_corner,
                result="、".join(reasons) or "达标")
