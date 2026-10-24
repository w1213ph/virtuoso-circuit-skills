# 连接、依赖和命令

离线脚本要求 Python 3.9+，只使用标准库。真正写入/验证 OA 需要使用者自己的 Cadence Virtuoso、工艺库和工具许可。

## 接通使用者的 Virtuoso

优先使用当前环境已有的受控连接工具。可选择 [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite)，按上游连接说明安装客户端、连接服务器，并在指定测试会话加载其给出的命令。用户名、主机、路径和密钥由使用者本地提供，本仓库不保存。

也可在独立工作目录用本机终端或 SSH 启动专用 Virtuoso 进程加载 SKILL。不要把脚本注入正在编辑的用户会话，不关闭或强制重载它。先验证 `1+2` 等只读表达式和正确的库映射，再执行写入。

## 生成同一组参考 symbol

```sh
python skills/paper-to-virtuoso/scripts/render_symbols.py --out build/reference
python skills/paper-to-virtuoso/scripts/emit_symbols.py \
  --library DEMO_CIRCUIT_SYMBOLS \
  --lib-path /path/to/isolated/DEMO_CIRCUIT_SYMBOLS \
  --tech-library YOUR_TECH_LIBRARY \
  --out build/reference-symbols.il
```

将 `YOUR_TECH_LIBRARY` 换成目标环境的真实技术库。先检查生成文件，再在该隔离工作目录的独立 Virtuoso 进程中加载：

```lisp
load("/path/to/reference-symbols.il")
```

生成器不自动连接服务器或执行代码，也不覆盖任何已有目标库。它保留参考 pin 坐标/方向/端序、roman、NLP label 和选择框。原生创建完成后仍要回读，并查看实际 symbol 和父级显示。参考 DBU 为 160；其他技术库需验证坐标是否可精确表示。

这组参考只有 symbol。搭电路时必须先按论文建立真实下层 schematic，再核对接口；不要用缺少实际电路的 symbol 代替功能实现。

## 任务与电气比较

```sh
python skills/paper-to-virtuoso/scripts/new_task.py --project AFE --mode redraw --out work/afe
python skills/paper-to-virtuoso/scripts/audit_electrical.py \
  --before work/afe/before.json --after work/afe/after.json --report work/afe/electrical-check.json
python skills/paper-to-virtuoso/scripts/compare_netlists.py \
  --before work/afe/before.scs --after work/afe/after.scs
```

回读 JSON 的字段与示例见 [验收说明](../skills/paper-to-virtuoso/references/acceptance.md)。改名时以 `--cell-map` 提供已审阅的旧名→新名映射，不做模糊字符串替换。PCell 编号比较只在明确指定前缀后按完整定义归一，保留端序与参数。

## 将 Skill 放入 AI 项目

支持项目内 Skill 的 AI 可将 `skills/paper-to-virtuoso` 放入其项目技能目录，或直接读取完整目录。此目录内的 references、assets 和 scripts 一起保留。其他 AI 可以从 README 的提示开始手动读取，不依赖 Codex 专有工具。
