# ICode — 全流程编码工作流（步骤 0 + 1~6，含日志根因分析入口 + 工程级知识库生成）

> English: [README.md](README.md) | 中文: 本文件

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为严格步骤，每步可单独调用，手动切换模型时操作更灵活。

- **入口命令（可选）**：`/icode log` 日志根因分析（领域无关）→ 转修复需求；`/icode init` 需求初稿对话
- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 特性

- **闭环交付**：(可选) 需求初稿 → 计划 → 审查 → 定稿 → 编码 → 复检 → 终审，每步可独立调用，主会话执行、不切换模型
- **双模式**：`/icode start` 全流程（多轮审查 + 对抗验证）/ `/icode fast` 精简（1 轮无对抗，约 65% 耗时），自动串联步骤 1→6
- **防偷懒质量门**：三阶段复检（Reverse/Fixed/Free）、Plan 断言实证验证、ADR 决策记录、对抗验证（独立质疑者——证据不足不确认、诚实降级不伪造共识）
- **跨工程历史检索**：init/log/plan/start 启动时自动检索相似历史工单按命令分流注入，只进会话、不污染工程产物；**verdict 防误导注入**——已证伪/被取代的工单注入的是"陷阱结论"而非 ADR，防止误导新工作
- **强制阻断边界矩阵**：检查项按 L1 致命 / L2 关键 / L3 重要 / L4 参考四级定义"是否阻断流程"（L1 报错退出、L2/L3 警告+记 metadata+继续、L4 柔性提示），各步骤头部声明检查项，统一"内容正确 ≠ 机制合规"
- **工程级知识库**（`/icode doc`）：生成全局工程知识库（跨仓库跨分支共享，模块文档只生成一次复用），供段零自动检索注入，开发时无需手动告知参考文档
- **工程工单备份**（`/icode bak`）：把工程整个 `.icode_output/`（工单 + `.debug/` 调试孪生 + limit.local + ppt）快照到全局 `~/.claude/icode_data/project_backup/`，可多次备份（rsync 硬链接去重）。**删工程前先跑**——工程被删后，历史检索仍可从备份读完整工单产物（工程优先 → 备份兜底），`/icode list` 显示 `[path_gone→backup]`；对已关闭 worktree 工单（`project_path` 失效但 `archive_path` 有效）显示 `[path_gone→archive]`（归档与备份均有效时 `[path_gone→archive+backup]`）
- **防重复注入**：历史检索与工程文档检索共用缓存去重，避免同开发链路重复注入
- **防偷懒强化**：步骤5/6 强制 Read 确认行 + 证据 file:line + 自检清单，步骤2 对抗强制 Agent ID
- **两个可选入口**：`/icode log` 日志根因分析（先基线检查再对抗分析，领域无关）→ 转修复需求；`/icode init` 多轮需求初稿对话 → `00_init.md`
- **产物与状态管理**：统一收纳在 `.icode_output/.icode_output_N/`，`.ico_metadata.json` 记录状态/代码文件，支持跨会话恢复与断点续跑
- **决策锚点**：步骤间以精简决策摘要（`.decision_anchors.json`）传递上下文——省 token、保持推理连续性
- **可选 TB 缺陷源**：`/icode log` 零散输入含 Teambition 项目 URL 或 `<LIB>-<NUM>` 时，可选拉取缺陷单的标题/描述/评论/日志附件作为分析输入（多项目文本配置，仅拉取分析、不回写 TB；无 TB 引用时走纯本地日志路径，行为不变）
- **可选钉钉文档源**：入口（`/icode init` / `log` / `plan` / `start`）与 patch 阶段0 零散输入含钉钉分享链接（alidocs.dingtalk.com）时，可选拉取文档/钉盘文件作为需求与参考资料输入（仅拉取、不回写钉钉；原生格式需用户在钉钉 UI 导出；无钉钉引用时行为不变）
- **能力感知视觉理解**：ICODE 先走确定性文本/结构提取，再按 `auto | native | bridge | dual | text_only` 路由视觉区域。宿主明确证明当前 GPT 等会话模型支持多模态时保留原生视觉；纯文本或能力未知时才由 `mcp/vision-bridge` 补盲；高风险证据可双通道独立复核，分歧保持未决。多图、视频帧和页面 tile 受单消息硬上限保护，超限时串行分批并最终只聚合文本。详见 [媒体路由真源](references/media_routing.md)。

## 安装

### 开源统一安装入口

把源码 clone 到普通目录，再运行仓库顶层安装器。它会一次性安装 ICODE、[`skill-packs/manifest.json`](skill-packs/manifest.json) 声明的全部共享技能和 MCP。默认只安装 Claude（`--client claude`）；Claude Code 与 Codex 双端使用 `--client all`。

```bash
git clone https://github.com/ayukyo/icode-skill ~/icode-skill
cd ~/icode-skill
./install.sh --client all
```

`./install.sh --dry-run --client all` 可做零写入预检；`--skip-mcp` 只安装 ICODE 和共享技能。ICODE 本体发布到 `<skills-root>/icode/`，每个共享技能从不可发现的源模板生成到顶层 `<skills-root>/<skill-name>/SKILL.md`，不会再出现嵌套同名技能。

安装器通过所有权标记管理共享技能：内容一致的旧副本可以无损接管；内容不同的未托管的同名技能会在任何宿主写入前拒绝，不会静默覆盖。运行配置和缓存继续保留。

证据摄取、邮件导出接入（`tools/email_intake.py`）、技术文档文件接入（`tools/document_intake.py`）、媒体路由/证据记录（`tools/media_router.py`）、项目内 debug catalog、三基线解析、验证债务、多仓 handoff 矩阵和 `/icode learn` 属于 **ICODE 内置工具/步骤**：`--client all` 会随 ICODE 本体同时复制到 Claude Code 与 Codex，不作为独立项写入 `skill-packs/manifest.json`。现有 16 个跨项目共享 Skill（含 `email-evidence-intake`、`technical-document-intake`、`hardware-spec-contract-audit`、`schematic-interface-audit`、`mcu-hardware-software-contract-audit` 及嵌入式/摄像头能力）仍由 manifest 独立安装，并继续使用 ownership/hash 冲突保护。

历史上的 Claude skills 目录直装方式继续兼容。Claude Code 发现 ICODE 后，执行同一个统一命令：

```bash
git clone https://github.com/ayukyo/icode-skill ~/.claude/skills/icode
/icode install --client all
```

### 开发者更新命令

仓库开发者只想把当前 checkout 发布到本机、但不安装 MCP 时，使用：

```bash
./scripts/sync-to-global.sh --dry-run --client all
./scripts/sync-to-global.sh --apply --client all
```

[`mcp/workflow-gate/skill-routes.json`](mcp/workflow-gate/skill-routes.json) 继续声明 ICODE 到共享技能的触发条件与输入/输出合同。可选 MCP 不可用时工作流仍可显式降级，但安装失败不会伪报成功。

嵌入式、摄像头、邮件、技术文档、原理图和 MCU 软硬件契约分析继续使用同一组公开工作流命令。可选的 `verification_profile` 与工单内 `embedded_baseline.json` 可由只读计划工具转换为硬件场景和指标阈值；本地 PDF/Office/图片/文本/7z 语料先由 `technical-document-intake` 验证真实类型、结构、归档风险、重复/变体和可读覆盖，再按需路由规格、原理图或 MCU 契约审计。工具不执行 baseline、邮件、文档、SDK 或工具包中的字符串/程序，受保护容器须授权导出，硬件写入、测量或破坏性故障注入仍须显式授权。

## 可选数据源：只读邮件证据

现有 `init`、`log`、`plan`、`doc`、`deepcheck`、`audit` 或 `verify` 请求包含限定范围的网页邮件链接、线程、`.eml` 或 `.msg` 时，`email-evidence-intake` 先完成批量采集、消息/线程身份、转发引用分段、资源预检、附件路由、分析和完整性门禁，再复用已有媒体、表格、技术文档、日志/时间线、原理图、跨层、来源和现场验证能力；不新增公开命令。显式网页邮件链接默认复用已经登录的浏览器：用户只需登录一次并提供精确链接，ICODE 严格限定到目标邮件阅读窗，通过网页已有入口把邮件原文和所需附件下载到受控证据根，不要求 IMAP 配置。

可选的 `icode-mail-observe` 仅用于无人值守、邮箱范围搜索或没有浏览器的服务器环境，不是普通网页邮件分析的前置条件。其 IMAP 路径只以 `readonly=True` 打开 allowlist 目录，以 `BODY.PEEK[]` 取信，不提供发送、回复、删除、移动、复制、标记已读或原始命令。唯一受管写入是把一个明确选中的附件保存到配置的证据根，并检查路径、大小、hash 和可执行文件。没有可用浏览器时可导出 `.eml`/`.msg`，由 `tools/email_intake.py` 确定性离线接入；用户未选择安装 `extract-msg` 时，Outlook `.msg` 会明确返回可选解析器缺口。接入阶段不加载远程图片/链接；企业邮件及派生内容默认不发给 `cheap-research` 的远程能力。

## 可选增强：图片/视频理解

视觉理解是可选增强，**未装不影响主工作流**。ICODE 不再把“vision-bridge 已安装”当成优先级依据：GPT 等宿主明确证明原生多模态能力时使用原生视觉；纯文本/能力未知会话使用 bridge；两者均无时只做文本/OCR/元数据并明确视觉缺口。不要通过试传图片探测未知模型能力。

### 安装 vision-bridge

```bash
cd ~/.claude/skills/icode/mcp/vision-bridge
./install.sh                          # 自动:创 venv + 装依赖 + 注册到 ~/.claude.json
# 编辑生成的 config.json 填你的 base_url / api_key / model
# 重启 Claude Code 即生效
```

### 不绑任何平台

任何 OpenAI Chat Completions 兼容端点都能用——你用什么平台就填什么 base_url 和 model，**没有任何推荐值**。

### 缺配置时怎么办？

如果 vision-bridge 装了但 `config.json` 还没填三件套（`base_url` / `api_key` / `model`），路由器把 bridge 标为不可用；只有宿主已明确证明原生多模态时才走 native，否则降级 `text_only` 并记录未分析视觉区域。`declared_capabilities` 与 `quality_profile` 只填写实际评测过的能力，未知就留空。

详见 [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md)。

## 可选增强：便宜 LLM 推理（cheap-research）

为降低主会话的 token 消耗，cheap-research 把"长上下文压缩 / 历史检索 / 模板填充 / 结构化提取"等子任务**转交便宜模型**（仍走 `mcp__cheap-research__*` 工具）。**不接管决策**：3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不交给 cheap-research。

当前共 15 个工具：6 个本地确定性工具、1 个不可信公网抓取工具、8 个可选 LLM 转换工具。会话先调用只读 `describe_capabilities` 获取不含密钥的能力画像，再按 capability 路由；技术文档、视觉页、原理图和硬件契约仍由 document-intake / media-router / 对应 SKILL 处理，cheap-research 只消费带来源回指的文本候选。企业邮件正文、地址、表格、图片、附件、日志及派生摘要默认不得进入远程 `fetch`/`llm` 能力；没有明确 provider 与披露范围授权时，只能研究与邮件内容分离的公开主题。

**入选条件**（单闸门）：价值 ≥ 3 ★ + 低风险，且是**各步骤正文有真实调用点**的子任务（TB 评论预提取 / 远程 README 拉取 / dedup 分类找重复 / 审查输出压缩 / 跨轮汇总 / 差异摘要 / 仓库事实候选等，覆盖 log / doc / review / merge / deepcheck / audit / patch 步骤）；init / plan / code / status / readme 无正文执行点（走确定性机制），标 ⚪。完整清单见 [mcp/cheap-research/tools_manifest.json](mcp/cheap-research/tools_manifest.json)。

### 安装 cheap-research

```bash
cd ~/.claude/skills/icode/mcp/cheap-research
./install.sh                          # 自动:创 venv + 装依赖 + 注册到 ~/.claude.json
# 编辑生成的 config.json 填你的 base_url / api_key / model
# 重启 Claude Code 即生效
```

### 跟 vision-bridge 一样的不锁平台

任何 OpenAI Chat Completions 兼容端点都能用——你用什么平台就填什么 base_url 和 model，**没有任何推荐值**。本地 Ollama 也是 provider 之一（`provider: local_ollama`）。

### 缺配置时降级

如果 cheap-research 装了但 `config.json` 还没填三件套，工具调用会返回 fallback 提示 dict，session 模型按默认会话模型处理——**等同于未装 cheap-research 的行为**。不报错、不阻塞。

### 执行门覆盖率检查（coverage）

每个 cheap-research 调用点都有机器可读的 gate（`mcp/cheap-research/gates.json`，阈值唯一真源）与工单内运行痕迹（`{ICODE_OUT_DIR}/.mcp_gate_trace.jsonl`）。可用校验器确认每个 eligible gate 是否真实履行（或带结构化证据合法跳过）：

```bash
python3 tools/lint_mcp_coverage.py <out_dir>              # Markdown 报告，退出码 0/1/2
python3 tools/lint_mcp_coverage.py <out_dir> --json       # 机器可读报告
python3 tools/lint_mcp_coverage.py <out_dir> --step review --strict
```

详见 [mcp/cheap-research/README.md](mcp/cheap-research/README.md)。

### 其他 11 个 MCP（4 个通用 + 7 个 ICODE 本地服务）

除 vision-bridge / cheap-research 外，`/icode install` 还会安装 4 个通用工作流工具和 7 个免 Key 的 ICODE 本地服务：

- **sequential-thinking**——分级思考 reasoning gate 的 L2/L3 载体：复杂修改/重构/重设计前结构化思考 3~5 步（L0/L1 步骤不调用；每步先列本步必调 MCP 再实际调用）
- **memory**——跨工程知识图谱（`mcp__memory__read_graph`），历史检索/段零注入时唤起，跨会话回看过去工单与工程文档
- **context7**——第三方库文档实时查询，init/plan/code 且需求涉及第三方库时调用
- **playwright**——浏览器自动化，deepcheck/audit + 前端工程时调用
- **icode-evidence**——文件 SHA-256、带行号回读、日志时间线和文档语料清单
- **icode-workspace**——多 Git 根、worktree、构建输入和制品来源观测，不执行 merge/commit/push
- **icode-device-observe**——命名 SSH/ADB/fixture profile 的固定只读设备检查，无任意命令或设备写操作
- **icode-mcp-health**——安装/升级/CI 使用的 MCP manifest、入口与敏感字段健康检查
- **icode-mcp-policy**——现有步骤到 server/tool/operation 的默认拒绝路由真源
- **icode-local-index**——大型源码/日志/文档的可重建 SQLite 全文索引，命中仍须回读原文件
- **icode-mail-observe**——可选的无人值守只读 IMAP 邮件/线程接入；普通网页邮件链接默认使用已登录浏览器

这 7 个本地服务不新增 `/icode` 公开命令，机器路由见 [mcp/icode-mcp-policy/policy.json](mcp/icode-mcp-policy/policy.json)。每个 MCP 都有显式强证据触发条件 + 声明的优雅降级路径（详见 [SKILL.md「MCP 工具集」](SKILL.md)）；缺失任一都不阻断工作流。

## 快速开始

```bash
# 一步走完全流程
/icode start 实现一个功能模块

# 或者分步执行
/icode plan 实现一个功能模块   # 步骤1：拟定计划
/icode review                          # 步骤2：专项审查（软上限3轮，仍有问题时自动延长）
/icode review 5                        # 步骤2：指定5轮审查
/icode merge                           # 步骤3：合并定稿
/icode code                            # 步骤4：编码实施
/icode deepcheck                       # 步骤5：循环复检
/icode audit                           # 步骤6：终极终审

# 精简全流程（fast 模式：单文件/小改动场景，耗时约为全流程 65%）
/icode fast 给 calc.c 增加 isqrt 函数   # plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit

# 工程级知识库生成（独立步骤，任意时刻可跑，不参与 1~6 流程）
/icode doc                              # 无描述→全局扫描各工程知识库 stale 状态
/icode doc myproject                    # 检查该工程更新（增量优先：git diff 命中章节才重生成）
/icode doc 重新生成 myproject           # 全量重生成（触发确认门，保护手动编辑）
# 生成后，后续 /icode init|log|plan|start|fast 启动时段零自动检索注入相关章节

# 工程工单备份（独立步骤：把工程 .icode_output/ 快照到全局，删工程前安全网，可多次备份）
/icode bak                              # 备份当前工程全部工单（快照 + 索引写 backup_path）
/icode bak --project ~/work/myproj      # 备份指定工程（可多次，每次新快照，硬链接去重）

# PPT 生成（独立交付步骤：把 icode 产物/知识库转成 .pptx）
/icode ppt                                # 默认：最新工单→本次功能开发 PPT（有 log 入口→本次BUG修复）
/icode ppt 项目                            # 项目全景 PPT（内容源：project_docs 工程知识库 + 仓库结构）
/icode ppt 模块 数据采集                        # 指定模块 PPT（内容源：模块 doc 章节）
/icode ppt 本次BUG修复                      # log 根因 + 08_patch + 验证→查BUG PPT
# 前置：pip install python-pptx（必需）；LibreOffice+poppler 可选（渲染 PNG 自检）
# 产出：<工程根>/.icode_output/ppt/xxx.pptx；内置模板非商业授权（tools/ppt/NOTICE）

# 需求不明确时，先讨论再进入流程
/icode init 实现数据录制功能              # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6

# 任意入口 opt-in 用 worktree 隔离（与其它参数/flag 共存；不传则默认原地，不弹问）
/icode start --worktree 实现一个功能模块   # 全流程 + worktree 隔离
/icode fast --worktree 实现一个功能模块    # fast 模式 + worktree 隔离
/icode plan --worktree 实现一个功能模块    # 仅步骤1 + worktree 隔离
/icode init --worktree 实现数据录制功能            # 步骤0 + worktree 隔离
/icode log --worktree ~/work/log/服务异常 "启动后无响应"  # log + worktree 隔离
# 触发方式也支持自然语言：「用 worktree 隔离做」「走 worktree」「独立分支做」等
# 反向声明后置优先：「别用 worktree」「不要 worktree 隔离」「普通做就行」可覆盖前置触发
# AI 入口输出一行状态：「▶ worktree 隔离：即将启用 → ...」/「▶ worktree 隔离：未启用」
# 工程级反向开关：`/icode limit worktree 强制禁止` 红线命中 → 阻止触发（提示一次后回退原地）
# 新建 worktree 基于主仓当前分支的远程跟踪（@{u}）创建 + 自动 upstream——git status/pull/merge 方向始终正确
# worktree 生命周期（创建之后）：
/icode worktree --update    # 迁移活动实现根到基于最新远程基线的新 checkout（自动跟踪上游）
/icode worktree --close     # 你已 commit/push/merge 后：核验在线证据 + 安全清理 + 记录基线
/icode worktree --reopen    # 已 close 工单恢复：在最新基线上重建活动 checkout（之后 /icode patch）
/icode worktree --merge         # 刷新线上目标、全仓冲突预检、安全合并并复检；绝不自动 commit/push

# 从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/服务异常 "启动后无响应"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md + 对外简报 log_problem_brief.md（对外表达按统一契约：归因分级/角色澄清/修复状态明确）
# ... 对抗分析收敛后根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
```

## 可选：从 Teambition 拉缺陷单日志分析

`/icode log` 的零散输入含 Teambition 项目 URL 或 `<LIB>-<NUM>`（如 `DEMO-26`）时，可选自动拉取缺陷单的标题/描述/评论/日志附件作为分析输入；详见 [SKILL.md「使用流程示例」段（方式 D2）](SKILL.md)。配置（多项目 + cookie）详见 `~/.claude/skills/icode/tools/tb/README.md`。另支持**批量分析所有"打开/未完成"缺陷单**（零散输入含"分析所有TB"类意图时触发，按真实任务流状态名过滤、不能用 isDone），详见 [SKILL.md「方式 D3」](SKILL.md)。

另支持**定时增量监控**（`tools/tb/scripts/tb_watch.py`）：周期轮询一个或多个项目的"打开/未完成"单（按单号倒序），检测到某单有新增评论/附件/状态变化时自动拉起 claude 无头会话做**完整 `/icode log --debug` 深度分析**（下载解压日志附件做日志实证根因分析；产物落 `{工程}/.icode_output/.debug/`、不写全局索引；首次出现的单自动建基线，每轮一条）。配置为 JSON 文件、**最小只需填项目 URL**；每轮把"打开/未完成单 + 分析最新状态"检索报告写到 `{工程}/.icode_output/tb_watch_report.md`，**且每次触发分析完成后立即刷新**（该单刚建基线/完成增量后不再标"待新建"）；`tb_watch_ctl.sh start` 还会一并拉起**网页只读查看服务**（配置 `web` 段，默认 `:8000`，md 渲染成 HTML），同事在局域网浏览器即可查看报告与各单简报，无需 SMB 账号。配置 / 启动 / 停止 / 风险详见 [tools/tb/README.md](tools/tb/README.md)「定时增量监控」节。

## 可选：从钉钉文档/钉盘拉资料

入口（`/icode init` / `log` / `plan` / `start`）与 patch 阶段0 零散输入含钉钉分享链接（`alidocs.dingtalk.com` / `/i/nodes/{token}`）时，可选自动拉取文档/钉盘文件到 `dingtalk_source/` 作为需求与参考资料输入；详见 [SKILL.md「使用流程示例」段（方式 H）](SKILL.md)。前置：Chrome 已登录钉钉文档 + `pip install browser_cookie3`；详见 `~/.claude/skills/icode/tools/dingtalk/README.md`。

## 命令一览

| 命令 | 功能 |
| ---- | ---- |
| `/icode help` | 帮助：输出使用流程示例 |
| `/icode log [零散信息...]` | 可选入口：确定性证据清单 + debug 本地复用 + 逐仓现场基线 → 日志根因分析 → 修复需求 `00_init.md` |
| `/icode init [<粗略需求>]` | 可选步骤 0：多轮对话产出需求初稿 `00_init.md` |
| `/icode start <需求>` | 全流程：创建/复用目录 → 步骤 1→6 |
| `/icode fast <需求>` | 精简全流程：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit（耗时约 65%） |
| `/icode plan <需求>` | 仅步骤 1：拟定项目计划 |
| `/icode review [N]` | 仅步骤 2：专项审查计划（N=软上限轮数，默认 3） |
| `/icode merge` | 仅步骤 3：合并审查意见定稿 |
| `/icode code` | 仅步骤 4：落地编码实施 |
| `/icode deepcheck` | 仅步骤 5：三阶段递进复检（Reverse → Fixed → Free） |
| `/icode audit` | 仅步骤 6：终极终审 + 统一修复（产出 `06_audit.md`） |
| `/icode readme` | 可选步骤 7：一次生成两份——交付报告（给自己看，完整档案）+ 跨领域简报（`_brief.md`，给其它模块研发/测试/产品看，含必要代码，较简略；对外表达按统一契约） |
| `/icode patch [问题或新需求]` | 追加修改（独立步骤）：主流程后/中途继续改——测试发现问题 / 新需求，在既有工单上打补丁。轻量四段式（重审现状→增量计划→最小实施→反向复检），靠磁盘产物重载上下文（换会话可继续），产出 `08_patch.md` 追加式；可选 `--listen` 自动进行实机部署监听；先配 `~/.claude/icode_data/device_config/<project_id>.json`，模板 `templates/device_config.json.template`，单文件多连接 adb/ssh/串口） |
| `/icode verify [--deploy\|--listen\|--test\|--reuse]` / `/icode verify --plan [--ticket <id>]` | 实机验证或只读生成剩余验证单元计划；plan 不记录 verification run，两种模式都不自动升级 delivery_verdict |
| `/icode learn [--project <path>] [--ticket <id>] [--since <ISO-8601>]` | 基于项目内真实 skill-run 观测生成学习报告，分类复用/组合/增强/新建/工具化/no-action；本步骤不直接创建或发布 Skill |
| `/icode doc [自然语言]` | 工程级知识库生成（独立步骤）：扫描代码特征生成全局知识库章节，供段零自动检索注入 |
| `/icode limit [自然语言]` | 项目约束红线（独立步骤）：定义和维护本工程的红线/约束/禁区。主存全局 + 单 checkout 覆盖（自动 gitignore），追加式演进。plan 步骤引用作为硬基线 |
| `/icode ppt [自然语言]` | PPT 生成（独立交付步骤）：自然语言 → 真实 `.pptx`，4 类场景——**项目 / 模块 / 本次功能开发 / 本次BUG修复**；内容源为 icode 产物/知识库（禁止编造），内置 16 套模板（`tools/ppt/templates/`，AI 先筛 2-3 个风格匹配候选、由用户挑选；也可直接点名模板），产出 `<工程根>/.icode_output/ppt/`（不放进工单目录）可回溯；依赖 python-pptx（必需），LibreOffice+poppler 可选（PNG 预览自检）；内置模板非商业授权（见 `tools/ppt/NOTICE`） |
| `/icode status [--pending]` | 查询当前工单状态，或只读生成跨工单验证债务报告（`--verdict` 仍是显式标注模式） |
| `/icode list [关键词]` | 跨工程工单查找（纯只读） |
| `/icode worktree --update [--target <ref>]` | worktree 生命周期（独立步骤）：把活动实现根受控迁移到基于最新/指定基线的新 checkout——11 阶段状态机，失败保留旧活动根，可中断恢复 + 幂等。**换基线必须走本命令**（禁止静默改指针）；多业务子仓按整体事务处理 |
| `/icode worktree --close` | worktree 生命周期（独立步骤）：你已自行 commit/push/merge 后的本地收敛——核验在线证据 → 置 submitted → 安全清理 checkout → 记录 `submitted_baseline`。不替 commit/push，不删未提交唯一代码/未归档唯一产物，幂等 |
| `/icode worktree --reopen [--target <ref>]` | worktree 生命周期（独立步骤）：已 close 的 completed 工单显式恢复——在最新在线基线上创建新活动 checkout（不新建 ticket、保留 patch 历史）。**已 close 工单必须先 reopen 再 patch** |
| `/icode worktree --merge` | worktree 生命周期：逐仓刷新契约线上目标，全仓预检通过后安全快进或留下无冲突未提交 merge，再执行复检；不自动 commit、不 push |

> 完整命令一览（含「创建目录？」列 + 复用规则 + `--verdict`/`--scan` 等参数详解）见 [SKILL.md「调用命令」段](SKILL.md)。

## 执行方式 / 目录结构 / 工作流程

执行方式（主会话 + 不主动切换模型）+ 目录结构（含 `.icode_output_N/` 产物收纳）+ 工作流程（步骤 1→6 数据流图 + fast 模式分支）详见 [SKILL.md「通用规则」段](SKILL.md)。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

## DEMO（用于测试 icode 流程）

`demo/` 是一个最小 C 计算器工程（`calc.h`/`calc.c`/`main.c`/`Makefile`），**专门用来端到端测试 icode 工作流**——五种调用方式（A 全流程 / B 分步 / C init→start / D log→start / E fast 精简）都可在此工程里真实跑通：步骤1 计划、步骤4 编码、步骤5 复检、步骤6 编译验证都有真实代码可操作。

```bash
cd demo && make && ./calc_demo   # 确认基线可编译可运行
```

测试示例需求：

- **方式A**： `cd demo && /icode start 给计算器增加取模和幂运算功能，并补全整数溢出检查`
- **方式B（分步）**： `cd demo && /icode plan 给计算器增加 isqrt 函数` 然后逐步 `/icode review` `/icode merge` `/icode code` `/icode deepcheck` `/icode audit`
- **方式C（先 init 后 start）**： `cd demo && /icode init 计算器加新功能`（多轮对话澄清需求）→ `/icode start`
- **方式D（先 log 后 start）**： `cd demo && /icode log <log路径> "症状描述"` → 产出根因 + 修复需求 → `/icode start`
- **方式E（fast 精简）**： `cd demo && /icode fast 给 calc.c 增加 isqrt 函数`（耗时约 65%）
