# 工具用法与常见问题

以下命令从仓库根目录运行，使用 Python 3.9+ 标准库。除生成的 SKILL 需要在 Virtuoso 中执行外，其余工具都在本地处理文件，不自动连接服务器或运行仿真。

## 先选工具

| 工具 | 输入 | 得到什么 |
|---|---|---|
| `render_symbols.py` | 仓库自带参考数据 | 18 个 SVG 预览 |
| `emit_symbols.py` | 新库名、目标库目录、真实技术库名 | 创建 18 个原生 symbol 的 `.il` 文件 |
| `new_task.py` | 项目前缀、任务类型、新目录 | 待填写的需求、验收记录和分类目录 |
| `audit_electrical.py` | 两份完整电气回读 JSON | 参数、master、端子网络和端口的差异报告 |
| `compare_netlists.py` | 两份原生 Spectre 网表 | 保留子电路范围与端序的文本比较结果 |
| `scripts/check_repo.py` | 当前仓库文件 | 本地链接、参考数据、发布文件与哈希检查 |

前五个脚本位于 `skills/paper-to-virtuoso/scripts/`。任意工具都可加 `--help` 查看参数；仓库检查脚本直接运行即可。

## 预览 18 个参考 symbol

```sh
python skills/paper-to-virtuoso/scripts/render_symbols.py --out build/reference
```

打开 `build/reference/` 中的 SVG。预览与原生生成器使用同一份图形数据；浏览器的字体、配色和显示与 Virtuoso 不完全相同。

## 在 Virtuoso 中生成原生 symbol

这条命令只生成 `.il` 脚本。执行它需要你已有可用的 Virtuoso 环境和技术库。

### 1. 确定三个目标值

| 参数 | 如何填写 |
|---|---|
| `--library` | 尚不存在的新专用库名，全大写字母、数字和下划线，例如 `DEMO_CIRCUIT_SYMBOLS` |
| `--lib-path` | **运行 Virtuoso 那台机器上的**绝对目录；目标目录必须尚不存在，父目录须可写 |
| `--tech-library` | 在那个 Virtuoso 进程中可读取的真实技术库名 |

当前生成器采用 Linux 绝对库路径和参考 **DBU=160**。目标技术库必须匹配；其他 DBU 需先适配生成器与坐标，再在专用库验证。不要为了运行示例修改真实 PDK。

### 2. 生成文件

将下面两个占位值换成实际值。命令写成单行，可在 PowerShell 或常见 Linux shell 使用：

```sh
python skills/paper-to-virtuoso/scripts/emit_symbols.py --library DEMO_CIRCUIT_SYMBOLS --lib-path /path/to/isolated/DEMO_CIRCUIT_SYMBOLS --tech-library YOUR_TECH_LIBRARY --out build/reference-symbols.il
```

`SKILL_GENERATED_NOT_EXECUTED` 表示文件生成完成，尚未写入 Cadence。`--out` 是运行 Python 机器上的文件位置，与 `--lib-path` 所指的目标机器目录不同。

### 3. 在专用会话执行

检查生成文件；若 Python 和 Virtuoso 在两台机器，把 `.il` 复制到 Virtuoso 可读的位置。在独立工作目录启动专用 Virtuoso，确认 `cds.lib` 能找到技术库，然后在该专用 CIW 加载文件：

```lisp
load("/path/to/reference-symbols.il")
```

这里使用复制后的实际路径。脚本会创建新库及其中的 18 个 symbol，拒绝覆盖已有库或目标目录。运行成功会打印 `VCS_PASS NATIVE_REFERENCE_SYMBOLS 18`。

从 Library Manager 打开 `DEMO_CIRCUIT_SYMBOLS/CTDSM_OTA_FF/symbol` 等视图，回读接口并检查画面。脚本不创建下层模拟 schematic，也不自动把新库注册到你日常项目的 `cds.lib`；跨进程使用时，需由连接工具或 AI 按[安装规则](../skills/paper-to-virtuoso/references/migration.md)确认映射。

若执行中途失败，保留日志和已创建的专用库，先查失败点。不要把目标库删掉后反复重跑，也不要改用真实工程库名绕过保护。

## 建立一份任务记录

```sh
python skills/paper-to-virtuoso/scripts/new_task.py --project AFE --mode paper --out work/afe
```

`AFE` 是你自定的大写项目缩写。`--mode` 可选：`paper` 新电路、`redraw` 整理图、`environment` 迁移环境、`export` 导出。

生成内容：

```text
work/afe/
  TASK.md                 任务入口
  requirements.json       待填写的需求
  acceptance.json         待填写的验收结果
  CONNECTION.local.json   本地连接占位信息，不提交凭据
  evidence/               检查证据
  screenshots/            实际画面
  netlists/               原生网表
  simulation/             实际仿真资料
  backup/                 备份
```

`EMPTY_TASK_CREATED` 只表示记录目录创建完成。先根据用户实际授权填写需求；`paper` 模式中的电气修改/仿真字段是模板初值，不能替代用户授权。已有目录会被拒绝，继续任务时直接读取原目录。

## 比较重排前后的电气数据

先用你的连接工具或原生 SKILL，从修改前后的 OA 数据库导出完整快照，保存为 `work/afe/before.json` 和 `work/afe/after.json`。本仓库的比较器不负责自动采集 OA；字段与示例见[回读格式](../skills/paper-to-virtuoso/references/acceptance.md#电气回读-json)。

```sh
python skills/paper-to-virtuoso/scripts/audit_electrical.py --before work/afe/before.json --after work/afe/after.json --report work/afe/electrical-check.json
```

输出 `supplied_electrical_data_equal: true` 表示提供的数据一致。坐标变化不算电气差异；参数、端子网络、master 或端口方向变化会列入 `differences`。仍需核对 CDF 默认值和原生网表。

从实际 OA 前后分别生成原生 Spectre 网表，再比较：

```sh
python skills/paper-to-virtuoso/scripts/compare_netlists.py --before work/afe/before.scs --after work/afe/after.scs
```

成功输出 `PROVIDED_NATIVE_NETLIST_TEXT_EQUIVALENT`。差异时输出 diff 并返回非零退出码；同样不能省掉 GUI 或仿真检查。

若本次明确重命名了 cell，创建经审阅的 `work/afe/cell-map.json`：

```json
{"cells": {"OLD_OTA": "AFE_OTA", "OLD_TOP": "AFE_TOP"}}
```

给上面两个比较命令加 `--cell-map work/afe/cell-map.json`。回读比较还支持 `libraries` 映射；网表只映射 master 名，不能随意重命名信号来消除差异。确需比较 PCell 自动编号时，按实际前缀使用 `--pcell-prefix`，比较器仍保留完整定义和参数。

## 检查仓库与工具

```sh
python scripts/check_repo.py
python -m unittest discover -s tests -v
```

第一条预期 `issues: []`；第二条预期测试通过。这些是仓库工具的检查，与你的新电路是否达标分别记录。已有仓库未修改时，使用者通常只需检查一次；详细验证范围见 [VALIDATION.md](VALIDATION.md)。

## 常见问题

| 现象 | 怎么处理 |
|---|---|
| 找不到 `python` | 检查 Python 安装；按实际环境使用 `python3` 或 Windows 的 `py` |
| 找不到 `scripts/…` | 进入包含本仓库 `README.md` 的根目录，再运行命令 |
| AI 读不到 GitHub | 下载仓库，提供 AI 可读取的本地位置 |
| 目标库或目录已存在 | 新建任务使用另一个未占用的库/路径；已有任务先检查已完成内容 |
| 提示 DBU 不匹配 | 当前原生参考要求 160；由 AI 适配并在专用库验证，保留实际技术库设置 |
| `.il` 已生成，Library Manager 中没有新库 | 生成与执行是两步；确认已在专用 Virtuoso 执行、查看日志及该进程库映射 |
| 有 SVG/symbol，但电路没有功能 | 参考生成器只创建 symbol；模拟电路需要按论文和实际 PDK 另行搭建 |
| 比较器通过，仍担心连接 | 核对快照是否包含全部端子/属性，再检查实际 CDF、原生网表和 GUI |
| 下载后仓库哈希检查失败 | 确认文件未被改写；自行修改内容会使发布清单失效，保留差异再排查 |
