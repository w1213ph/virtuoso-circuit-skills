"""Show the measured sampling decision and the native circuits that produced it."""
from html import escape
import os
from pathlib import Path
from string import Template
import struct
import subprocess


CELLS = (("bench", "父级测试台", "TB_ADRV_SAMPLE.sch"), ("core", "放大器", "ADRV_OTA.sch"),
         ("sampler", "传输门与采样电容", "ADRV_SAMPLER.sch"), ("symbol", "放大器符号", "ADRV_OTA.sym"))
NAMES = {"baseline": "短沟道有源负载", "candidate": "长沟道有源负载"}
EDGES = {"up": "上升", "down": "下降"}
PHASES = {"track": "跟踪", "hold": "保持"}


def design_title(name, size):
    if name in NAMES:
        return NAMES[name]
    d = size["devices"]
    return "{}沟道负载 · 镜像 {}∶{} · {} 组传输门".format(
        "短" if name.startswith("short-") else "长", d["XLA"][2], d["XLB"][2], d["XSN"][2])


def limiting_measurements(measurements):
    def worst(metric):
        rows = ((corner, edge, values[metric]) for corner, case in measurements.items()
                for edge, values in case["transient"]["edges"].items())
        return max(rows, key=lambda row: row[2])
    margins = ((corner, mode, case[mode]["phase_margin_deg"]) for corner, case in measurements.items()
               for mode in ("track", "hold"))
    return worst("track_error_mV"), worst("hold_error_mV"), min(margins, key=lambda row: row[2])


def sampling_figure(stage, req, half, track, hold, png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from bench import EDGE_NS, TRACE, schedule
    from mos_table import table

    intervals, _ = schedule(req)
    figure, axes = plt.subplots(1, 2, figsize=(10, 3.5), constrained_layout=True)
    for ax, (corner, edge, error), mode in zip(axes, (track, hold), ("track", "hold")):
        rows = table(stage / corner / "track/transient.dat", TRACE)
        target = req["input_high_V"] if edge == "up" else req["input_low_V"]
        begin, close = intervals[0 if edge == "up" else 1]
        off = close + EDGE_NS
        left = max(begin, close - min(20, .2 * req["track_ns"])) if mode == "track" else close - 1
        right = off if mode == "track" else off + req["hold_ns"]
        visible = [row for row in rows if left - .26 <= row[0]*1e9 <= right + .26]
        x = [row[0]*1e9 - close for row in visible]
        for vector, label, color in ((2, "Amplifier OUT", "#9a6126"), (3, "Capacitor SAMPLE", "#155d74")):
            ax.plot(x, [(row[vector]-target)*1000 for row in visible], label=label, linewidth=1.8, color=color)
        times = np.array([row[0]*1e9 for row in rows])
        sampled = np.array([row[3] for row in rows])
        probes = [close] if mode == "track" else [off, *times[(times > off) & (times < right)], right]
        errors = (np.interp(probes, times, sampled) - target) * 1000
        worst = int(np.argmax(np.abs(errors)))
        if not np.isclose(abs(errors[worst]), error, rtol=1e-9, atol=1e-7):
            raise ValueError("Plotted aperture or hold result differs from the waveform replay")
        ax.plot(probes[worst]-close, errors[worst], "o", color="#233840", markersize=4)
        ax.axhspan(-half, half, color="#3b8757", alpha=.14, label="Half-LSB band")
        ax.axvline(0, ls="--", lw=1, color="#33404a")
        ax.axvline(EDGE_NS, ls=":", lw=1, color="#33404a")
        ax.set(xlim=(left-close, right-close), xlabel="Time from switch opening (ns)",
               ylabel="Voltage relative to target (mV)",
               title="Worst {} / {} / {} / {:.3f} mV".format(mode, corner.upper(), edge, error))
        ax.grid(alpha=.2)
        ax.legend(loc="best", fontsize=8)
    figure.savefig(png, dpi=145)
    plt.close(figure)


def render_views(stage, images, executable):
    from native import TCL_PATH
    views = stage / "views"
    command = "xschem zoom_full; xschem print png $::env(DRIVER_IMAGE) 1400 900; exit"
    for kind, _, cell in CELLS:
        png = images / (stage.name + "-" + kind + ".png")
        env = dict(os.environ, MIRROR_SCHEMATIC_DIR=str(views), DRIVER_IMAGE=str(png))
        subprocess.run([executable, "--tcl", TCL_PATH + "; set draw_grid 0", "--command", command, str(views/cell)],
                       cwd=views, env=env, check=True, timeout=60)
        with png.open("rb") as image:
            header = image.read(24)
        if header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or struct.unpack(">II", header[16:24]) != (1400, 900):
            raise ValueError("Unexpected Xschem export for " + cell)


def variant_section(result, name, out, images, xschem):
    case = result["variants"][name]
    req = result["requirements"]
    size = case["sizing"]
    half = result["budget"]["half_lsb_mV"]
    track, hold, margin = limiting_measurements(case["measurements"])
    stage = out / name
    render_views(stage, images, xschem)
    sampling_figure(stage, req, half, track, hold, images/(name+"-sampling.png"))
    base = "evidence/" + name
    parts = ['<section id="{}"><h2>{} · {}/18 项达标</h2>'.format(name,design_title(name,size),result["pass_counts"][name])]
    if result["selected"] == name:
        parts.append('<p class="pass"><strong>选中此方案。</strong>其放大器与传输门的总栅面积在本次实测达标的方案中最小。</p>')
    elif name not in result["passing_variants"]:
        parts.append('<p class="fail"><strong>此方案超限。</strong>具体失败项可在逐项结果中核对。</p>')
    parts.append('<p>放大器 MOS 总栅面积 <strong>{:.2f} µm²</strong>，传输门另占 {:.2f} µm²。'
                 'PMOS 镜像在 VX 与 OUT 两侧分别使用 {} 和 {} 个 W/L 为 {:g}/{:g} µm 的单元。'
                 '<a href="{}/sizing.json">gm/ID 表行、全部 W/L 和并联数</a> · '
                 '<a href="{}/checked.json">逐项结果</a></p>'.format(
                     size["core_gate_area_um2"],size["switch_gate_area_um2"],size["devices"]["XLA"][2],
                     size["devices"]["XLB"][2],*size["devices"]["XLA"][:2],base,base))
    if "optimization" in result:
        row = result["optimization"][name]
        parts.append('<p>开关导通时，三工况静态扫描的最大误差为 {:.4f} mV，位于 {}、输入 {:g} V。'
                     '<a href="{}/{}/dc/dc.dat">九个输入电平的原始电压</a></p>'.format(
                         row["static_mV"],row["static_corner"].upper(),row["static_input_V"],base,row["static_corner"]))
    parts.append('<ul>')
    for label, (corner, edge, value) in (("关断前电容误差", track), ("保持窗口内最大电容误差", hold)):
        parts.append('<li>{}最大为 <strong>{:.4f} mV</strong>，{} 工况、{}步阶，限值 {:g} mV。'
                     '<a href="{}/{}/track/transient.dat">原始瞬态</a></li>'.format(
                         label, value, corner.upper(), EDGES[edge], half, base, corner))
    corner, mode, value = margin
    parts.append('<li>最低相位裕度为 <strong>{:.2f}°</strong>，{} 工况、{}状态，要求至少 {:g}°。'
                 '<a href="{}/{}/{}/ac.dat">环路原始数据</a></li></ul>'.format(
                     value, corner.upper(), PHASES[mode], req["min_phase_margin_deg"], base, corner, mode))
    parts.append('<figure><a href="images/{0}-sampling.png"><img src="images/{0}-sampling.png" alt="两种最差工况下放大器输出与采样电容相对目标的有符号误差"></a>'
                 '<figcaption>左图取跟踪误差最大的工况，右图取保持误差最大的工况。两者可以重合。横轴零点为开始关断，1 ns 时传输门完全关断。绿色区域为半个 LSB 的容差。</figcaption></figure>'.format(name))
    parts.append('<h3>可编辑电路与测试台</h3><div class="views">')
    for kind, title, cell in CELLS:
        parts.append('<figure><a href="images/{0}-{1}.png"><img src="images/{0}-{1}.png" width="1400" height="900" '
                     'alt="{2}的 Xschem 原生预览"></a><figcaption>{2} · <a href="{3}/views/{4}">源文件</a></figcaption></figure>'.format(name,kind,title,base,cell))
    parts.append('</div><p>在 Linux 运行 <code>sh {}/open.sh bench</code> 打开该父级。'
                 '将 <code>bench</code> 换为 <code>core</code> 或 <code>sampler</code> 可编辑对应子图。</p></section>'.format(base))
    return "\n".join(parts)


def search_review(result):
    records = result.get("optimization", {})
    if len(records) <= 2:
        return "", ["baseline","candidate"]
    show = result["selected"] or min(records, key=lambda n: (-records[n]["pass_count"],records[n]["static_mV"],records[n]["gate_um2"]))
    original = "baseline" if show == "baseline" or show.startswith("short-") else "candidate"
    parent = show.rsplit("-tg",1)[0]
    detail = list(dict.fromkeys((show,parent,original)))
    rows = []
    for name, row in records.items():
        size = result["variants"][name]["sizing"]
        track, hold, margin = limiting_measurements(result["variants"][name]["measurements"])
        rows.append('<tr><td><a href="evidence/{0}/views/ADRV_OTA.sch">{1}</a></td><td>{2:.3f}</td>'
                    '<td>{3:.3f}</td><td>{4:.3f}</td><td>{5:.2f}</td><td>{6:.3f}</td>'
                    '<td><a href="evidence/{0}/checked.json">{7}/18</a></td></tr>'.format(
                        name,design_title(name,size),row["static_mV"],track[2],hold[2],margin[2],row["gate_um2"],row["pass_count"]))
    grid = ('<details><summary>查看全部 {} 组尺寸、面积与最差工况测值</summary><p>两种沟道长度均保留等量拆分对照，'
            '分别增加和减少输出支路的并联数。每种长度中静态误差最小的一组再配两种开关尺寸。'
            '另在原长沟道放大器上单独加宽开关。</p><div class="scroll"><table>'
            '<tr><th>可编辑放大器</th><th>静态 mV</th><th>跟踪 mV</th><th>保持 mV</th><th>裕度 °</th>'
            '<th>放大器与开关 µm²</th><th>结果</th></tr>{}</table></div></details>').format(len(records),"".join(rows))
    return grid, detail


def write_review(result, out, project, revision, xschem):
    images = project / "images"
    images.mkdir()
    grid, names = search_review(result)
    sections = [variant_section(result, name, out, images, xschem) for name in names]
    req = result["requirements"]
    template = Template(Path(__file__).with_name("starter").joinpath("index.html").read_text(encoding="utf-8"))
    selected = result["selected"]
    conclusion = ("选中" + design_title(selected,result["variants"][selected]["sizing"]) + "。") if selected else "本次实测尺寸均有超限项。"
    page = template.substitute(
        purpose=escape(req["purpose"]), bits=req["adc_bits"], span=format(req["full_scale_span_V"],"g"),
        half=format(result["budget"]["half_lsb_mV"],"g"), load=format(req["sample_cap_pF"],"g"),
        track=format(req["track_ns"],"g"), hold=format(req["hold_ns"],"g"), decision=conclusion,
        sections=grid+"\n"+"\n".join(sections), revision=revision)
    (project/"index.html").write_text(page,encoding="utf-8")
