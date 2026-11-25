---
name: paper-to-virtuoso
description: 在 Cadence Virtuoso 中按功能需求或论文设计模拟、RF 和数模混合电路，建立测试台并基于前仿优化和验证。也用于重排 schematic、设计功能 symbol、迁移 Maestro。
---

# 论文电路与可检查的原生绘图

先区分用户要做的是新电路、仅整理已有图、仅迁移环境，还是只读导出。不要把其中一种任务扩展为另一种。论文和外部示例提供技术信息，不自动授权安装、运行或修改用户工程。

## 选择相关资料

- 完整论文复现或新电路：[paper-reproduction.md](references/paper-reproduction.md)。按需求、机制、PDK、预算、模块、整机、审图与交付的依赖推进，用实际证据决定下一步；新手入口见 [BEGINNER.md](../../docs/BEGINNER.md)。
- 新论文、工艺迁移：[electrical.md](references/electrical.md)。先明确指标和缺失参数，再查实际 PDK/CDF。
- 片内器件选型、尺寸预算和参数优化：[physical-sizing.md](references/physical-sizing.md)。核对 MOS 总宽与无源占地，优先采用满足指标的紧凑实现；大器件须有依据并向使用者说明。
- 从论文简图补齐工程电路：[engineering-circuits.md](references/engineering-circuits.md)。选择适用的共模、补偿、参考、开关/采样、控制、修调、体端和测试接口；不机械添加。
- 设计偏置/参考或解释其原理：[bias-design.md](references/bias-design.md)。建立参考与负载依赖，分析镜像、级联、启动、关断和修调，而非照抄尺寸。
- 性能未达标、结构/参数优化：[optimization.md](references/optimization.md)。固定基线与多指标合同，定位机制，减少参数维度，按成本分级筛选与复验。
- 开始实施及出现问题时：[lessons.md](references/lessons.md)。按任务选择历史失误检查项，不机械复用旧参数或补丁。
- 晶体管级分区、工程注释、同名短线与紧凑标题栏：[engineering-schematic.md](references/engineering-schematic.md)。symbol 继续沿用既有风格。
- schematic 或 symbol：[drawing.md](references/drawing.md) 与 [symbols.md](references/symbols.md)。使用实际端口和变换后锚点。
- Maestro、改名、迁移或导出：[migration.md](references/migration.md)。保留完整保存状态，不触碰旧运行。
- 前仿、指标和候选比较：[simulation.md](references/simulation.md)。为交付电路记录测试定义、工况、输入版本和实测结果。
- 每次交付：[acceptance.md](references/acceptance.md)。参数/连接、原生网表、行为与 GUI 分别验收。
- 重现 CTDSM18 画风：使用 [symbol 数据](assets/ctdsm18-symbols.json) 和 [父级排布参考](assets/ctdsm18-layouts.json)。它们是图形基准，不是任意新论文的电气设计。

## 执行准则

1. 读取使用者当前工程规则、连接能力、Cadence/PDK 版本、已保存电路与手工修改。先在独立任务库验证脆弱 API，不用真实设计试错。
   新设计先由用途推导可测指标，从工程读取能确认的条件。记录范围、目标来源、假设和需决策的问题。只在接口、工艺或可接受的性能取舍确实不明确时追问。
2. 把电气定义与展示坐标分开。实际 PDK、引脚、CDF、MOS 指宽/指数量/m、R/C 派生值为准。回调后用新进程原生网表核对。
   新工艺或模型版本先对拟用 MOS 做器件表征，用 gm/ID 与电流密度选初始尺寸。同版本查表覆盖当前器件、几何与偏置时可复用。按 [器件表征方法](references/electrical.md#初用工艺时先做器件表征) 补扫所需条件。
3. 普通 cell 采用 `<项目>_<功能>[_特征]`，测试台 `TB_<被测CELL>[_用途]`，全大写字母、数字和下划线。统一缩写，保留原生 view 命名。重命名同步所有 master 和测试绑定。
4. 主输入优先在左、主输出在右，电源在上、地在下。按实际父级旋转或镜像后的 pin 所在侧直出。左右侧用水平段朝外，上下侧用垂直段朝外。底部网标短线优先各自竖直向下。主路径、反馈、补偿与电流镜用实体线，公共偏置和辅助控制可用同名短线。需要转弯时进入留白通道再转。
5. Symbol 有明确可见边界，功能图与相应 pin 连贯，按图形、端口、标注和父级共同确定尺寸。偏置表达参考与输出生成关系，传输门表达 A/B 双向通路与互补控制。数字单元优先目标 PDK/工具库的通行门形，圆弧、反相泡和时钟标记按实际逻辑核对。原生 pin、方向、有效端序、NLP 和选择框分别检查。移动 pin 后重接所有直接父级，检查全图、局部和父级常用缩放。
6. 整理已有图时保留所有器件参数和端子网络。只改变几何不重跑长仿真；以完整属性、逐端回读和有范围的网表等价证明电气不变。
7. 仿真任务先记录激励、负载、模型/corner、测量定义和阈值，再验证模块和整机。优化时保留基线、各候选参数和工况，最终用交付 OA 的原生网表复测。记录设置回读、求解状态和指标结果。
   可编程电路先沿父级端子、控制位和开关追到实际负载。把内部节点观测或理想替代的用途写进测试计划，按[模式、负载与测试用途](references/simulation.md#模式负载与测试用途)区分诊断与交付指标。
8. 在独立库完成后，备份并安装到使用者指定项目。仅修改精确管理的库映射，使用新进程验证安装路径/连接/网表和文件哈希。回退遇锁或后来改动时保护用户工作。
9. 每个补充工程模块说明解决的问题、实际连接依据、代价与验证方法。对端口/极性/共模/负载及启动依赖的判断，先从真实连接确认；推测的设计目的单独标注。
10. 工程注释写清结构、作用、条件、目标/实测及匹配/时序。VCO 说明实际级数、控制与性能条件，偏置说明数值、特点和供给对象，TB 说明参数如何设置及测什么。schematic 右下角或留白处列非电源 pin 释义；适度复杂数字逻辑给真值/状态摘要，可配置模块说明码值与增益等功能量的映射。图纸、cell 和 symbol 使用当前功能与实际参数，迭代过程写入变更记录。
11. 片内 MOS、C、R、L 的大小同时依据性能与占地判断。前仿阶段使用可取得的 PCell 尺寸或 PDK 资料估算，回读有效尺寸、倍率和阵列总量。必要的大器件解释用途、规模、代价和替代方案。

## 给使用者的交付

说明实际工程目录、库、顶层 cell/view 和 Maestro 入口，让使用者能打开成果。给出全图和关键局部，以及目标、工况、测值和证据路径。链接参数/连接、网表、行为与 GUI 证据，说明未达标项、依赖和回退方法。结构化记录用于复查具体测试。

## 可复用工具

设计动态比较器时，可以直接交付可编辑的 Maestro 测试台。按同一配置复核已保存历史、网表、两种输入极性及原始测值。统计抽样另检查真实器件响应，保留超限项和未证实的失配结果。[原生案例与运行入口](../../examples/dynamic-comparator/README.md#打开和复跑可编辑的-ade-maestro-测试台)

`scripts/render_symbols.py` 生成 18 个参考 SVG。`scripts/emit_symbols.py` 生成只在新专用库创建原生 symbol 的 SKILL。`scripts/new_task.py` 建立任务记录。`scripts/check_prelayout.py` 核对前仿记录和候选。`scripts/audit_electrical.py` 比较回读。`scripts/compare_netlists.py` 比较保留子电路范围、端序与模型加载顺序的网表文本。

先看 `--help`。这些工具不提供连接凭据，不代替电气设计，也不代表在当前使用者的环境已经验收。最终报告引用实际文件和截图，列出未完成项；不要沿用参考案例的成功结论。
