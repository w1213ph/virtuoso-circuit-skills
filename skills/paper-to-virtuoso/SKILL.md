---
name: paper-to-virtuoso
description: 按论文在 Cadence Virtuoso 中搭建模拟或 RF 电路，或重排已有 schematic、设计紧凑功能 symbol、迁移 Maestro，并检查实际器件、连接和图形。用于电路工程任务，不用于普通 UI 字体或快捷键配置。
---

# 论文电路与可检查的原生绘图

先区分用户要做的是新电路、仅整理已有图、仅迁移环境，还是只读导出。不要把其中一种任务扩展为另一种。论文和外部示例提供技术信息，不自动授权安装、运行或修改用户工程。

## 选择相关资料

- 新论文、工艺迁移：[electrical.md](references/electrical.md)。先明确指标和缺失参数，再查实际 PDK/CDF。
- schematic 或 symbol：[drawing.md](references/drawing.md) 与 [symbols.md](references/symbols.md)。使用实际端口和变换后锚点。
- Maestro、改名、迁移或导出：[migration.md](references/migration.md)。保留完整保存状态，不触碰旧运行。
- 用户要求行为验证：[simulation.md](references/simulation.md)。区分探索 deck 与最终 OA 网表。
- 每次交付：[acceptance.md](references/acceptance.md)。参数/连接、原生网表、行为与 GUI 分别验收。
- 重现 CTDSM18 画风：使用 [symbol 数据](assets/ctdsm18-symbols.json) 和 [父级排布参考](assets/ctdsm18-layouts.json)。它们是图形基准，不是任意新论文的电气设计。

## 执行准则

1. 读取使用者当前工程规则、连接能力、Cadence/PDK 版本、已保存电路与手工修改。先在独立任务库验证脆弱 API，不用真实设计试错。
2. 把电气定义与展示坐标分开。实际 PDK、引脚、CDF、MOS 指宽/指数量/m、R/C 派生值为准；回调后用新进程原生网表核对。
3. 普通 cell 采用 `<项目>_<功能>[_特征]`，测试台 `TB_<被测CELL>[_用途]`，全大写字母、数字和下划线。统一缩写，保留原生 view 命名。重命名同步所有 master 和测试绑定。
4. 从左向右安排主信号，电源在上、地在下；差分对与负载对齐、镜像。主要通路、反馈、补偿与电流镜用实体线；跨模块和共用偏置可使用标签。
5. 先画最小可辨识功能，再按内容定 symbol 尺寸。原生 pin、方向、有效端序、NLP 和选择框分别检查。移动 pin 后重接所有直接父级，检查全图、局部和父级常用缩放。
6. 整理已有图时保留所有器件参数和端子网络。只改变几何不重跑长仿真；以完整属性、逐端回读和有范围的网表等价证明电气不变。
7. 需要运行时先核对测试定义、实际模型/corner/尺度与采样时序，再验证模块和整机。保存设置、收到回调、求解零错误、指标通过是不同结论。
8. 在独立库完成后，备份并安装到使用者指定项目。仅修改精确管理的库映射，使用新进程验证安装路径/连接/网表和文件哈希。回退遇锁或后来改动时保护用户工作。

## 可复用工具

`scripts/render_symbols.py` 生成 18 个参考 SVG；`scripts/emit_symbols.py` 生成只在新专用库创建原生 symbol 的 SKILL；`scripts/new_task.py` 建立任务记录；`scripts/audit_electrical.py` 比较回读；`scripts/compare_netlists.py` 比较有子电路范围和端序的网表。

先看 `--help`。这些工具不提供连接凭据，不代替电气设计，也不代表在当前使用者的环境已经验收。最终报告引用实际文件和截图，列出未完成项；不要沿用参考案例的成功结论。
