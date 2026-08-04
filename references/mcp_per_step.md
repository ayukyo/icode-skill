# 步骤 × MCP 推荐矩阵（v2.2 强证据二元化）

> 每个 icode 步骤推荐使用的 MCP。v2.2 起消除 🟡"应该调"模糊地带，改为二元：🟢 必须调（强证据场景满足）/ ⚪ 不必调（不评估）。详见 [mcp_integration.md](mcp_integration.md)。

## 推荐级别语义（v2.2 二元化）

| 级别 | 符号 | 语义 | 触发条件 | 未调用的合规处理 |
|------|------|------|---------|-----------------|
| **必须调** | 🟢 | 强证据场景满足就**必须调用**（先实际调用一次，失败/空才能降级） | 强证据场景满足（见下表）+ MCP 在 `~/.claude.json` 注册 且 tool 在 deferred tools 列表 | **降级声明**：在思考块「MCP 调用」段写明降级原因（MCP 不可用 / LSP server 缺失 / 调用返回空）|
| **不必调** | ⚪ | 强证据场景不满足，**无需评估、无需声明** | 强证据场景不满足 | 无需说明 |

## v2.1 -> v2.2 变化（破兼容性变更）

**v2.1**（已弃用）：🟢 必须调 / 🟡 应该调 / ⚪ 不必调。🟡"应该调"语义模糊，AI 倾向"被动不调用"+ 补降级声明应付，实测仍只触发 sequential-thinking。
**v2.2**（当前）：🟢 必须调 / ⚪ 不必调。**消除 🟡**——强证据场景满足就 🟢 必调（执行步骤内嵌 + thinking_core gate 双保险），不满足就 ⚪ 完全不评估。

**为什么变**：v2.1 的 🟡 是降级口子——AI 把"应该调"解读为"可选"，配合"工程类型感知"段的降级表，5 个 MCP 全有合理降级理由，强制化变成"强制声明降级"。v2.2 二元化后，AI 没有模糊地带：要么必调，要么不评估。

## 强证据场景判定（v2.2 核心）

**判定时机**：每个步骤开始时（thinking_core gate + 执行步骤内嵌点），按以下场景判定每个 MCP 是否 🟢。**不满足强证据场景 = ⚪ = 不评估不声明**。

| MCP | 🟢 强证据场景（满足即必调） | ⚪ 否则 |
|-----|---------------------------|--------|
| **sequential-thinking** | 所有步骤（强制思考前置，已嵌入 thinking_core） | 无 |
| **serena** | log/plan/code/deepcheck/doc/review 步骤 **且** 工程有可索引源码（.py/.ts/.js/.jsx/.tsx/.vue/.c/.cpp/.h/.rs/.go/.java/.kt 等，非空非 demo-skeleton）**且** log 步骤额外要求：根因假设涉及代码符号/引用/持有链/跨文件调用时必调（绑定「代码事实验证门」，仅 Read 不算替代） | 其余步骤 / 无可索引源码 |
| **context7** | init/plan/code 步骤 **且** 需求或代码涉及第三方库（package.json/Cargo.toml/go.mod/requirements.txt/pom.xml/build.gradle 等声明依赖，且需求触及该库 API） | 其余步骤 / 不涉及第三方库 |
| **vision-bridge** | 任意步骤 **且** (a) 用户主动提供图片/截图/视频（会话中含媒体附件/路径，直接调） **或** (b) TB 缺陷源拉取的附件含视频/图片（`{ICODE_OUT_DIR}/tb_source/<ID>/` 下，**vision-bridge 可用则主动调**：视频先用 ffmpeg 本地提取关键帧再传图片帧给 vision-bridge 省钱——见 [steps/log.md](../steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」段） **或** (c) `/icode log` 本地日志目录含视频/图片文件（`find <log_dir> -type f \( -name '*.mp4' -o -name '*.mov' -o -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \)`，**vision-bridge 可用则主动调**，行为同 (b) 的 ffmpeg 抽帧流程） | vision-bridge 未安装 / `~/.claude/skills/icode/mcp/vision-bridge/config.json` 三件套未配齐 → 仅提示不主动调（防纯文字模型报错）；ffmpeg 不可用时降级为直接传视频（需用户确认，可能耗 API 额度） |
| **playwright** | deepcheck/audit 步骤 **且** 前端工程（含 .html/.jsx/.tsx/.vue 或 package.json 含 react/vue） | CLI/后端/嵌入式工程 |
| **memory** | init/plan 步骤 **且** 本工程历史工单数 ≥ 1（`~/.claude/icode_data/index.json` 中本 project_path 工单数 ≥ 1） | 新工程首个工单 / demo |
| **cheap-research** | init/log/doc/plan/review/code/deepcheck/audit/readme 步骤 **且** 走单闸门入选的 22 个子任务（长上下文压缩 / 历史检索 / 模板填充 / 结构化提取 / 代码事实审计 / 模式扫描 / 符号追溯 / 差异摘要 / 文件名生成 / 模板选择 / schema 迁移 / 模块识别 / project_id 解析 / 远程拉取） | **不接管决策**：3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案 / 用户对话一律不走；推理敏感度中等的"灰区"也不走（零灰区原则）；merge/install/list 无入选子任务 |

**判定执行**：
- serena/context7 的"可索引源码"/"第三方库"探测：步骤 1 plan 开始时 `ls` 顶层 + grep 依赖文件，结果写入 `01_plan.md` §1.5；**log 步骤开始时同样探测**（根因假设涉及代码时按相同判定走 serena 强证据场景），结果写入 `log_analysis.md §2.0`
- memory 的工单数探测：Read `~/.claude/icode_data/index.json` 按本工程 project_path 计数
- vision-bridge/playwright 的工程类型/媒体探测：按会话上下文 + 工程文件判定

## 通用前置（所有步骤必用，v2.4 去重）

> **所有步骤**必用 `sequential-thinking` 🟢（承载「强制思考前置」，**每步至少 3 步 + 每步对应该步骤声明的子项之一**）。详见 [references/thinking_core.md](../references/thinking_core.md)「通用流程」第 4 步。**该行为是默认常量，不在下方矩阵中重复标注**。

## 推荐矩阵（v2.2 二元化）

> 矩阵只标**除 sequential-thinking 外的**MCP 默认推荐；sequential-thinking 见上方「通用前置」。实际执行按上方「强证据场景判定」动态判定。强证据场景不满足时，即使下表标 🟢 也降为 ⚪。

| Step | serena | context7 | vision-bridge | playwright | memory | **cheap-research** |
|---|---|---|---|---|---|---|
| **0 init** | ⚪ | 🟢* | 🟢* | ⚪ | 🟢* | 🟢* |
| **0 log** | 🟢* | 🟢* | 🟢* | ⚪ | 🟢* | 🟢* |
| **doc** | 🟢* | ⚪ | 🟢* | ⚪ | ⚪ | 🟢* |
| **1 plan** | 🟢* | 🟢* | 🟢* | ⚪ | 🟢* | 🟢* |
| **2 review** | 🟢* | ⚪ | 🟢* | ⚪ | ⚪ | 🟢* |
| **3 merge** | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |
| **4 code** | 🟢* | 🟢* | 🟢* | ⚪ | ⚪ | 🟢* |
| **5 deepcheck** | 🟢* | ⚪ | 🟢* | 🟢* | ⚪ | 🟢* |
| **6 audit** | ⚪ | ⚪ | 🟢* | 🟢* | ⚪ | 🟢* |
| **7 readme** | ⚪ | ⚪ | 🟢* | ⚪ | ⚪ | 🟢* |
| **install/list** | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |
| **status** | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | 🟢* |

`🟢*` = 默认 🟢，但实际需满足强证据场景才必调（不满足降为 ⚪，无需声明）。

## 详细说明（强证据场景下的用途）

### 0 init（需求初稿对话）
- **context7**：库调研（"用 React 19 还是 18？"）——仅需求涉及第三方库时
- **vision-bridge**：识别用户给的设计图/截图——仅用户给图时
- **memory**：read_graph 查跨工单偏好——仅本工程有历史工单时


- **cheap-research**（🟢*）：现状盘点（`summarize` 压缩工程结构长文）+ 历史工单匹配（`retrieve_similar` 从全局索引筛相似工单）。**不接管决策**：需求点抽取 / 4 维度验证清单 / 链路图绘制走主会话（推理敏感）
### 0 log（日志根因分析）
- **context7**：库 API 行为查证——仅涉及第三方库行为时
- **vision-bridge**：TB 附件视频/图片主动分析 + 本地日志视频/图片分析——TB 拉取的附件含视频/图片时 **vision-bridge 可用则主动调**（视频先用 ffmpeg 本地提取关键帧再传图片帧省钱，详见 [steps/log.md](../steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」）；用户主动给截图时直接调；本地日志目录含视频/图片文件时扫目录后主动调。vision-bridge 不可用时仅提示附件清单不主动调（防纯文字模型报错）

- **serena**：根因涉及的代码符号/引用/持有链定位（`find_symbol` 定位定义 / `find_referencing_symbols` 找谁调用 / `find_implementations` 找实现 / `search_for_pattern` 模式检索）--根因假设涉及代码行为时**必调**（绑定 log 阶段3「代码事实验证门」，仅 Read 实读不算替代：Read 是文本层，serena 是语义符号层，互补非替代）。有可索引源码时必调；serena 不可用（未装/LSP 不支持该语言/无源码）降级 ripgrep/grep 并显式声明 `serena 不可用(<原因>)，降级 ripgrep/grep`

- **cheap-research**（🟢*）：长上下文压缩（log 阶段 0/1/2）+ 8.6 memory 沉淀 + TB 缺陷源拉取（`fetch_remote`）。**不接管决策**：阶段 3 链路图分析 / 阶段 4 根因假设 / 阶段 6+7 对抗分析 / 阶段 8 修复建议 / 追问机制一律不走（高风险子任务）

### doc（工程知识库生成）
- **serena**：理解代码结构（入口/API/IPC）——有可索引源码时，**比 Read 精准 10 倍**
- **vision-bridge**：截图分析——仅用户给图时
- **cheap-research**（🟢*）：项目代码事实审计（`audit_facts`） + 章节模板填充（`fill_template`）+ 进度输出（`fill_template`）+ 6 级模块识别（`scan_modules`）+ 增量判定（`scan_patterns`/`diff_summary`）+ 远程依赖 README 拉取（`fetch_remote` 拉模块仓库 README 作为模块文档参考输入）。**不接管决策**：意图识别走主会话（推理敏感度中等），把控"该写哪章"的决策

### 1 plan（拟定计划）
- **serena**：理解代码结构（哪些函数被谁调用）——有可索引源码时（**执行步骤 5.0 内嵌**）
- **context7**：库 API 核对——仅涉及第三方库时
- **vision-bridge**：识别截图——仅用户给图时
- **memory**：read_graph 查跨工单记忆——仅有历史工单时


- **cheap-research**（🟢*）：跨工程代码事实审计（`audit_facts` 抽取 README/CLAUDE.md/入口文件关键事实）+ 历史 ADR 检索（`retrieve_similar` 从全局索引找相似工单的 ADR+风险）+ schema 迁移（`apply_migration` 生成 ops 不执行）。**不接管决策**：4 维度设计态固化 / 风险评估 / 接口误用预审 / 端到端路径推演走主会话（推理敏感/中风险灰区不做）
### 2 review（审查）
- **serena**：依赖关系审查（"这个函数被哪些地方调用？"）——有可索引源码时
- **vision-bridge**：截图分析——仅用户给图时


- **cheap-research**（🟢*）：第 N 轮增量审查（`diff_summary` 摘要计划/代码差异）+ 审查输出压缩（`summarize` 压缩审查结果供 merge 消费）+ 维度结果结构化（`fill_template` 填审查维度模板）+ 历史相似 issue 检索（`retrieve_similar`）+ grep 模式扫描（`scan_patterns` 找 TODO/FIXME/重复模式）+ 引用追溯（`trace_refs` 找符号引用）。**不接管决策**：3 质疑者对抗验证 / 审查意见合成走主会话（高风险）
- **dedup 子阶段**：见 §2.5.7。**强证据** = serena 🟢 + cheap-research 🟢 + 函数数 ≥ 50 → 调 `mcp__serena__find_symbol` 抽函数 + `mcp__cheap-research__extract`（haiku 分类 + 高质量模型找 top 5 重复）。**降级**：函数数 < 50 / serena 不可用 / cheap-research 不可用 → 整个 §2.5.7 跳过

### 3 merge（合并审查意见）
- **sequential-thinking**：仅此（结构化合并审查点）

### 4 code（编码）
- **serena**：**game-changer**——按符号编辑、重命名引用追踪——有可索引源码时（**执行步骤内嵌**）
- **context7**：实时查库 API——仅涉及第三方库时
- **vision-bridge**：截图分析——仅用户给图时


- **cheap-research**（🟢*）：schema 迁移（`apply_migration` 生成 ops 不执行，主会话审核后手动执行）。**不接管决策**：关键设计 / 编码实施 / Code Review Fix 4 维度复检走主会话（推理敏感）。死代码清理 / 批量补全 / 格式化不入选（价值 < 3 ★）
### 5 deepcheck（复检）
- **serena**：找所有调用点评估 blast-radius——有可索引源码时（**执行步骤内嵌**）
- **playwright**：跑 E2E——仅前端工程时
- **vision-bridge**：截图分析——仅用户给图时


- **cheap-research**（🟢*）：Reverse 阶段原文对比（`diff_summary` 对比计划与代码差异）+ 阶段摘要压缩（`summarize` 压缩长审查输出）。**不接管决策**：Fixed/Free 阶段 / 3 质疑者对抗（A6）走主会话（高风险）
- **dedup 子阶段**：见 §9.4。**强证据** = serena 🟢 + cheap-research 🟢 + 函数数 ≥ 50 → 全量 dedup（5 阶段：抽取→分类→拆分→高质量模型逐类找重复→报告）。**降级**：函数数 < 50 / serena 不可用 / cheap-research 不可用 → 整个 §9.4 跳过。**复用**：检测 `dedup_categorized.json` 是否已由 §2 02_review 生成 → 复用避免重跑分类

### 6 audit（终审）
- **playwright**：真实 UI 验证——仅前端工程时
- **vision-bridge**：UI 截图分析——仅用户给图时


- **cheap-research**（🟢*）：计划vs代码差异摘要（`diff_summary` 对比计划与实现）+ 6.4 交付报告提示（`fill_template`）+ schema 状态汇总（`summarize`）+ 实现偏差备忘（`fill_template`）。**不接管决策**：6.1 终审报告裁决 / 6.2 强制修复 / 6.3 最终交付走主会话（高风险）
### 7 readme（交付报告）
- **vision-bridge**：附加 UI 截图——仅用户给图时


- **cheap-research**（🟢*）：文件名生成（`generate_filename`）+ 智能模板选择（`select_template` 功能/查BUG）+ 模板填充（`fill_template` 7 个段落）+ 已知限制检索（`retrieve_similar` 查历史 BUG 防重复）。**不接管决策**：风险章节提炼走主会话（推理敏感度中等，灰区不做）
### install / list
- **sequential-thinking**：仅此（install 装依赖；list 纯查询内置 index.json + 过滤 + 表格化，不需 cheap-research）

### status
- **sequential-thinking**：仅此
- **cheap-research**（🟢*）：`--scan-verdict` 批量扫描时用 `extract` 提取 00_init 末轮/06_audit 证伪信号（结构化抽取，低风险）。**不接管决策**：verdict 标注走主会话（用户决策）

## 调用覆盖率强制化规则（v2.2）

1. **产物文件不记录 MCP 调用信息**（v2.3 精简）：MCP 调用结果只进思考块「MCP 调用」段，不写入 01_plan.md / 02_review.md / 03_plan_final.md / 04_code_review_fix.md / 05_deepcheck.md / 06_audit.md / log_analysis.md / 00_init.md 等产物
2. **思考块每行**：MCP 名 + 实际调用结果（成功 / 降级 / 不适用）+ 证据
3. **🟢 MCP 未实际调用**（含未先尝试调用就标降级）= 反偷懒第 21 条违规
4. **⚪ MCP 无需记录**（强证据场景不满足，不评估不声明）
5. **🟢 MCP 未在思考块留下调用/降级记录** = 反偷懒第 21 条违规（审计 grep 思考块核查）

## 双保险机制（v2.2 新增）

🟢 MCP 由两层强制驱动，确保真实触发（治本"只触发 sequential-thinking"问题）：

1. **执行步骤内嵌**（A 层）：serena 在 plan/code/deepcheck/doc/review 的执行步骤主体里有独立的"第 N 步"调用指令（非末尾推荐表），AI 顺序执行必然走到——复制 sequential-thinking 的成功模式
2. **thinking_core MCP gate**（B 层）：强制思考前置流程里，思考块先列本步 🟢 MCP -> ToolSearch 取 schema -> 实际调用 -> 结果进思考块。覆盖 context7/memory/vision-bridge/playwright

两层任一触发即合规。serena 走 A 层（执行步骤内嵌），其余 🟢 MCP 走 B 层（thinking_core gate）。

**与 v2.1 的区别**：v2.1 只有末尾推荐表 + 产物记录段（形式强制），AI 可补声明应付；v2.2 把 🟢 MCP 写进执行步骤主体和思考流程（执行强制），AI 顺序执行必然真实调用。

## 降级路径

- **serena 不可用**（无 LSP / 未装）：Read + Grep 兜底，思考块标"serena 降级-无 LSP，用 Read+Grep 替代"
- **context7 不可用**：WebFetch 官方文档兜底，标降级
- **vision-bridge 不可用**：用户自负原生多模态能力，标降级
- **playwright 不可用**：Bash + curl 兜底（无 JS 渲染），标降级
- **memory 不可用**：本对话手动笔记兜底，标降级
- **cheap-research 不可用**：主会话 / 子代理走 `Agent(model="haiku")` 兜底（Claude 家族最便宜模型）。整体 token 节省幅度下降，但工作流不阻塞

**降级不是错误，但必须显式声明**（先实际调用一次，失败/空才能标降级）。
