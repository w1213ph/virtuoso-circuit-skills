"""Assemble a relocatable, offline-readable ADC bias project from a measured sizing run."""
import argparse
from html import escape
from pathlib import Path
import re
import shutil
from string import Template
import tempfile

import preview
import transfer


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = {
    "examples/cross-pdk-current-mirror": ("transfer.py", "transfer_bench.py", "transfer_models.py", "sizing_search.py",
                                         "gf180-models.lock.json"),
    "examples/sky130-current-mirror": ("run.py", "fetch_models.py", "measure.py", "mos_model.py", "mos_table.py",
                                      "native.py", "models.lock.json", "adc-bias.example.json", "BIAS_MIRROR.sch",
                                      "BIAS_MIRROR.sym", "NMOS18.sym", "TB_BIAS_MIRROR.sch"),
    "skills/paper-to-virtuoso/scripts": ("check_prelayout.py",),
}
LABELS = {"core": "核心原理图", "symbol": "功能符号", "bench": "父级测试台"}


def process_section(process, data, req):
    heading = "<section><h2>{}</h2>".format({"sky130": "SKY130", "gf180": "GF180MCU"}[process])
    selected = data["selected"]
    name = selected or data["diagnostic"]
    if not name:
        return heading + '<p class="fail">全部查表宽度超出 0.50–20 µm，本工艺没有可仿真的候选。</p></section>'
    case = data["designs"][name]
    base = "evidence/{}/{}".format(process, name)
    dc_corner, tran_corner = transfer.search.worst_traces(case)
    dc = case["corners"][dc_corner]["metrics"]
    tran = case["corners"][tran_corner]["metrics"]
    elapsed = tran["settling_ns"]
    settling = ("{:.2f} ns".format(elapsed) if elapsed is not None else
                "观察结束仍未建立，已超过 {:.2f} ns".format(tran["settling_lower_bound_ns"]))
    size = case["sizing"]
    states = "、".join("{}（{:g} °C，{:g} V）".format(c.upper(), t, v) for c, t, v in data["states"])
    directions = {(-1, 0): "向左", (1, 0): "向右", (0, -1): "向上", (0, 1): "向下"}
    leads = case["native_readback"]["parent_leads"]
    pins = "，".join("<code>{}</code> {}".format(pin, directions[tuple(leads[pin]["outward_vector"])])
                    for pin in ("IREF", "IOUT", "VSS"))
    status = ('<strong class="pass">达标</strong>，在本次候选中两管栅面积最小' if selected else
              '<strong class="fail">本次候选全部超限</strong>，以下为排查用的尺寸')
    parts = [heading, "<p>{}。共仿真 {} 个本工艺尺寸，当前图对应 <code>{}</code>。</p>".format(
        status, len(data["designs"]), name), "<p>本次工况为{}。</p>".format(states),
        '<p>单管 W/L 为 {:.2f}/{:.2f} µm，两管总栅面积 {:.2f} µm²。'
        '该工艺 TT 表选中行的电流密度为 {:.3f} µA/µm。<a href="{}/sizing.json">查表行与尺寸</a></p>'.format(
            size["width_um"], size["length_um"], size["mirror_pair_gate_area_um2"],
            size["selected_row"]["id_per_width_uA_um"], base),
        '<ul><li>{} 工况，输出 {:.2f} V 时误差最大，为 <strong>{:.3f}%</strong>（限值 {:g}%）。'
        '<a href="{}/{}/dc.dat">直流原始数据</a></li>'.format(
            dc_corner.upper(), dc["worst_vout_V"], dc["worst_error_pct"], req["max_error_pct"], base, dc_corner),
        '<li>{} 工况建立最慢，<strong>{}</strong>（限值 {:g} ns）。'
        '<a href="{}/{}/transient.dat">瞬态原始数据</a></li></ul>'.format(
            tran_corner.upper(), settling, req["max_settling_ns"], base, tran_corner),
        '<figure><a href="images/{0}-waveforms.png"><img src="images/{0}-waveforms.png" '
        'alt="{0} 限制尺寸选择的直流误差和开启建立波形"></a><figcaption>'
        '左图为输出电压扫描，右图从实测参考电流达到 99% 开始计时。虚线为规格上限。'
        '</figcaption></figure><h3>打开电路</h3>'.format(process),
        '<p>父级中，外部参考接 IREF，输出支路为 IOUT，VSS 接地。按图中读回的首段方向，{}。</p><div class="views">'.format(pins)]
    for kind, view, image in preview.views(process, data):
        title = LABELS[kind]
        parts.append('<figure><a href="images/{0}"><img src="images/{0}" width="1200" height="800" '
                     'alt="{1} {2}的 Xschem 原生预览"></a><figcaption>{2} · '
                     '<a href="evidence/{3}">可编辑源文件</a></figcaption></figure>'.format(
                         image, process, title, view.as_posix()))
    parts.append('</div><p>在 Linux 上从解压目录运行 <code>sh {}/open.sh bench</code>，即可在 Xschem 打开这组尺寸的测试台。</p></section>'.format(base))
    return "\n".join(parts)


def build(out, images, destination, model_license, revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Supply the 40-character source commit for this build")
    if destination.exists() or any(p == destination or p in destination.parents for p in (out, images)):
        raise ValueError("Use a new project directory outside the evidence and previews")
    report = transfer.verify(out)
    if "search" not in report:
        raise ValueError("Use the measured output of run --optimize")
    records = preview.inventory(out, images, report)
    if records != transfer.read_json(images / "previews.json"):
        raise ValueError("The native previews do not match this run and these editable views")
    digest = transfer.mirror.digest(model_license)
    if any(digest != lock["files"]["LICENSE"] for _, _, _, lock in transfer.PROCESSES):
        raise ValueError("The upstream license differs from the two pinned PDK sources")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        project = Path(directory) / "project"
        evidence = project / "evidence"
        evidence.mkdir(parents=True)
        for name in [*transfer.read_json(out / "evidence.json"), "evidence.json"]:
            target = evidence / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / name, target)
        (project / "images").mkdir()
        for name in [*records["images"], "previews.json"]:
            shutil.copyfile(images / name, project / "images" / name)
        for process, data in report["processes"].items():
            name = data["selected"] or data["diagnostic"]
            if name:
                from waveforms import draw
                draw(evidence, process, name, data["designs"][name], report["requirements"],
                     project / "images" / (process + "-waveforms.png"))
        for folder, names in SOURCE.items():
            target = project / "source" / folder
            target.mkdir(parents=True)
            for name in names:
                shutil.copyfile(REPO / folder / name, target / name)
        for name in ("LICENSE", "NOTICE"):
            shutil.copyfile(REPO / name, project / name)
        shutil.copyfile(model_license, project / "model-license.txt")
        notices = ["SKY130: Copyright 2020 The SkyWater PDK Authors.",
                   "GF180MCU: Copyright 2022 GlobalFoundries PDK Authors.",
                   "SKY130 wrapper: includes global options and parameters from models/all.spice.",
                   "GF180MCU wrapper: selects named sections and sets deterministic statistics switches.",
                   "Upstream model source trees for new simulations are retrieved and checked separately.", ""]
        for process, _, _, lock in transfer.PROCESSES:
            notices.append("{}: https://github.com/{}/tree/{}".format(process, lock["repository"], lock["revision"]))
        (project / "model-notices.txt").write_text("\n".join(notices) + "\n", encoding="utf-8")
        shutil.copyfile(HERE / "starter/bias.py", project / "bias.py")
        shutil.copyfile(evidence / "requirements.json", project / "my-bias.json")
        req = report["requirements"]
        template = Template((HERE / "starter/index.html").read_text(encoding="utf-8"))
        html = template.substitute(current=format(req["reference_uA"], "g"), load=format(req["reference_load_fF"], "g"),
                                   error=format(req["max_error_pct"], "g"), deadline=format(req["max_settling_ns"], "g"),
                                   purpose=escape(req.get("purpose", "")), revision=revision,
                                   processes="\n".join(process_section(p, d, req) for p, d in report["processes"].items()))
        (project / "index.html").write_text(html, encoding="utf-8")
        project.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Measured sizing run")
    parser.add_argument("--images", type=Path, required=True, help="Native PNG directory produced by preview.py")
    parser.add_argument("--project", type=Path, required=True, help="New portable project directory")
    parser.add_argument("--revision", required=True, help="Source Git commit")
    parser.add_argument("--model-license", type=Path, help="Pinned upstream LICENSE when using archived evidence")
    args = parser.parse_args()
    try:
        build(args.out.resolve(), args.images.resolve(), args.project.resolve(),
              args.model_license or args.out / "models/sky130/LICENSE", args.revision)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")
