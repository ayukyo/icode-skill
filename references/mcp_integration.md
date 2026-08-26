# MCP 工具集成与降级路径

> icode 工作流可调用 6 个 MCP（`/icode install` 一键安装）。**用户可能不装全部**，每个 MCP 都是**可选 + 降级**的。
>
> 安装入口：`/icode install`（详见 [steps/install.md](../steps/install.md)）
>
> 步骤 × MCP 推荐矩阵：[mcp_per_step.md](mcp_per_step.md)
>
> **二元化**：推荐级别为 🟢 必须调 / ⚪ 不必调（无 🟡），按 [mcp_per_step.md](mcp_per_step.md)「强证据场景判定」执行。本文档的「强证据 + 降级路径」适用，下文各 MCP 的「触发场景」即强证据场景。

## 判定 MCP 是否可用

按 [thinking_core.md](thinking_core.md) 的"强证据"逻辑（**注册 且 可调用 双条件同时满足才视为可用**；证据 A=注册为必要条件，证据 B=可调用为充分条件——B 成立必然 A 成立，但仅 A 成立（已注册未连接/未暴露）不算可用）：

- **证据 A（强证据）**：MCP 在**当前宿主**注册——Claude Code = `~/.claude.json` 的 `mcpServers.<name>` 段存在；Codex = `codex mcp list` 含 `<name>`。跨宿主双注册已支持（`/icode install --client all`），**宿主不同注册证据互不替代**（当前宿主没注册 = 证据 A 不成立，即使另一宿主已注册）
- **证据 B（强证据）**：工具可在当前会话**直接调用**——工具列表直接可见（完整 schema，按语义识别：标准 `mcp__<name>__<tool>` 或代理前缀 `__<proxy>_<tool>` 形态）或 ToolSearch 可取 schema（不可见时按 [thinking_core.md](thinking_core.md) 第 0 判据）

**强证据不存在 → 走降级路径**。**本文档路径：可装可降级，不阻塞流程。**

---

## 6 个 MCP 的强证据 + 降级路径

### ① sequential-thinking（**必装**）

- **强证据**：`mcp__sequential-thinking__sequentialthinking`（至少 3 步）
- **降级**：`### 结构化思考` 文字块（每步骤强制思考不可省）
- **触发场景**：**所有步骤**（强制思考前置）
- **当前状态**：已装

### ② vision-bridge（**推荐装，需配三件套**）

- **强证据**：`~/.claude.json` 的 `mcpServers.vision-bridge` 段存在 + `config.json` 三件套（base_url/api_key/model）已填
- **强证据满足**：`mcp__vision-bridge__analyze_media(media_path, prompt)` 返回文本，**优先用 MCP 工具**
- **降级**（没装 / 装了没填三件套）：AI 不替用户判断原生能力
  - 原生支不支持图片/视频 **视具体 session 模型而定**（Opus/Sonnet 一般支持，Haiku 可能部分支持）
  - **用户自己把握**原生能力是否够用；AI 不假装"可以原生处理"
  - 不报错、不阻塞
- **触发场景**：涉及图片/视频/UI 截图（用户主动提供时直接调）**或 TB 缺陷源拉取的附件含视频/图片**（`{ICODE_OUT_DIR}/tb_source/<ID>/` 下）—— vision-bridge 可用则主动调（视频先用 ffmpeg 本地提取关键帧省钱，详见 [steps/log.md](../steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」）；vision-bridge 不可用时仅提示附件清单不主动调（防纯文字模型报错）
- **当前状态**：已装（用户在 `config.json` 填三件套后可用）

### ③ memory（推荐）

- **强证据**：`mcp__memory__create_entities` / `mcp__memory__search_nodes` / `mcp__memory__read_graph`
- **降级**：本对话内手动维护笔记（`## 记忆` 段落），或写到 `~/.claude/icode_data/memory.md`
- **触发场景**：跨工单偏好（"用户偏好 NoSQL"）、项目特性（"用 gRPC v3"）
- **token 性价比**：中（需要工程化使用才收益大）

### ④ context7（推荐）

- **强证据**：`mcp__context7__resolve-library-id` + `mcp__context7__query-docs`
- **降级**：WebFetch `https://<library>.readthedocs.io` 或官方文档
- **触发场景**：步骤 0 init（库调研）、步骤 1 plan（API 核对）、步骤 4 code（实时查 API）
- **token 性价比**：高（解决训练数据过时问题）

### ⑤ playwright（仅前端项目）

- **强证据**：`mcp__playwright__browser_navigate` / `mcp__playwright__browser_take_screenshot` / `mcp__playwright__browser_click` / `mcp__playwright__browser_evaluate`
- **降级**：Bash + `curl <url>`（无 JS 渲染、不支持交互）
- **触发场景**：步骤 5 deepcheck E2E、步骤 6 audit UI 验证
- **⚠️ token 警告**：24 个工具 schema 永久加载，**非前端项目慎装**
- **当前状态**：工程内置（用户决策：保留）；包名 `@playwright/mcp`（原 `@microsoft/playwright-mcp` 已 404）

### ⑥ cheap-research（**可选 · 降本场景**）

- **强证据**：`~/.claude.json` 的 `mcpServers.cheap-research` 段存在 + `config.json` 三件套（base_url/api_key/model）已填
- **强证据满足**：`mcp__cheap-research__summarize(text)` / `__retrieve_similar(query, candidates)` / `__fill_template(template, data)` / `__extract(text, schema)` / `__propose_repo_facts(repo_path)` 等 14 工具返回结构化 dict，**子代理优先用 MCP 工具**
- **⚠️ 分能力闸门（v1.1）**：14 工具分三类 capability，**不能整体按三件套判定**——
  - `local`（6 个，不依赖 LLM provider）：`scan_patterns` / `trace_refs` / `validate_migration_ops` / `parse_project_id` / `scan_modules`（`fetch_remote` 归 fetch）——**provider 未配置也照常可用**
  - `llm`（8 个，必须 provider 可用）：`summarize` / `retrieve_similar` / `fill_template` / `extract` / `propose_repo_facts` / `diff_summary` / `generate_filename` / `select_template`
  - 真源：[mcp/cheap-research/tools_manifest.json](../mcp/cheap-research/tools_manifest.json)
- **降级**（没装 / 装了没填三件套）：主会话 / 子代理走 `Agent(model="haiku")` 兜底（方案 A），不阻塞主流程。**子代理兜底时按 [subagent_spawn_wait.md](subagent_spawn_wait.md) 通用契约等待**（后台 spawn + `TaskOutput` 阻塞等 + 20 分钟墙钟硬截止，禁止裸同步 spawn / 被动等通知 / 无限等待）
- **触发场景**：以各步骤**正文执行点**为真源（推荐表仅声明、正文无调用的不算入选）——log 阶段2 TB 评论预提取（`extract`，评论 ≥ 8 条）、doc 远程模块 README 拉取（`fetch_remote`）、review dedup 分类/找重复（`extract`）+ 审查输出压缩（`summarize`）、merge 跨轮 review 汇总（`summarize`，>1 轮）、deepcheck Fixed 预扫（`scan_patterns`）+ dedup（`extract`）、audit 仓库事实候选预审（`propose_repo_facts`）+ 计划vs代码差异摘要（`diff_summary`）、patch 阶段工具映射（见 [steps/08_patch.md](../steps/08_patch.md) 338 行）。**init/plan/code/status/readme 无正文执行点**（历史检索/ADR 检索/现状盘点/文件名/模板选择均走确定性机制 Read/rg/规则，`--scan-verdict` 零 LLM），标 ⚪。完整清单见 [tools_manifest.json](../mcp/cheap-research/tools_manifest.json)
- **不接管决策**：所有高风险子任务（3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案 / 用户对话）一律不交给 cheap-research
- **触发场景详见**：[mcp_per_step.md](mcp_per_step.md) 强证据场景表 + 14 工具入参/出参 schema（见 [mcp/cheap-research/server.py](../mcp/cheap-research/server.py)）
- **当前状态**：14 工具已在 dev_repo 完成，核心契约测试见 [mcp/cheap-research/tests/](../mcp/cheap-research/tests/)；**未同步到已安装目录**（等用户指令）。此前「43 个自检用例全过」声明已撤销（当时无仓库测试证据，以实际 tests/ 为准）

### ⑦ cheap-research 单跑：dedup 子阶段

- **复用 MCP**：⑥ cheap-research（`extract` 用 haiku 分类 + 高质量模型找重复）；函数抽取用 ripgrep（catalog.json）
- **强证据**：02_review/05_deepcheck 步骤中 + cheap-research 🟢 + **函数数 ≥ 50**
- **触发场景**：
  - **02_review §2.5.7（轻量 top 5）**：ripgrep 抽所有函数 → `mcp__cheap-research__extract`(haiku) 分类（wrapper object schema）→ 后处理映射到 23 类 → 取 top 5 类别逐类调高质量模型找重复
  - **05_deepcheck §9.4（完整全量）**：完整 5 阶段（抽取→分类→拆分→高质量模型逐类找重复→报告）。分别检测 catalog.json/categorized.json 是否已由 §2 生成 → 复用避免重跑
- **中间产物路径**：`{ICODE_OUT_DIR}/<ticket>/dedup/{catalog,categorized,duplicates/*.json}`
- **最终报告**：进 step 产物 .md（02_review.md §2.5.7 / 05_deepcheck.md §9.4）的 `## 语义重复检测报告` 段（HIGH/MEDIUM/LOW 三段 + top 5 重复函数对）。**HIGH = 高优先级复核，不直接等于 confirmed**
- **权限边界（v1.1 取消 dedup 对抗豁免）**：dedup 只产出**疑似重复候选对**。会导致代码删除/合并的候选**必须进入 §2.5.5 / §5 A6 对抗验证**，对抗通过才可标 `confirmed`、才可进入合并/删除；纯重复提示（无删除动作）可保持轻量。`trace_refs` 只作文本引用候选，不能证明动态调用/反射/链接关系
- **降级路径**：
  - 函数数 < 50 → 整个 §2.5.7/§9.4 跳过（避免 LLM 成本浪费）
  - 函数数 > 500 → 分批（每批 100），合并结果
  - ripgrep 抽不到 / 不可用 → 整个 dedup 跳过
  - cheap-research 不可用 → 整个 dedup 跳过（不降级主代理自跑，因为 高质量模型分类 + 找重复是高成本子任务）
  - 高质量模型某类返回空数组 → 该类跳过（无重复），不报错

- **已知限制**：
  - **cheap-research schema 不支持 `enum` 类型**——`{"enum": [...]}` 报 `'enum' is not valid under any of the given schemas`。改用 `string` + prompt 强约束
  - **cheap-research LLM 把 array-of-objects 退化为 single object**——多次确认，schema `{"type": "array", "items": {...}}` 时 LLM 仍返回单个 object。**必须用 wrapper object 模式** `{"results": [...]}` 规避
  - **cheap-research LLM 不严格遵守 23 类清单**——即使 prompt 强约束，LLM 仍返回"Number Parsing"/"Math"/"String Manipulation"等自由类别。**必须主代理在写入 categorized.json 前做后处理映射**（见 §2.5.7/§9.4 第 3 步映射表）
  - extract 返回 schema_validation_failed → 重试 1 次（自动改 instruction），仍失败标"分类降级"
- **类别清单（共 23 类）**：
  - 通用类 20：file-ops / string-utils / validation / error-handling / http-api / date-time / data-transform / database / logging / config / async-utils / testing / ui-helpers / crypto / provider-impl / tool-impl / event-handling / session-management / compaction / other
  - iCode 扩展 3 类（嵌入式场景）：**hardware-abstraction**（硬件抽象：传感器/GPIO/中断）/ **protocol-impl**（通信协议：MQTT/Modbus/CAN/串口）/ **build-system**（构建脚本：CMake/Make/Bazel）

---

## 工具命名约定

- 实际工具名格式：`mcp__<server-name>__<tool-name>`
- server-name 用 kebab-case（`sequential-thinking` / `vision-bridge` / `cheap-research`）
- tool-name 用 snake_case（`sequentialthinking` / `analyze_media` / `resolve-library-id` / `summarize`）
- 示例：
  - `mcp__sequential-thinking__sequentialthinking`
  - `mcp__vision-bridge__analyze_media`
  - `mcp__playwright__browser_navigate`
  - `mcp__cheap-research__summarize`

## AI 执行时的工作流

1. **本步骤开始前**：判定本步骤推荐的 MCP（见 [mcp_per_step.md](mcp_per_step.md)）是否可用
   - 查 `~/.claude.json` 的 `mcpServers`：用 `Read` 工具
   - 查当前会话工具列表：工具**直接可见**（按语义识别，含代理前缀形态）即视为可用；不可见再 ToolSearch 取 schema（见 [thinking_core.md](thinking_core.md) 第 0 判据）
2. **如有强证据**：优先用 MCP 工具（省事且返回更结构化）
3. **无强证据**：走降级路径（Bash / Read / Write / WebFetch 等原生工具）
4. **不阻塞**：MCP 不可用不是错误，**降级操作完全是合规的**

---

## 安装建议

| 场景 | 推荐安装 |
|---|---|
| 全新 clone icode-skill | sequential-thinking（必装） + vision-bridge（推荐装，需配三件套） |
| 纯后端项目 | sequential-thinking + vision-bridge + context7 |
| 前端项目 | 上述 + playwright |
| 长期项目 | 上述 + memory（跨工单积累） |
| **降本场景** | 上述 + **cheap-research**（仅低风险候选/压缩/结构化提取子任务，以各步骤正文执行点为真源；3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不走） |

完整安装：`/icode install`（一键扫描 `mcp/` 目录里所有 `install.sh`）
