# 外部来源与实际采用的方法

## 2026-11-27 的统计器件验证

SkyWater SKY130 的器件库在顶层角段引用各 MOS 的 mismatch 文件，单管文件给出局部参数。核对的提交为 `f62031a`，许可证为 Apache-2.0。本项目据此在 SMIC18 自身的模型段和统计器件上先验证无扰动电气等价，再逐管检查抽样向量，最后用同一组抽样对照全部启用和局部关闭时的电路响应。本文 OTA 数值来自 SMIC18 Spectre，开源工艺提供了检查模型组织方式的实例。[顶层角段](https://github.com/google/skywater-pdk-libs-sky130_fd_pr/blob/f62031a1be9aefe902d6d54cddd6f59b57627436/models/sky130.lib.spice) · [单管失配文件](https://github.com/google/skywater-pdk-libs-sky130_fd_pr/blob/f62031a1be9aefe902d6d54cddd6f59b57627436/cells/nfet_01v8/sky130_fd_pr__nfet_01v8__mismatch.corner.spice) · [许可证](https://github.com/google/skywater-pdk-libs-sky130_fd_pr/blob/f62031a1be9aefe902d6d54cddd6f59b57627436/LICENSE) · [本项目验证代码](../examples/smic18-ota-sizing/ota_mismatch.py)

## 2026-11-26 的复核与转化

JKU 的全差分 OTA 测试台给同一设计设置独立差模与共模激励，并分别记录输出。课程还解释了用输出平均电压作检测量和分别检查环路稳定性的原因。上游版本固定为 `21b5255`，课程和测试台采用 Apache-2.0。本项目据此设计了保留实际偏置、负载、误差放大器和执行管的对照实验，并在 SMIC18 上独立实现以原始电流和两端输出诊断误差的程序。[课程原文](https://github.com/iic-jku/analog-circuit-design/blob/21b52550a9ff256ee272585f384590fbd386a221/content/ota_diff/_sec_differential_ota.qmd#common-mode-regulation) · [Xschem 测试台](https://github.com/iic-jku/analog-circuit-design/blob/21b52550a9ff256ee272585f384590fbd386a221/xschem/ota-diff_tb-acol.sch) · [许可证](https://github.com/iic-jku/analog-circuit-design/blob/21b52550a9ff256ee272585f384590fbd386a221/LICENSE) · [本项目的实现](../examples/smic18-ota-sizing/ota_cmfb.py)

本轮通过 GitHub API 枚举 Arcadia-1 的 33 个公开仓库和 131 个公开 Star，并查阅 iic-jku、idea-fasoc、bluecheetah、cascode-ai、Nova-Silicon 的公开目录。针对 17 个候选核对实际仓库、版本、许可证元数据及可见 issue/PR，9 个在索引里有具体代码位置和筛选评分。累计索引有 296 个唯一仓库。下面的 288 个是上一轮的历史清单。[可搜索的逐项记录](sources.jsonl)

| 实际阅读的实现 | 纳入本项目的决定与核验 |
|---|---|
| [iic-jku 的五管 OTA 噪声测试台](https://github.com/iic-jku/analog-circuit-design/blob/21b52550a9ff256ee272585f384590fbd386a221/cace/templates/ota-5t-noise.sch) | 原例明确了输出、输入源和 1 kHz 至 100 MHz 频带，读取输出积分。我们将测量参考写进合同，并在全差分 OTA 上逐点核对输入折算和输出功率。原例的数值、带宽和器件尺寸不用作本项目的验收目标。 |
| [CACE 的 ngspice 参数执行器](https://github.com/fossi-foundation/cace/blob/dedb5e0d30f5c286d36a1511b61fc81795bd24cf/cace/parameter/parameter_ngspice.py) | 学习把参数、仿真作业和后处理分开。合同固定测试条件和限值，独立复算器读取保存的证据；两个 RC 电路用 ngspice 和解析式校准。 |
| [BAG 的 MeasurementManager](https://github.com/bluecheetah/bag/blob/a496368ad8fc92ff0f5bf3bfb4ebaa45c540a104/src/bag/simulation/measure.py) | 借鉴让下一步测量取决于已得结果。原生运行、只读复验和更换噪声预算分别有入口。本站保留现有 Virtuoso / SI 使用方式。 |
| [analog-canvas 的几何投影](https://github.com/cascode-ai/analog-canvas/blob/5fbe58523a3b047d0c41a91a066296fdc393df89/packages/edit-engine/src/routing-geometry-projection.ts) | 确认存在可调用的导线几何操作，记录 AGPL-3.0。跨编辑器的 OA 连通性、旋转后引脚方向和父级图仍需实测。本轮仅阅读。 |
| [analog-agent-flow 的 Virtuoso 适配器](https://github.com/w1213ph/analog-agent-flow/blob/a2fa4268033804f52dd49a9b5d5f2ff451c9586c/src/analog_agent_flow/adapters/virtuoso_bridge.py) | 核对到示例网表入口含占位代码。此项目的网表证据来自自己实际运行的 SI、逐端子比对和 Spectre。 |

筛选按理论、实现、复现、可移植、自动化、维护、文档、许可分别占 20、20、20、15、10、5、5、5。各项以 0、1、2 表示缺证、局部证据、在所审范围内有直接证据，再按权重折算。iic-jku 82.5、CACE 80，均优先学习方法。上游本身的完整测试本轮未重跑，项目内的数值校准独立执行。BAG 的许可证元数据为 NOASSERTION，AutoCkt 未声明标准许可，本轮采用自主实现。ngspice 的默认幅度谱、平方谱开关和积分总量定义见其官方手册。[ngspice 手册，第 11.3 节](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)

## 2026-11-25 的初次清单

按 2026-11-25 的 GitHub 公开数据合并去重，共有 **288** 个仓库。Arcadia-1 所有者仓库 33 个（1 页），Star 130 个（2 页），iic-jku 仓库 94 个（2 页）。每页 75 条，逐页检查数量和重复项。账户公开展示的组织成员关系为 0。另解析 37 个 Fork 的根上游，加入指定参考项目与导航目录条目。[完整逐库索引](sources.jsonl)

每行记录仓库、版本、许可证、说明、来源集合与分级。Fork 另列直接父级、根仓库和上游提交。源码路径相对该行提交。近期 issue 与 PR 提供调查线索，固定提交对应实际阅读的版本。

S 是本轮核心方法，A 为已找到具体适用点的来源，B 为限定用途参考，C 为本轮仅查目录及元数据，EVAL 为评测保留。对元数据行，运行、案例质量、专业正确性、测试与可复用性均待验证。本轮没有执行上游程序。数据、PDK 和二进制授权须分别核对，未声明许可的来源仅记录独立观察。

| 级别与项目 | 阅读所得与用途 | 适用边界 |
|---|---|---|
| B [Arcadia-1/awesome-ams-skills](https://github.com/Arcadia-1/awesome-ams-skills/blob/69f12ed08c780f4b5cb8d8d1cb0a6fcd9748579f/README.md) | 从目录追到具体实现与改名记录。作为导航。 | 索引自身没有电路测试结果。 |
| S [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite/blob/672b451416b1ef3cc560db4196365075e94c8d4e/examples/01_virtuoso/maestro/06b_rc_simulate_and_read.py) | RC 示例将保存视图、发起作业、读取历史和导出波形分开，历史测试覆盖回读不一致。采用独立进程重开和历史波形验收。 | 本例自行实现 IC6.1.8 本机流程，IC23.1 结果目录需另验。 |
| A [Arcadia-1/analog-agents](https://github.com/Arcadia-1/analog-agents/blob/4d6bd218ceff60624d7ee62887787aa83daecdb4/shared-references/review-protocol.md) | 审查协议要求规格、网表、依据与逐项结论，桥接代码组织外部审查输入。采用确定性复核，保留两种极性及失败工况。 | 模型意见本身不能证明实际仿真，本工程无需远程模型服务。 |
| A [Arcadia-1/analog-circuit-skills](https://github.com/Arcadia-1/analog-circuit-skills/blob/cf1066edb0dc8a7eb78914ecf29b081261e91421/comparator/scripts/simulate_sweep_input_amplitude.py) | 比较器输入幅度扫描共用 DUT，LDO 另有负载瞬态测试。采用同一测试台核对差分输入和小信号工况。 | 各电路预算须重新推导，未搬用模型或网表。 |
| A [Arcadia-1/gmoverid-skill](https://github.com/Arcadia-1/gmoverid-skill/blob/30ab431485ed9045d9df8a12bda545e163b6b1d8/gmoverid/assets/simulate_gmoverid.py) | 生成器和弱反型物理检查分开。参考合理性检查，与本地工艺表结果对照。 | 预测模型数据不能给 SMIC18 定尺寸。 |
| A [Arcadia-1/ams-skills](https://github.com/Arcadia-1/ams-skills/blob/ed731b8d5acb28f923c8b7449cbda7c3d9cd2998/skills/opamp-ota/references/fully-diff-and-cmfb.md) | 全差分 OTA 资料区分共模和差模反馈与相应测试台。下一例评估实体偏置、CMFB 和两种环路的稳定性。 | 本项目尚未复建该 OTA。 |
| A [Arcadia-1/ADCToolbox](https://github.com/Arcadia-1/ADCToolbox/blob/9340460d483b76f420338a9fc1bad72f60329437/python/src/adctoolbox/spectrum/analyze_spectrum.py) | 频谱函数有窗、OSR 与频点设置，并有集成比对。预留给 ADC 动态指标与定义回归。 | 比较器判决需另测原始波形。 |
| B [Arcadia-1/circuits-and-systems-classroom](https://github.com/Arcadia-1/circuits-and-systems-classroom/blob/53f403a72cf7d0908721045f342641aa770e138c/code/degeneration_resistor_sizing.py) | 源退化电阻计算和电容 AC 提取可作解释性参考。待交叉核对理论预算。 | 教材工作点需对照实际模型。 |
| B [Arcadia-1/veriloga-skills](https://github.com/Arcadia-1/veriloga-skills/blob/7c5d3f03a162ee8131103e9551eee842424360bb/evas-sim/examples/measurement/gain_extraction/analyze_gain_extraction.py) | 增益提取脚本显式使用激励种子，行为模块单独建模。留作系统级诊断。 | 行为增益与晶体管级性能分别报告。 |
| B [Arcadia-1/EVAS](https://github.com/Arcadia-1/EVAS/blob/6cb6fa7a7dac70fc0d4120126d8cf74258e6637b/evas/netlist/spectre_parser.py) | 输入解析器与引擎用例分离，测试含后端版本检查。保留作行为级快速验证参考。 | 未与本项目 PDK 数据对齐。 |
| A [JPWang-OvO/analog-artist-skills](https://github.com/JPWang-OvO/analog-artist-skills/blob/1e7cbfcf57382647efc0eb00296dcccd30be4213/virtuoso-drawing/references/native-geometry.md) | 引线格点、接合点和功能外形需在父级实际几何中检查。延用父级逐端子向外引线回读。 | 字体和几何尺寸服从实际 PDK 与本项目 roman 约定。 |
| A [iic-jku/analog-circuit-design](https://github.com/iic-jku/analog-circuit-design/blob/21b52550a9ff256ee272585f384590fbd386a221/cace/scripts/ota-5t-ac.py) | 五管 OTA 有独立 AC、噪声原理图和结果后处理，AC 脚本会裁掉指定范围外的样本。学习拆分 TB，保留本案例的全部失配样本与分母。 | 统计验收须解释每一个排除项，示例截断范围不适用于本项目。 |
| A [iic-jku/sky130_adc_202311](https://github.com/iic-jku/sky130_adc_202311/blob/6b4806a79a7c53e34bd45f71cd1d0f601c4eb681/xschem/adc_top_tb.sch) | 顶层与桥接接口分别设台，供电和差分激励写入 TB。作为混合信号接口测试参考。 | 此轮未运行该芯片或核验硅测指标。 |
| S [MacAnalog/SpiceXplorer](https://github.com/MacAnalog/SpiceXplorer/blob/58a6248070e5d37bc53f284e774c7f89f055b60e/src/spicexplorer/optimization/orchestrator.py) | 优化器编排独立于算法，回归覆盖日志隔离和参数名唯一性。沿用同工况隔离保存候选并先核硬指标。 | 当前两几何比较没有验证通用搜索器性能。 |
| A [SkillSurf/os-ina-pso-smacd_2026](https://github.com/SkillSurf/os-ina-pso-smacd_2026/blob/3fa03c06c9364d66a3ef970bc5d78547d71fb412/Complete%20Framework/FDDA_CMFB/gmID_sizing.py) | FDDA_CMFB 将 gm/ID 取值、粒子搜索和最终验证分开。为全差分放大器设计有界候选。 | 论文任务的目标权重不能通用于其他电路。 |
| A [medwatt/gmid](https://github.com/medwatt/gmid/blob/6c4c5e164295c60a8b996f9dff8a79c64456ddaf/src/mosplot/lookup_table_generator/lookup_table_generator.py) | 表生成器先定义扫描轴再构表，查询边界影响可用域。延用按工艺、角、偏置建立有效查表范围。 | 表的授权和实际偏置覆盖须逐个确认。 |
| A [efabless/cace](https://github.com/efabless/cace/blob/d71c8da5f8d8a5bfb4cfb2640714bdd374a9b84a/cace/parameter/parameter_ngspice.py) | ngspice 后端调度任务，数据表声明条件与指标。将条件矩阵与测量分开。 | 与另一个 CACE 维护仓分别锁版本。 |
| S [fossi-foundation/cace](https://github.com/fossi-foundation/cace/blob/dedb5e0d30f5c286d36a1511b61fc81795bd24cf/cace/parameter/parameter_ngspice.py) | 数据表和后端分别约束条件、执行和结果。采用独立原始结果复核和预声明预算。 | 本轮未引入 CACE 依赖，未执行上游示例。 |
| B [CODA-Team/AnalogGym](https://github.com/CODA-Team/AnalogGym/blob/0a9d1390ade361e2b4a2d33181e22367edbb8afc/AnalogGym/Amplifier/perf_extraction_amp.py) | 放大器建立时间与 LDO AC/DC 各有提取器，上游问题记录默认工作点风险。按拓扑选择测试方法。 | 默认参数和性能需在目标 PDK 重验。 |
| B [jialinlu/opensource-analog-circuits](https://github.com/jialinlu/opensource-analog-circuits/blob/e8308aa273c1a6737a5dee89178c4d48270ff87e/benchmark/ngspice_benchmark.py) | 仿真封装从配置选电路并解析日志，LDO 有独立统计测试网表。后续多电路搜索接口比较。 | 未执行上游二进制，也未复用随库模型。 |

已有 SKY130 案例的器件模型作为实际工程依赖单列，索引采用项目实际仿真的版本。[锁定文件、哈希与许可证](../examples/sky130-current-mirror/models.lock.json)

`sar-adc-skills` 现指向 `ams-skills`，`Analog-Circuit-Knowledge-Base` 现指向 `circuits-and-systems-classroom`。索引保留两个旧入口。`razavi-bench` 与 `analog-design-bench` 只取仓库元数据。

更新时用 GitHub GraphQL 的 `user.repositories(privacy:PUBLIC, ownerAffiliations:[OWNER])`、`user.starredRepositories` 与 `organization.repositories(privacy:PUBLIC)` 遍历 `pageInfo.endCursor` 至 `hasNextPage=false`。用 REST 仓库的 `parent` 与 `source` 区分直接父级和根仓库，再取默认分支提交和授权声明。对要采用的方法，检查固定版本的源文件、回归或实例及近期问题，在本项目的独立工况重验。
