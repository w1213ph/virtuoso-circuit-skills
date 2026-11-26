"""Build, size and independently replay a native SMIC18 ADC bias mirror."""

import argparse
import json
import math
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "dynamic-comparator"), str(HERE.parent / "sky130-current-mirror")]
import bias_sim
import bias_views
import bias_ade
from mos_table import choose
from run import load_requirements
from run_native import load_config
from spectre_run import read_json, save_json, sha256


TARGETS = (12, 15, 18, 20)
WIDTH_RANGE = (0.5, 20.0)


def config_for_site(path):
    raw = read_json(path)
    allowed = {"cds_lib", "model_file", "pdk_library", "pdk_revision", "virtuoso", "si", "spectre"}
    if (
        not isinstance(raw, dict)
        or set(raw) - allowed
        or not isinstance(raw.get("pdk_revision"), str)
        or raw["pdk_revision"].strip() in ("", "your-installed-revision")
    ):
        raise ValueError("Supply the local SMIC18 revision, tool paths, cds.lib and Spectre model file")
    return load_config(path)


def requirements(path):
    req, _ = load_requirements(path)
    if req.get("assumed_fields") or req["selection"] != "min_gate_area":
        raise ValueError("Supply confirmed current, load, error, deadline and selection=min_gate_area")
    return req


def candidate_grid(rows, req):
    grid = {}
    for length in bias_sim.LENGTHS:
        for target in TARGETS:
            name = "l{:03d}-g{:02d}".format(round(length * 100), target)
            sizing = choose(rows, length, req["reference_uA"], target, (0, math.inf))
            if not WIDTH_RANGE[0] <= sizing["width_um"] <= WIDTH_RANGE[1]:
                state = "outside_width_range"
            elif any(
                s["sizing"]["width_um"] == sizing["width_um"]
                and s["sizing"]["length_um"] == length
                and s["state"] != "outside_width_range"
                for s in grid.values()
            ):
                state = "same_geometry"
            else:
                state = "eligible"
            grid[name] = dict(sizing=sizing, state=state)
    return grid


def search(grid, measure):
    """Measure both starting lengths, then visit every smaller eligible geometry until the area bound closes."""

    def area_key(name):
        s = grid[name]["sizing"]
        return s["mirror_pair_gate_area_um2"], s["length_um"], name

    seeds = [n for n in grid if grid[n]["sizing"]["target_gm_id_per_V"] == 15]
    order = seeds + sorted((n for n in grid if n not in seeds), key=area_key)
    best = None
    measured = {}
    for name in order:
        item = grid[name]
        if item["state"] != "eligible":
            continue
        if name not in seeds and best and area_key(name) >= area_key(best):
            item["state"] = "area_bound"
            continue
        measured[name] = measure(name, item["sizing"])
        item["state"] = "measured"
        if measured[name]["passed"] and (best is None or area_key(name) < area_key(best)):
            best = name
    return best, measured


def evaluate(out, site, req, config=None):
    cards = {}
    for length in bias_sim.LENGTHS:
        folder = out / "reference" / ("l{:03d}".format(round(length * 100)))
        if config:
            bias_views.build(folder, config, 2.0, length, req)
        _, cards[length], _ = bias_views.readback(folder, site["pdk_library"], 2.0, length, req)
    tool = config["spectre"] if config else None
    points = {}
    for state in bias_sim.STATES:
        rows = bias_sim.lookup(out / "lookup" / state[0], site, state, cards, tool)
        points[state[0]] = len(rows)
        if state[0] == "tt":
            grid = candidate_grid(rows, req)
    parent_bench = None

    def measure(name, sizing):
        nonlocal parent_bench
        folder = out / "designs" / name
        width, length = sizing["width_um"], sizing["length_um"]
        if config:
            bias_views.build(folder, config, width, length, req)
            save_json(folder / "sizing.json", sizing)
        elif read_json(folder / "sizing.json") != sizing:
            raise ValueError("Saved geometry differs from its measured SMIC18 lookup row: " + name)
        native, _, oa = bias_views.readback(folder, site["pdk_library"], width, length, req)
        parent = native.split("\nends " + bias_views.CORE + "\n", 1)[1]
        if parent_bench is not None and parent != parent_bench:
            raise ValueError("The native testbench changed between candidate designs")
        parent_bench = parent
        corners = bias_sim.measure_cases(folder, site, native, req, tool)
        passed = all(c["passed"] for c in corners.values())
        times = [c["metrics"]["settling_ns"] for c in corners.values()]
        result = dict(
            native_readback=oa,
            corners=corners,
            passed=passed,
            worst_error_pct=max(c["metrics"]["worst_error_pct"] for c in corners.values()),
            worst_settling_ns=max(times) if None not in times else None,
        )
        if None in times:
            result["settling_lower_bound_ns"] = max(
                c["metrics"].get("settling_lower_bound_ns", 0) for c in corners.values()
            )
        if config:
            print(name, width, length, "PASS" if passed else "FAIL", flush=True)
        return result

    best, measured = search(grid, measure)
    if {p.name for p in (out / "designs").iterdir() if p.is_dir()} != set(measured):
        raise ValueError("Measured candidate inventory differs from the repeatable search")
    return dict(
        requirements=req,
        model_revision=site["pdk_revision"],
        model_sha256=site["model_sha256"],
        states=bias_sim.STATES,
        lookup_points=points,
        width_range_um=WIDTH_RANGE,
        grid=grid,
        designs=measured,
        selected=best,
    )


def report(data):
    req = data["requirements"]
    lines = [
        "# SMIC18 ADC 偏置电流镜前仿",
        "",
        req.get("purpose", ""),
        "",
        "要求为 {reference_uA:g} µA 外部参考，参考端 {reference_load_fF:g} fF，"
        "最大误差 {max_error_pct:g}%，建立上限 {max_settling_ns:g} ns。".format(**req),
        "",
        "| 方案 | 单管 W/L (µm) | 两管 ΣWL (µm²) | TT 参考管实际 gm/ID (V⁻¹) | 三工况最大误差 | 最慢建立 | 判定 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name, result in data["designs"].items():
        s = data["grid"][name]["sizing"]
        settling = (
            "{:.2f} ns".format(result["worst_settling_ns"])
            if result["worst_settling_ns"] is not None
            else "超过 {:.2f} ns 观察窗".format(result["settling_lower_bound_ns"])
        )
        slow = max(
            result["corners"],
            key=lambda c: result["corners"][c]["metrics"]["settling_ns"]
            if result["corners"][c]["metrics"]["settling_ns"] is not None
            else math.inf,
        )
        error = max(result["corners"], key=lambda c: result["corners"][c]["metrics"]["worst_error_pct"])
        lines.append(
            "| [{}](designs/{}/open.il) | {:.2f} / {:.2f} | {:.2f} | {:.2f} | {:.3f}% ({}) | {} ({}) | {} |".format(
                name,
                name,
                s["width_um"],
                s["length_um"],
                s["mirror_pair_gate_area_um2"],
                result["corners"]["tt"]["operating_point"]["MREF"]["gm_id_per_V"],
                result["worst_error_pct"],
                error.upper(),
                settling,
                slow.upper(),
                "达标" if result["passed"] else "超限",
            )
        )
    selected = data["selected"]
    if selected:
        base = "designs/" + selected
        lines += [
            "",
            "**选中 {}**。这是本次两档管长和四档目标 gm/ID 中栅面积最小的达标尺寸。".format(selected),
            " [打开核心、symbol 和父级测试台的脚本]({}/open.il) · [选中尺寸的查表行]({}/sizing.json)".format(
                base, base
            ),
            "在 Library Manager 打开 `{} / {} / maestro`，查看 {} 的六个 DC 与瞬态测试。".format(
                bias_views.LIB, bias_views.TB, data["maestro"]["history"]
            ),
        ]
    elif data["designs"]:
        lines += [
            "",
            "本次搜索范围内没有达标尺寸。表中工况指出误差或建立时间最差的位置。"
            "在对应候选目录中打开原理图，检查该工况 dc/ 和 tran/ 下的波形。",
        ]
    else:
        lines += [
            "",
            "查表得到的几何均超出本例的允许宽度。请在 summary.json 的 grid 中核对电流密度、"
            "预测宽度和范围，再决定是否更改尺寸范围或支路结构。",
        ]
    lines += ["", "[各支路实际工作点、候选搜索记录与面积](summary.json)", ""]
    return "\n".join(lines)


def run(args):
    config = config_for_site(args.config)
    site = dict(
        pdk_library=config["pdk_library"],
        pdk_revision=config["pdk_revision"],
        model_file=str(config["model_file"]),
        model_sha256=sha256(config["model_file"]),
    )
    req = requirements(args.requirements)
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    for name in ("lookup", "reference", "designs"):
        (out / name).mkdir()
    save_json(out / "site.json", site)
    save_json(out / "requirements.json", req)
    data = evaluate(out, site, req, config)
    data["maestro"] = None
    if data["selected"]:
        name = data["selected"]
        stage = out / "designs" / name
        bias_ade.launch(stage, config, req)
        data["maestro"] = bias_ade.review(stage, site, req, data["designs"][name]["corners"])
        opener = '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        opener += (
            'cd "$root/designs/'
            + name
            + '"\ntool='
            + shlex.quote(config["virtuoso"])
            + '\n[ -x "$tool" ] || tool=virtuoso\n'
        )
        opener += 'exec "${VIRTUOSO:-$tool}" -restore open.il\n'
        (out / "open.sh").write_text(opener, encoding="utf-8")
    save_json(out / "summary.json", data)
    (out / "RESULTS.md").write_text(report(data), encoding="utf-8")
    return data


def verify(out, config_path=None):
    site = read_json(out / "site.json")
    if config_path:
        config = config_for_site(config_path)
        if (
            site["pdk_revision"] != config["pdk_revision"]
            or site["pdk_library"] != config["pdk_library"]
            or sha256(config["model_file"]) != site["model_sha256"]
        ):
            raise ValueError("PDK configuration differs from this measurement")
        for job in out.glob("**/job.json"):
            if any(sha256(Path(p)) != digest for p, digest in read_json(job)["dependencies"].items()):
                raise ValueError("An actually loaded Spectre dependency changed")
    data = evaluate(out, site, requirements(out / "requirements.json"))
    name = data["selected"]
    data["maestro"] = (
        bias_ade.review(out / "designs" / name, site, data["requirements"], data["designs"][name]["corners"])
        if name
        else None
    )
    if read_json(out / "summary.json") != json.loads(json.dumps(data)) or (out / "RESULTS.md").read_text(
        encoding="utf-8"
    ) != report(data):
        raise ValueError("Reported selection or metrics differ from native raw evidence")
    return data


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action")
    create = sub.add_parser(
        "run", help="Create native OA views, extract SI and measure every necessary candidate"
    )
    create.add_argument("--config", type=Path, required=True)
    create.add_argument("--requirements", type=Path, default=HERE / "fast-bias.example.json")
    create.add_argument("--out", type=Path, required=True)
    replay = sub.add_parser(
        "verify", help="Independently recompute the search from OA readbacks, SI and PSF ASCII"
    )
    replay.add_argument(
        "--config", type=Path, help="Also verify current installed tools, PDK and model dependencies"
    )
    replay.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    if not args.action:
        cli.error("Choose run or verify")
    try:
        args.out = args.out.expanduser().resolve()
        if args.config:
            args.config = args.config.expanduser().resolve()
        data = run(args) if args.action == "run" else verify(args.out, args.config)
        print("Selected:", data["selected"], "| measured:", len(data["designs"]))
        sys.exit(0 if data["selected"] else 1)
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        cli.exit(2, str(exc) + "\n")
