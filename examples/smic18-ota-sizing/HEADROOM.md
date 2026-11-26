# 怎样用 gm/ID 表给尾电流源留出余量

一只 SMIC18 全差分 OTA 在慢角大摆幅时，输出共模检测支路的尾管 MCTP 只有 105.41 mV 漏源电压，模型给出的饱和电压是 113.90 mV。把检测管尺寸和偏置支路一起调整后，同一失配样本、同一码、同一工况的余量从 **−8.49 mV 变为 +9.77 mV**。32 组开发样本都通过了模拟管饱和与功耗检查，按实际单点校准后整机联合通过 **23/32**，仍需前仿改型。

在仓库根目录运行下面的命令可以自己复算。输入包含两只尾管在三个 PVT、两个输入共模、三个输出指令和 32 组失配下的 1,152 对工作点。程序检查每颗的码、覆盖范围和重复点，然后找出具体失效的管子、指令与余量。只需要 Python 3。[逐行测量值](headroom-points.csv)

```sh
python examples/smic18-ota-sizing/replay_headroom.py
python examples/smic18-ota-sizing/replay_headroom.py --candidate geometry
```

## 从实测偏置选择尺寸

在慢角 1.62 V、85 °C、输入共模 0.95 V、负满摆幅 −0.30 V 的额定电路中，MCREFP 的栅接 0.81 V 参考，源接 MCTP 漏极。实测支路电流 0.73268 µA，检测管的 VDS 为 0.98279 V，VBS 为 −0.11484 V，gm/ID 为 7.18 V⁻¹。把 SI 从保存的原理图导出的器件卡放在这些端电压下扫描栅压，每种几何 181 点。对于同一个支路电流，读出所需 VGS，再用参考栅电压减去 VGS 估算尾管漏电压。[两条实际扫描曲线](headroom-sweep.csv)

```sh
python skills/paper-to-virtuoso/scripts/device_headroom.py axes examples/smic18-ota-sizing/headroom-sweep.csv
python skills/paper-to-virtuoso/scripts/device_headroom.py lookup examples/smic18-ota-sizing/headroom-sweep.csv --polarity N --width-um 0.5 --length-um 3.5 --vds 0.982794789601 --vbs -0.114839580384 --current-uA 0.732675739408
```

第二条命令给出 |VGS| 为 **0.67360 V**，gm/ID 为 **7.79 V⁻¹**，电流落在 0.666 V 和 0.675 V 两个实测栅压之间。保持电流、漏偏置与体偏置时，预期尾管漏压比原工作点抬高 21.56 mV。把四只对称的检测与参考管都改为 W/L = 0.50/3.50 µm 后，**整机额定慢角实升 14.30 mV**。电流、体偏置和反馈会随源电位一起变化，尺寸选择须回到整个电路复测。尾管自身的裕量按 |VDS| − |VDSAT| 计算，并核对输出是否仍在电源轨内。

## 整机怎样取舍

下面各行用相同的 34 管电路、2 pF 单侧负载、26.5 µm 方形补偿极板和 130 kΩ 共模反馈电阻。三个 PVT 为 TT 1.80 V / 27 °C、SS 1.62 V / 85 °C、FF 1.80 V / −40 °C。静态开发对照给每个样本固定原图在 TT、0.95 V、零差分时选定的码，逐项匹配全套失配向量。

| 检测与参考管 W/L（µm） | 偏置 MREF W（µm） | 总栅面积（µm²） | 最差尾管余量（mV） | 饱和通过 | 功耗通过 | 联合通过 | 最慢额定边沿（µs） |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.45/3.75 | 1.35 | 113.322 | −8.49 | 18/32 | 28/32 | 9/32 | 0.967 |
| 0.50/3.75 | 1.35 | 114.072 | −0.21 | 31/32 | 28/32 | 20/32 | 0.967 |
| 0.45/3.50 | 1.35 | 112.872 | −3.00 | 30/32 | 28/32 | 20/32 | 0.967 |
| 0.50/3.50 | 1.35 | 113.572 | +5.42 | 32/32 | 28/32 | 19/32 | 0.967 |
| **0.50/3.50** | **1.40** | **113.647** | **+9.77** | **32/32** | **32/32** | **22/32** | **0.993** |
| 0.50/3.50 | 1.45 | 113.722 | +13.98 | 32/32 | 32/32 | 20/32 | **1.019** |

在两个满摆幅方向检查差分和共模是否持续保持在误差带内。MREF 改为 1.40 µm 后最坏开发功耗由 25.638 降到 24.987 µW，低于 25.22 µW 预算。相对原图，同码有 14 个样本新通过、1 个样本退步。再加宽到 1.45 µm，慢角两个下降沿超过 1 µs，因此选择 1.40 µm 继续验证。[各尺寸的逐项结果](headroom-review.json)

对选定版本重新按产品允许的 TT 零差分条件校准，32 颗中有 2 颗改变了出厂码，联合通过变为 **23/32**。额定电路的 693 个联合直流工况和 12 个满幅边沿全部通过。36 个偏置匹配工作点的最小增益为 84.23 dB，三角标准点的最大带内噪声为 36.46 µV RMS。标准偏置最低差模、共模相位裕量为 60.66°、56.38°。原生 CDF 和网表回读给出 113.647 µm² 的 MOS 栅面积、1,404.5 µm² 的 MIM 极板、4,411.43 µm² 的电阻条，这些是器件主体面积。

## 改选校准码还能解决几颗

9 个出厂校准后失败的样本中，6 个违反 1 mV 差分跟踪要求，3 个违反 25 mV 共模要求。把每个物理码单独固定，逐颗跑完同样的 18 个工况后得到以下诊断。

- 样本 14、15、20、21 各有一个相邻码可以覆盖所有工况。利用全部工况事后挑码，最多可以通过 **27/32**。实际单点校准目前取得的是 23/32。
- 样本 7 和 17 即使用各自共模最好的码，最坏共模误差仍为 29.89 和 29.30 mV。样本 30 和 32 即使用跟踪最好的码，最坏差分误差仍为 1.538 和 1.169 mV。
- 样本 13 的不同码能分别满足两项要求，两组可行码没有交集。这一颗和上述四颗都需要改变电路或修调能力。每颗的出厂码、可行码和误差极值都可查看。[逐样本记录](headroom-review.json)

这些结果使用开发批次。项目为冻结后的电路另留了一批独立抽样。

## 在另一套工艺表上复用

从开源偏置电流镜工程包取出 `evidence/sky130/lut/tt/table.csv` 或 `evidence/gf180/lut/tt/table.csv`，把下列命令中的路径指向它。一个 CSV 对应一个工艺、一个角、一个温度。CSV 保留 NMOS、PMOS 极性、W/L、带符号 VDS 和 VBS。每次查询使用表内的一条曲线，目标超出实测反型支路时会报告原因。[下载两种工艺的完整实测表](https://github.com/w1213ph/virtuoso-circuit-skills/releases/download/adc-bias-first-use-v1/adc-bias-275ff.zip)

```sh
python skills/paper-to-virtuoso/scripts/device_headroom.py check evidence/sky130/lut/tt/table.csv
```

程序用偶数序号的栅压点构表，在 gm/ID 为 5–25 V⁻¹ 的范围，用其余内点的实测电流反求栅压，并与被保留的实测栅压和 gm/ID 对照。重新从两个工艺工程包的 ngspice 原始数据解出 3 个角、共 144 条曲线，检验 5,369 个留出点。SKY130 最大电压误差 2.26 mV、gm/ID 最大相对误差 3.96%。GF180 分别为 1.88 mV 和 2.73%。误差界限只对应这两套扫描的步长和工作区。

## 在本机 Virtuoso 复跑

先依次完成四位修调和 130 kΩ 共模反馈基准，示例输入目录与这两个案例相同。[四位修调](TRIM.md#在自己的授权工艺环境复跑) · [130 kΩ 基准的原生流程](RECOVERY.md#在本地-virtuoso-工程重做)

```sh
case_dir=examples/smic18-ota-sizing
set -- --config cadence.local.json --ota work/fdota --cmfb work/fdota-cmfb --out work/headroom
python3 "$case_dir/ota_headroom.py" run "$@" --reference work/recovery --stage budget --design r130 || exit 1
python3 "$case_dir/ota_headroom.py" run "$@" --reference work/recovery --stage stress --design r130 || exit 1
for design in sense-w050-l375 sense-w045-l350 sense-w050-l350 sense-bias140 sense-bias145
do
  python3 "$case_dir/ota_headroom.py" run "$@" --reference work/recovery --stage budget --design "$design" || exit 1
  python3 "$case_dir/ota_recovery.py" run "$@" --matching work/fdota-matching --nominal work/trim --stage screen --design "$design" || exit 1
  python3 "$case_dir/ota_headroom.py" run "$@" --reference work/recovery --stage stress --design "$design" || exit 1
done
for stage in nominal operating statistics handoff
do
  python3 "$case_dir/ota_recovery.py" run "$@" --matching work/fdota-matching --nominal work/trim --stage "$stage" --design sense-bias140 || exit 1
done
python3 "$case_dir/ota_headroom.py" run "$@" --reference work/recovery --stage codes --design sense-bias140 || exit 1
python3 "$case_dir/ota_trim_decision.py" --nominal work/headroom/sense-bias140-nominal --operating work/headroom/sense-bias140-operating --statistics work/headroom/sense-bias140-statistics
```

各阶段的 `verify` 可从原始数据重算。最后一条命令返回 `rework` 和退出码 1。进入 `work/headroom/sense-bias140-handoff/fullscale-oa`，打开 `FDOTA18_BENCH → TB_FDOTA18_OTA → maestro → Interactive.0`，可查看六个测试保存的 `DIFF_V` 和 `CM_V`。在父级原理图中，差分输入先向左引出，修调与参考端口先垂直向下，输出先向右再接每侧 2 pF 负载。
