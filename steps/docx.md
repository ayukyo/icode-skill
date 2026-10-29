# 步骤 docx — DOCX 交付（独立交付步骤，不参与 1~6 流程推进）

**命令**：`/icode docx [自然语言]`

**定位**：把真实材料写成便于阅读、可核对、可编辑的 `.docx`，包含章节规划、文字改写、源码核对、图示制作、Word 修订、排版和交付检查；明确要求忠实转换时保持原文。这是和 `/icode ppt` 并列的独立交付步骤：**不创建工单目录、不写 `.ico_metadata.json`、不更新 `completed_steps` 或 `status`**。现有 `/icode doc` 仍只维护工程知识库，二者不可混用。

**产物**：P0（明确 Markdown 路径）把 `{source}.md` 输出为同目录 `{source}.docx` 和 `{source}.manifest.json`；P1（项目/模块/本次功能/本次 Bug）输出 `<project_root>/.icode_output/docx/{project}_{scenario}.docx`、同名 manifest 及可选 `preview/`。输入 Markdown 永不改写。

用户指定的工程、文档集合、输出目录和禁做事项优先于默认值。修改已有 Word 时保留原文件备份，在候选文件完成检查后更新指定文档；不顺带修改项目代码、提交、推送、安装技能或发布。

## 0. L1 决策记录与前置

本步骤为 **L1**：在 `.decision_anchors.json` 或交付 manifest 的 `source_map` 中记录路由、来源、输出位置、是否请求视觉验收。无需 sequential-thinking。

1. 先执行 `python3 tools/docx/bootstrap_runtime.py`。未显式配置 pip 源时，它会以同一 lock 先尝试 PyPI，仅在网络/超时失败后依次回退到内置 HTTPS 镜像；用户的 `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_NO_INDEX` 保持原样。依赖或版本冲突不切源。失败时停止；不得 `pip install --user`、不得使用全局 `python-docx`。
2. 输出 JSON 中的 `python` 是本次唯一可使用的 DOCX Python。该运行时由 ICODE 安装器自己维护在用户目录，升级随 lock hash 创建新版本，旧版本不被删除。
3. 正常 `./install.sh` 会安装兼容渲染器；首次默认 `render_docx.py` 也复用 `bootstrap_renderer.py` 自动补齐。固定官方包在用户 `runtime/docx-renderer/` 中独立解包并原子登记，登记不受技能目录同步覆盖；Windows/macOS/ARM 等无 recipe 的平台明确保留 `visual_qa_pending`。
4. 禁止调用 PATH 上的 `soffice` / `libreoffice`。视觉验收只能走 `tools/docx/render_docx.py` 的 ICODE 受管理 renderer resolver。

## 1. 自然语言 → 场景

| 用户表达 | 路由 | 必需来源 | 默认输出 |
|---|---|---|---|
| 指定 `.md` / `.markdown` 文件路径，仅转换格式 | **P0 faithful** | 此文件及本地相对图片 | 源文件同目录 |
| “项目/工程/总体” | **P1 project** | `project_docs` + 仓库结构 + README；文档快照必须回读 Git 核实 | `.icode_output/docx/` |
| “模块/组件 <名称>” | **P1 module** | 对应知识库章节 + 指定源码目录 | `.icode_output/docx/` |
| “本次功能/需求/交付 Word” | **P1 feature** | 当前工程最近且唯一 ICODE 工单 + Git diff | `.icode_output/docx/` |
| “本次 Bug/根因/修复报告” | **P1 bugfix** | `log_analysis.md`、patch、验证和引用证据 | `.icode_output/docx/` |
| “按源码写原理/流程文档”“更好理解”“重排章节” | **P1 内容组织** | 指定当前源码、已有文档及用户反馈；不以知识库快照代替当前实现 | 用户指定目录，否则 `.icode_output/docx/` |
| “改这份 Word”“第几章/图看不懂” | **已有 Word 修订** | 当前实际 DOCX、已有 Markdown/manifest（如有）、相关源码 | 原目录的候选与备份，检查后更新目标 |

无路径的“生成交付 Word”默认最近且唯一工单；没有工程、没有工单或存在多个同等候选时，**只追问一次**，不得把普通 Markdown 猜成工单。P1 先在主会话把真实来源组织成 Markdown，再构建；源文和 source map 保留在指定交付目录的审计子目录，未指定时用 `.icode_output/docx/`，不得凭印象补全事实。

指定文件并要求改写内容时，不误走忠实转换。已有 Word 没有配套 Markdown 时，直接读取 DOCX 的正文、表格和图片，按修订流程处理，不要求用户先重新提供源文。

## 2. 内容组织、图示与修订

- **P0 忠实转换**：保留内容、顺序及输入文件，只检查转换是否丢失内容；不主动精简、删源码索引或重写术语。
- **P1 内容组织或重写**：先读 [docx_authoring.md](../references/docx_authoring.md)，据文档主题规划章节、核对实现及调用链、用短句解释原理，再选择图和必要接口。针对多份文档分配主题，避免每份都重复整个项目。
- **已有 Word 修改**：先读 [docx_revision.md](../references/docx_revision.md)，记录修改范围与需要保留的内容；复用原图例、配色和排版，检查坐标、公式、单位、条件、接口及其它文档是否被误改。
- **制作图示**：沿用 docx_authoring 的图示规则。标准构建器适合普通文字和简单线性图；复杂流程图、带条件的时序图、坐标图可使用宿主可用的文档/绘图能力，或自管 Python 中的 `python-docx`、Pillow 和专用本地脚本。保留绘图脚本或图源、模块配色及图注，统一进入下方检查。不得因构建器不能表达而删掉真实分支、标签、坐标或模块。

局部改字不必重新规划整份文档；只加载本次相关指南。内容审阅由主会话完成，机器结构检查不能代替原理、术语和图文含义核对。

## 3. 与现有 MCP 的边界

- P0 单个本地 Markdown：不强制 MCP；文件路径、hash、相对图片由本地构建器记录。
- P1 使用工单、日志、附件或知识库：按 `icode-mcp-policy` 的 `docx` 路由先用 **icode-evidence** 回读并记录 hash；语料超过常规 `rg` 范围时再使用 **icode-local-index** 找候选，命中仍须回读原文件。
- 不新增 DOCX MCP，也不把本地文档内容发送到远程服务。`cheap-research`、vision、浏览器等只在其既有明确条件成立时使用，不能替代来源核验。

## 4. 构建、结构验收与视觉验收

```bash
# 1) 用 bootstrap JSON 取 ICODE 自管 Python
RUNTIME_JSON="$(python3 tools/docx/bootstrap_runtime.py)"
DOCX_PYTHON="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["python"])' "$RUNTIME_JSON")"

# 2) P0：源文件同级输出；P1：SOURCE 是刚收集的真实交付 Markdown
"$DOCX_PYTHON" tools/docx/build_docx.py "$SOURCE" "$OUTPUT" --manifest "$MANIFEST"

# 3) 必须结构验收；失败不得交付
"$DOCX_PYTHON" tools/docx/inspect_docx.py "$OUTPUT" --source "$SOURCE" --manifest "$MANIFEST"
# P1图文交付加 --require-diagrams，阻止把图示源码回退当作完成图示。

# 4) 视觉验收仅使用 ICODE 所有的匹配 renderer；无匹配包会写 visual_qa_pending
"$DOCX_PYTHON" tools/docx/render_docx.py "$OUTPUT" "$PREVIEW_DIR" --result-manifest "$MANIFEST"
```

构建器支持标题、段落、清单、表格、引用、代码、本地图片和简单 Mermaid 线性 flowchart。内置图仅支持矩形节点、无标签的实线有向边，支持 LR/RL/TB/BT/TD；不支持的语法整图保留源码代码块并在 manifest 标注 fallback，不再忽略边标签、条件、样式或分支后输出失真的图。忠实转换需报告 fallback；要求图示的内容交付应改用本地图片并通过 `--require-diagrams`。图片、代码、表格和每个来源块都写入 source map。

专用脚本修改 Word 时也必须用自管 Python，保留脚本、源文快照和同名 manifest，更新 `input/output` 的路径/hash、`source_images` 与 `source_map`。`source_map` 用构建器的块记录列表；已有手工 manifest 若格式不同，先保留旧版再规范化，将路由决策保留为独立字段，不删旧依据。源文与实际 Word 同步，不能仅更换 hash 来掩盖不同步。允许的构建手段不限于 `build_docx.py`，验收要求保持一致。

按任务使用以下辅助检查；来源记录和对照报告保存在交付审计目录，不另塞到正文作为源码索引：

```bash
# 有源码依据的技术文档：检查已审读源文件的hash和行范围内符号/片段
"$DOCX_PYTHON" tools/docx/verify_sources.py "$SOURCE_RECORD" --root "$PROJECT_ROOT" --report "$SOURCE_REPORT"

# 局部修订：仅允许指定章节正文和第5张嵌入图片改变（分别授权）
"$DOCX_PYTHON" tools/docx/compare_docx.py "$BACKUP" "$OUTPUT" \
  --allow-section "5.1 从像素到空间坐标" --allow-figure 5 --report "$DIFF_REPORT"
# 全文重写改用 --report-only，审读删除项；退出码0不代表保持项全部通过。
```

`inspect_docx.py` 检查实际标题及顺序、表格、图片引用、源文/图片/输出 hash，排除代码块中的伪标题和伪图片；重复使用一张图不会误报。`verify_sources.py` 只证明引用与当前文件一致，不能代替实现逻辑审阅。`compare_docx.py` 检查正文/表格/公式文字、图像 hash 和页眉页脚文字，不能证明排版或图内文字正确。

`render_docx.py` 的三种合法状态：`passed`、`visual_qa_pending`、`visual_qa_failed`；`passed` 仅证明渲染成功，报告绑定本次 Word 和页面 hash，`page_review` 初始为 `pending`。必须实际查看最终保存文件的每一页后才能记录页面审阅通过，任何后续修改都要重新渲染并审阅。不能把候选文件的预览当作最终文件的验收。当前 OS/CPU/glibc/kernel 不匹配发行 recipe 时，结构验收通过仍可交付，但必须说明视觉验收待发行包补齐，禁止使用系统 LibreOffice 兜底。

## 5. 收尾

正式验收使用开源 resvg、受管 LibreOffice 和本地开放字体（如 Noto CJK）；微软 Office/WPS 仅作可选兼容检查，不是安装依赖或必过项。完成 LibreOffice 实际导入、PDF 导出、再保存与全部最终页面审阅后才能报告开源链路验收通过。

技术图可直接引用相对 SVG：`![图注](diagrams/route.svg)`。用受管 Python 的 `diagram_svg.py --validate-only` 预检；构建时 `--svg-dpi 192` 控制兼容 PNG 密度，`--svg-font <本地字体>` 可重复。受限子集、字形与资源预算不通过时明确失败。图源/PNG/hash/字体/renderer 均进入 manifest，最终仍需结构检查、受管渲染和逐页检查。时序图可用 `draw_sequence.py ... sequence.svg --palette ...` 输出矢量源。

默认 `diagram_status=raster_fallback`；实验 `--svg-mode dual` 保留 SVG+PNG，状态为 `native_vector_pending`、`consumer_compatibility=pending`。包内 `vector_preserved=true` 不代表目标 Office 已验证；实际消费者打开、缩放、导出 PDF、再保存和 fallback 结果须单独记录。

输出：DOCX/manifest 路径、主要内容或图示变化、结构检查、内容核对范围、渲染和逐页审阅状态。P1 还说明关联来源/Git baseline；静态源码核对与设备实测分别说明，不把缺失固件或闭源逻辑写成已确认事实。多文件请求逐份列出完成或未完成项，不能以其中两份通过代表整批完成。备份、图源和审计保留在目录中，正文按用户要求保持简要。
