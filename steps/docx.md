# 步骤 docx — DOCX 交付（独立交付步骤，不参与 1~6 流程推进）

**命令**：`/icode docx [自然语言]`

**定位**：把已有真实材料交付为可编辑 `.docx`。这是和 `/icode ppt` 并列的独立交付步骤：**不创建工单目录、不写 `.ico_metadata.json`、不更新 `completed_steps` 或 `status`**。现有 `/icode doc` 仍只维护工程知识库，二者不可混用。

**产物**：P0（明确 Markdown 路径）把 `{source}.md` 输出为同目录 `{source}.docx` 和 `{source}.manifest.json`；P1（项目/模块/本次功能/本次 Bug）输出 `<project_root>/.icode_output/docx/{project}_{scenario}.docx`、同名 manifest 及可选 `preview/`。输入 Markdown 永不改写。

## 0. L1 决策记录与前置

本步骤为 **L1**：在 `.decision_anchors.json` 或交付 manifest 的 `source_map` 中记录路由、来源、输出位置、是否请求视觉验收。无需 sequential-thinking。

1. 先执行 `python3 tools/docx/bootstrap_runtime.py`。未显式配置 pip 源时，它会以同一 lock 先尝试 PyPI，仅在网络/超时失败后依次回退到内置 HTTPS 镜像；用户的 `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_NO_INDEX` 保持原样。依赖或版本冲突不切源。失败时停止；不得 `pip install --user`、不得使用全局 `python-docx`。
2. 输出 JSON 中的 `python` 是本次唯一可使用的 DOCX Python。该运行时由 ICODE 安装器自己维护在用户目录，升级随 lock hash 创建新版本，旧版本不被删除。
3. 禁止调用 PATH 上的 `soffice` / `libreoffice`。视觉验收只能走 `tools/docx/render_docx.py` 的 ICODE 受管理 renderer resolver。

## 1. 自然语言 → 场景

| 用户表达 | 路由 | 必需来源 | 默认输出 |
|---|---|---|---|
| 指定 `.md` / `.markdown` 文件路径 | **P0 faithful** | 此文件及本地相对图片 | 源文件同目录 |
| “项目/工程/总体” | **P1 project** | `project_docs` + 仓库结构 + README；文档快照必须回读 Git 核实 | `.icode_output/docx/` |
| “模块/组件 <名称>” | **P1 module** | 对应知识库章节 + 指定源码目录 | `.icode_output/docx/` |
| “本次功能/需求/交付 Word” | **P1 feature** | 当前工程最近且唯一 ICODE 工单 + Git diff | `.icode_output/docx/` |
| “本次 Bug/根因/修复报告” | **P1 bugfix** | `log_analysis.md`、patch、验证和引用证据 | `.icode_output/docx/` |

无路径的“生成交付 Word”默认最近且唯一工单；没有工程、没有工单或存在多个同等候选时，**只追问一次**，不得把普通 Markdown 猜成工单。P1 先在主会话把真实来源组织成临时 Markdown，再按 P0 构建；临时 Markdown 和 source map 必须保留在 `.icode_output/docx/`，不得凭印象补全事实。

## 2. 与现有 MCP 的边界

- P0 单个本地 Markdown：不强制 MCP；文件路径、hash、相对图片由本地构建器记录。
- P1 使用工单、日志、附件或知识库：按 `icode-mcp-policy` 的 `docx` 路由先用 **icode-evidence** 回读并记录 hash；语料超过常规 `rg` 范围时再使用 **icode-local-index** 找候选，命中仍须回读原文件。
- 不新增 DOCX MCP，也不把本地文档内容发送到远程服务。`cheap-research`、vision、浏览器等只在其既有明确条件成立时使用，不能替代来源核验。

## 3. 构建、结构验收与视觉验收

```bash
# 1) 用 bootstrap JSON 取 ICODE 自管 Python
RUNTIME_JSON="$(python3 tools/docx/bootstrap_runtime.py)"
DOCX_PYTHON="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["python"])' "$RUNTIME_JSON")"

# 2) P0：源文件同级输出；P1：SOURCE 是刚收集的真实交付 Markdown
"$DOCX_PYTHON" tools/docx/build_docx.py "$SOURCE" "$OUTPUT" --manifest "$MANIFEST"

# 3) 必须结构验收；失败不得交付
"$DOCX_PYTHON" tools/docx/inspect_docx.py "$OUTPUT" --source "$SOURCE" --manifest "$MANIFEST"

# 4) 视觉验收仅使用 ICODE 所有的匹配 renderer；无匹配包会写 visual_qa_pending
"$DOCX_PYTHON" tools/docx/render_docx.py "$OUTPUT" "$PREVIEW_DIR" --result-manifest "$MANIFEST"
```

构建器支持标题、段落、清单、表格、引用、代码、本地图片和常见 Mermaid flowchart。Mermaid 成功时是嵌入 DOCX 的 PNG；不支持的 Mermaid 不会丢失，会保留源码代码块并在 manifest 标注 fallback。图片、代码、表格和每个来源块都写入 source map。

`inspect_docx.py` 检查 OOXML 包、标题样式、表格、嵌入 media/drawing 和输入 hash。`render_docx.py` 的三种合法状态：`passed`、`visual_qa_pending`、`visual_qa_failed`；只有 `passed` 可以声称视觉渲染已验收。当前发行包不带匹配 renderer 时，结构验收通过仍可交付，但必须在收尾说明视觉验收待发行包补齐，禁止使用系统 LibreOffice 兜底。

## 4. 收尾

输出：DOCX 路径、manifest 路径、路由（P0/P1）、来源清单、结构验收结果、视觉验收状态。P1 还说明关联工单/知识库/Git baseline。用户说“改 Word”时复用已有 source Markdown + manifest 增量修改，不重新编造或重收集来源。
