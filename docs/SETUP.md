# 准备环境并开始使用

本仓库既能作为 AI 的项目参考，也能在本地运行辅助脚本。根据你要做的事准备环境即可。

| 你要做什么 | 需要准备 |
|---|---|
| 阅读规则、把任务交给 AI | 能读取仓库文件的 AI；[任务提示](AI_START.md) |
| 预览 symbol、建立记录、比较已有数据 | Python 3.9+；无需 Cadence 或额外 Python 包 |
| 创建/检查原生电路和 symbol | 可用的 Virtuoso、实际技术库/PDK、相应许可和操作工具 |
| 运行仿真 | 对应仿真器、许可、模型与明确的测试条件 |

## 1. 获取仓库

安装 Git 的电脑可以运行：

```sh
git clone https://github.com/w1213ph/virtuoso-circuit-skills.git
cd virtuoso-circuit-skills
```

也可以在 GitHub 点击 **Code → Download ZIP**，解压后进入包含 `README.md`、`skills/` 和 `scripts/` 的目录。下文所有终端命令都从这个目录运行。

先检查 Python：

```sh
python --version
```

需要 3.9 或更新版本。Linux/macOS 只有 `python3` 时，后续统一使用 `python3`；Windows 已有 `py` 命令时，也可以用它选择已安装的合适版本。离线脚本不需要运行 `pip install`。

## 2. 先预览一次

```sh
python skills/paper-to-virtuoso/scripts/render_symbols.py --out build/reference
```

终端出现 `REFERENCE_SVG_CREATED 18`，并在 `build/reference/` 生成 18 个 SVG。用浏览器打开其中任意一个，即可查看图形。此步骤不连接或修改 Virtuoso。

更多工具与输出说明见 [TOOLS.md](TOOLS.md)。想直接交给 AI 搭电路，可继续下一步。

## 3. 让 AI 读取工作规则

将仓库链接和 [AI_START.md](AI_START.md) 中适合任务的提示交给 AI。它应先读取：

```text
AGENTS.md
skills/paper-to-virtuoso/SKILL.md
```

如果 AI 无法访问网页，把下载好的仓库放到它可读取的项目中，并提供实际位置。它需要按任务继续读取 Skill 中链接的 references；只复制首页的文字会丢掉细则。

支持项目内 Skill 的工具，也可以把整个 `skills/paper-to-virtuoso/` 目录复制到该工具的项目技能目录。保留 `SKILL.md`、`references/`、`assets/`、`scripts/` 和 `agents/` 的相对位置。具体发现方式沿用你所用工具的规则；手动读取无需全局安装。

## 4. 接通你自己的 Virtuoso

已有可用连接时继续使用，并告诉 AI 如何读取本地配置。没有连接时，可以选择：

| 方式 | 由谁提供连接能力 | 开始前确认 |
|---|---|---|
| 桥接工具 | [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) | 按上游 README 配置客户端和服务器端；本仓库不附带桥接程序 |
| 本机终端或 SSH＋SKILL | 你已有的 Cadence 启动环境与远程访问工具 | 能在独立工作目录启动专用 Virtuoso，并读取真实工艺库 |

先让 AI 在测试环境验证连接、当前工作目录和库映射，再执行写入。使用独立测试库和会话，保留你正在编辑的窗口及运行中的仿真。用户名、主机和凭据保存在本地配置中。

本仓库不绑定某一种连接方式。它提供 AI 的搭建规则和离线辅助工具；实际器件查询、绘图与数据库回读需要连接工具支持，或由 AI 编写适合当地版本的原生 SKILL。

## 文档中的几个术语

| 术语 | 在这里的意思 |
|---|---|
| PDK / CDF | 工艺器件与模型工具包 / Virtuoso 中器件参数和回调的定义 |
| library / cell / view | 库 / 电路单元 / 单元的不同视图，例如 `schematic`、`symbol` |
| symbol / 父级 | 模块的图形接口 / 放置该模块实例的上一层原理图 |
| Maestro | 保存测试、变量、分析和输出等设置的仿真环境 |
| OA / 原生网表 | Virtuoso 电路数据库 / 从实际保存电路提取的仿真连接描述 |
| SKILL / DBU | Cadence 的脚本语言 / 数据库坐标精度，影响图形是否能按坐标准确保存 |

## 5. 选择下一步

- **搭建或整理电路**：填写[任务提示](AI_START.md)，提供实际工艺或现有工程，让 AI 按任务执行。
- **只复现 18 个参考 symbol**：按[原生生成步骤](TOOLS.md#在-virtuoso-中生成原生-symbol)生成 SKILL，并在专用新库执行。
- **检查重排有没有改变电路**：按[电气比较说明](TOOLS.md#比较重排前后的电气数据)准备前后回读和原生网表。

遇到命令、路径、DBU 或库名问题，查看[常见问题](TOOLS.md#常见问题)。
