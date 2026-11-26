"""Ship the measured designs, a local replay entry point and native schematic previews."""
import argparse
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from switch_models import HERE
from switch_views import ADC, TCL_PATH
from switch_measure import harmonics
from switch import main as replay


LAUNCHER='''"""Recalculate the delivered project, or run a new specification with Xschem and ngspice."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/"source"/"sky130-bootstrap-switch"))
from switch import main
if __name__=="__main__":
    sys.exit(main(sys.argv[1:] or ["verify","--out",str(root/"evidence")]))
'''


def previews(folder,viewdir,tool):
    for name,cell in (("core","BSW_SWITCH.sch"),("bench","TB_BSW_SAMPLE.sch"),("symbol","BSW_SWITCH.sym")):
        file=folder/(name+".png")
        env=dict(os.environ,MIRROR_SCHEMATIC_DIR=str(viewdir),SWITCH_PNG=str(file))
        cmd="xschem zoom_full; xschem print png $::env(SWITCH_PNG) 1600 1050; exit"
        subprocess.run([tool,"--tcl",TCL_PATH+"; set draw_grid 0","--command",cmd,str(viewdir/cell)],cwd=str(viewdir),env=env,check=True,timeout=60)
        with file.open("rb") as image:header=image.read(24)
        if header[:16]!=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or struct.unpack(">II",header[16:24])!=(1600,1050):raise ValueError("Invalid native image "+name)


def spectrum(folder,evidence,chosen,summary):
    import csv
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    case=summary["refinement"][chosen]["case"]
    fig,ax=plt.subplots(figsize=(7.8,3.5),constrained_layout=True)
    for name,color,offset in (("transmission","#9d6236",-.18),(chosen,"#236779",.18)):
        with (evidence/"designs"/name/case/"samples.csv").open() as f:samples=[float(row["held_V"]) for row in csv.DictReader(f)]
        bins,thd=harmonics(samples)
        ax.bar([n+offset for n in range(2,6)],[20*math.log10(max(x/bins[0],1e-15))+130 for x in bins[1:]],
               bottom=-130,width=.34,color=color,label="{} ({:.2f} dBc THD)".format(name,thd))
    ax.set(xticks=[2,3,4,5],xlabel="Harmonic of the sampled tone",ylabel="Level relative to fundamental (dBc)",
           title="Held samples / "+case,ylim=(-130,0))
    ax.grid(axis="y",alpha=.23);ax.legend(fontsize=9);fig.savefig(str(folder/"harmonics.png"),dpi=150);plt.close(fig)


def page(root,s,revision):
    name=s["diagnostic"];v=s["variants"][name];fine=s["refinement"][name];limit=s["requirements"]["max_thd_dBc"]
    title="所选自举电路" if s["selected"] else "最接近要求的诊断电路"
    rows="".join("<tr><td>{}</td><td>{}/{}</td><td>{:.2f}</td><td>{:.2f}</td><td>{:g}</td></tr>".format(
        escape(n),a["passed_cases"],a["cases_total"],a["worst_thd_dBc"],a["gate_um2"],a["plate_um2"]) for n,a in s["variants"].items())
    detail_rows=[]
    for k,m in v["cases"].items():
        last=fine["cases"][k][-1];factor=round(.25/last["step_ns"]);path=f"evidence/refinement/{name}/{k}/divide-{factor}"
        detail_rows.append('<li><a href="{}/metrics.json">{} · {:g} Vpp · {:.2f} dBc</a> · <a href="{}/samples.csv">128 个保持样本</a></li>'.format(
            path,m["corner"].upper(),m["input_vpp_V"],last["thd_dBc"],path))
    details="".join(detail_rows)
    dc=v["max_dc_vbs_excess"];spec=s["requirements"]
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SKY130 自举采样开关前仿</title><style>body{{font:17px/1.65 system-ui,sans-serif;max-width:960px;margin:auto;padding:1.4rem;color:#182d34}}h1,h2{{line-height:1.25}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #b8c5c8;padding:.4rem;text-align:left}}img{{display:block;max-width:100%;height:auto}}figure{{margin:1.5rem 0}}code{{background:#eff4f4;padding:.1rem .3rem;overflow-wrap:anywhere}}a{{color:#075875}}.table{{overflow:auto}}</style>
<h1>SKY130 自举采样开关</h1><p>{escape(spec["purpose"])}</p><p><strong>{title}为 {name}。</strong> 在 {v["cases_total"]} 个工况中通过细步长复测 {fine["passed_cases"]} 个，复测最差 H2–H5 总失真 {fine["thd_dBc"]:.2f} dBc，目标 {limit:g} dBc。</p>
<p>在解压目录运行 <code>python sample.py</code>，可从器件扫描和所有原始波形复算。用 Xschem 打开 <a href="evidence/designs/{name}/views/TB_BSW_SAMPLE.sch">测试台</a> 或 <a href="evidence/designs/{name}/views/BSW_SWITCH.sch">开关原理图</a>。Linux 用户可在 <code>evidence/designs/{name}</code> 下运行 <code>sh open.sh bench</code>。<a href="evidence/RESULTS.md">逐项结果</a></p>
<h2>开关、测试台和采样频谱</h2>
<figure><a href="images/core.png"><img src="images/core.png" alt="入选或诊断电路的原生晶体管级原理图"></a><figcaption>原生原理图，点击查看器件和阱连接。</figcaption></figure>
<figure><a href="images/bench.png"><img src="images/bench.png" alt="带信号源、时钟和保持电容的原生测试台"></a><figcaption>IN 从左侧进入，SAMPLE 向右连接保持电容。<a href="images/symbol.png">查看开关符号</a></figcaption></figure>
<figure><img src="images/harmonics.png" alt="普通传输门与自举电路的保持样本谐波"><figcaption>相同激励、负载和 0.25 ns 初筛步长下的二至五次谐波。下方可下载各工况细步长复测的样本。</figcaption></figure>
<h2>面积与前仿检查</h2><div class="table"><table><tr><th>电路</th><th>初筛通过工况</th><th>初筛最差 THD (dBc)</th><th>总栅面积 (µm²)</th><th>MIM 极板 (µm²)</th></tr>{rows}</table></div>
<p>每个工况检查失真、增益和全程端压，自举电路另查预充、跟踪和关断。面积按器件几何分别统计。按实际接线核对公开模型的有符号 VGS、VDS、VBS，{v["published_model"]["cases_outside"]} 个工况出现越界。工程瞬态正向结压预算 {spec["max_forward_V"]:g} V。{name} 的 {dc["device"]} 在 {dc["case"]} 测得正向 VBS {dc["forward_peak_V"]:.4f} V，公开模型的正向边界为 {dc["published_forward_V"]:g} V。逐管详情在各工况的 metrics.json，超出模型范围的瞬态须另做工艺可靠性确认。</p>
<ul>{details}</ul><p>源码版本 <code>{escape(revision)}</code>。<a href="my-switch.json">修改输入规格</a>，再运行 <code>python sample.py run --requirements my-switch.json --out my-run</code>。</p></html>'''


def pack(measured,project,revision,tool):
    if not re.fullmatch(r"[0-9a-f]{40}",revision):raise ValueError("Expected the source commit")
    s=json.loads((measured/"summary.json").read_text(encoding="utf-8"))
    if replay(["verify","--out",str(measured)])!=(0 if s["selected"] else 1):raise ValueError("Raw evidence failed replay")
    project.mkdir(parents=True);evidence=project/"evidence"
    shutil.copytree(str(measured),str(evidence),ignore=shutil.ignore_patterns("models","xschem.log"))
    for src,files in ((HERE,[p.name for p in HERE.iterdir() if p.suffix in (".py",".sym",".json") or p.name=="README.md"]),
                      (ADC,["ADRV_NMOS.sym","ADRV_PMOS.sym","ADRV_SOURCE.sym"]),
                      (HERE.parent/"sky130-current-mirror",["mos_table.py","mos_model.py","measure.py","native.py","fetch_models.py","models.lock.json"])):
        dest=project/"source"/src.name;dest.mkdir(parents=True)
        for name in files:shutil.copyfile(str(src/name),str(dest/name))
    shutil.copytree(str(HERE/"images"),str(project/"source"/HERE.name/"images"))
    for name in ("LICENSE","NOTICE"):shutil.copyfile(str(HERE.parents[1]/name),str(project/name))
    (project/"sample.py").write_text(LAUNCHER,encoding="utf-8")
    shutil.copyfile(str(evidence/"requirements.json"),str(project/"my-switch.json"))
    images=project/"images";images.mkdir()
    previews(images,evidence/"designs"/s["diagnostic"]/"views",tool);spectrum(images,evidence,s["diagnostic"],s)
    (project/"index.html").write_text(page(project,s,revision),encoding="utf-8")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run",type=Path,required=True);p.add_argument("--project",type=Path,required=True)
    p.add_argument("--revision",required=True);p.add_argument("--xschem",default="xschem")
    a=p.parse_args();pack(a.run.resolve(),a.project.resolve(),a.revision,a.xschem)
