# 用 AI 搭建清楚、规范的 Virtuoso 电路

想让 AI 根据论文搭电路，或者把已有原理图整理得更好读？把这个仓库链接交给能操作你工程的 AI，再提供论文和工艺要求，就可以按这里的规范开始。

这里提供**搭建方法、绘图规则、功能 symbol 参考和检查工具**。AI 负责结合你的实际工艺完成设计；你可以用示例图和验收清单检查成果。

## 你想做什么？

| 任务 | 可以让 AI 完成的工作 | 使用入口 |
|---|---|---|
| 按论文搭新电路 | 理解拓扑、映射工艺器件、建立分层电路和测试台、验证指标 | [新电路提示](docs/AI_START.md#搭建新论文电路) |
| 整理已有电路 | 紧凑排版、功能 symbol、规范命名、同步父级连线 | [整理电路提示](docs/AI_START.md#整理已有原理图和-symbol) |
| 搬迁仿真环境 | 迁移 Maestro 的测试、模型、变量、分析和输出 | [迁移环境提示](docs/AI_START.md#只迁移-maestro-环境) |
| 导出工程和历史结果 | 保存可交接的工程快照，分清旧结果与新验证 | [导出提示](docs/AI_START.md#只导出工程和历史结果) |

## 第一次使用，按这三步开始

### 1. 准备工程信息

- **搭新电路**：提供论文文件、要复现的图或章节、目标工艺、电源和优先指标。
- **整理已有电路**：提供库名、cell 名称、工程位置，以及要改哪些图。
- **实际操作环境**：AI 需要能读取工程，并通过你已有的工具访问 Virtuoso。只有网页聊天和仓库链接时，可以先分析论文、制定方案；写入电路还需[接通环境](docs/SETUP.md)。

工艺、连接方式已经保存在你的项目里时，让 AI 先读取即可。新用户可直接填写[需求表](docs/AI_START.md#先说明你的任务)。

### 2. 把下面这段话发给 AI

```text
请使用 https://github.com/w1213ph/virtuoso-circuit-skills 帮我完成电路任务。
先读取仓库的 AGENTS.md 和 skills/paper-to-virtuoso/SKILL.md，
再按我的任务读取相关参考文件。

我的任务：按附上的论文搭建电路。
论文范围：[图号或章节]
目标工艺：[实际 PDK 库名与版本]
电源与主要指标：[填写，并说明优先级]
工程位置与连接方式：[填写，或读取我已有的项目配置]

原理图和 symbol 参照仓库 CTDSM18 示例：功能直观、尺寸紧凑、
主路径与反馈使用实体连线；保留我已有的手工修改和正在运行的会话。
完成后告诉我从哪个库/cell/view 打开，并分别交付连接检查、
网表检查、实际仿真结果和全图/局部目视检查。未完成的项目明确说明。
```

如果只想改图，把“我的任务”改为：**只整理指定 schematic/symbol，迁移现有 Maestro；保持电气参数和拓扑，不重跑仿真。** 其他场景的完整提示见[使用示例](docs/AI_START.md)。

### 3. 按交付内容检查成果

让 AI 给出工程入口、改动说明、实际截图和检查结果。重点看三件事：

- **电路是否正确**：实际工艺器件、参数和端子连接已回读；纯重排前后网表一致。
- **图是否好读**：symbol 能看出功能，父级间距紧凑，主路径清楚，文字不压线。
- **指标是否真的测过**：仿真需带条件和结果；只迁移环境时，应明确“未重跑”。

完整检查项见[验收清单](skills/paper-to-virtuoso/references/acceptance.md)。你不需要先读完仓库里的所有技术文件。

## 画出来是什么样？

下面是已完成的 CTDSM18 案例在 **Virtuoso 中导出的真实画面**。

![前馈 OTA 的原生功能 symbol](docs/images/ota-symbol.png)

OTA symbol 表达差分极性、级联增益和前馈路径。其他模块按各自功能绘制，并按内容确定大小。

![CTDSM18 顶层原理图中的模块与连线](docs/images/top-schematic.png)

symbol 缩小后，父级实例和连线也同步收紧。[案例说明](examples/ctdsm18/README.md)包含这次整理的具体做法与完成范围。

## 没有 Cadence，也能先看参考图

电脑有 Git 和 Python 3.9+ 即可运行，脚本只用 Python 标准库：

```sh
git clone https://github.com/w1213ph/virtuoso-circuit-skills.git
cd virtuoso-circuit-skills
python skills/paper-to-virtuoso/scripts/render_symbols.py --out build/reference
```

打开 `build/reference/` 里的 SVG，就能查看 18 个参考 symbol。也可以用 GitHub 的 **Code → Download ZIP** 下载，解压后在仓库目录运行最后一条命令。系统只有 `python3` 时，将命令里的 `python` 换成 `python3`。

这一步生成预览文件。要在 Virtuoso 中生成可用的原生 symbol，继续阅读[环境准备](docs/SETUP.md)和[工具用法](docs/TOOLS.md)。

## 仓库里有什么？

| 文件或目录 | 什么时候看 |
|---|---|
| [docs/AI_START.md](docs/AI_START.md) | 填需求、复制适合自己任务的提示 |
| [docs/SETUP.md](docs/SETUP.md) | 准备 Python、连接 Virtuoso、让 AI 读取 Skill |
| [docs/TOOLS.md](docs/TOOLS.md) | 查命令、输入输出、原生 symbol 生成和常见问题 |
| [examples/ctdsm18](examples/ctdsm18/README.md) | 看效果、学习 symbol 与父级的排布 |
| [AGENTS.md](AGENTS.md) 与 [SKILL.md](skills/paper-to-virtuoso/SKILL.md) | AI 的执行入口，按任务读取细则 |
| [docs/VALIDATION.md](docs/VALIDATION.md) | 查哪些工具和案例已经实际验证 |

## 能复现到什么程度？

仓库提供 18 个 symbol 的固定图形与接口数据，原生生成器已在独立 Virtuoso IC6.1.8 测试库重放，与认可版本的几何和接口一致。参考数据可重复生成同一组图形。

新论文可以沿用这里的搭建和绘图标准；电气设计与性能仍取决于实际 PDK、模型、论文细节和仿真。仓库不附带 PDK、完整模拟电路数据库或服务器访问配置。具体范围见[验证记录](docs/VALIDATION.md)。

功能图表达参考 [JPWang-OvO/analog-artist-skills](https://github.com/JPWang-OvO/analog-artist-skills)。可选连接工具为 [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite)，安装入口见[环境准备](docs/SETUP.md)。出处与版本见 [NOTICE](NOTICE)，代码与文档采用 [GPL-3.0](LICENSE)。
