# 强制思考前置——核心规则（每步必读）

> 本文件是 icode 所有步骤共享的「强制思考前置」**核心规则**，每步必读。
> 各步骤子项详见 [thinking_detail.md](thinking_detail.md)「各步骤思考子项」段，按需 Read 自身步骤对应小节（各 step 文件本已声明本步骤子项，主要作为速查）。
>
> 历史参考小节（init/plan/log/start 检索命中时）按 verdict 分流标注在 [thinking_detail.md](thinking_detail.md)「历史参考小节」段。

## 强证据化总览

本节为强证据化机制索引——列出 3 项机制 + 落地点，避免机制散落各 step 文件后无人能找全。各机制的真源仍在对应文件，本节只起导航作用。

| # | 机制 | 真源 | 落地点 |
|---|---------|------|--------|
| 1 | **跨层枚举对齐修复模式**（防"同值不同义"型根因遗漏） | cross-layer-enum-normalization-pattern（外部参考案例，不随仓库分发） | log.md §2.1 对照表生成 + §0 §2.2 占位 + §3.1 扫描字段 + 阶段3「上游语义追问」；01_plan.md §4 ADR 场景 + §4.5 维度 2 子项；02_review.md 维度 4 风险遗漏子项；04_code.md 优雅度6条第 7 条 + 维度 4 复检双值日志 |
| 2 | **段零文档/姐妹工程/关联工程检索强证据化**（防"只看自己工程代码"） | [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」段（含 3.5 反查父项目 + **3.6 关联工程源码路径定位三级兜底**）+ [references/anti_laziness.md](anti_laziness.md) 第 24 条 | log.md §2.0 自动发现姐妹工程 + 段零 3.6 关联工程源码路径（project_path + manifest + 兜底三级）+ §2.1 段零文档盘点 + 阶段3 对抗质疑者 prompt 喂入 |
| 3 | **TB 附件视频/图片研读强制化**（防"分析错时间点"） | [references/anti_laziness.md](anti_laziness.md) 第 23 条 | log.md「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」段 |

## 强制思考前置·统一契约（step 文件如何引用本文件）

> 所有 step 文件的「强制思考前置」段落**统一**用本契约引用，不可重新展开三件套 Read 长句（防重复；本段为真源，各 step 文件若展开完整长句则视为与本段重复）。

每个 step 文件的「强制思考前置」段落**必须**按以下统一结构（不展开三件套 Read 长句）：

```text
N. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少 N 步）= <step-specific 子项列表>。
```

**三件套 Read 要求**（统一契约真源，step 文件不得重复展开）：

1. **[thinking_core.md](thinking_core.md) 完整内容**（每步必读）——核心规则 + MCP gate + 思考载体（首选 sequential-thinking、降级文字块）
2. **[thinking_detail.md](thinking_detail.md) 对应小节**（按需 Read）——各步骤思考子项 + 历史参考小节
3. **[anti_laziness.md](anti_laziness.md) 完整内容**——30 条偷工反例 + 正面合规要求

**多 Read 追加**：本步骤额外要求 Read 其他 references 时，**追加**在 step 文件强制思考前置段落末，格式 `+ Read [references/xxx.md](../references/xxx.md) 完整内容` 即可。当前已识别的多 Read 场景：

- `02_review.md` / `log.md`：+ Read [references/adversarial.md](../references/adversarial.md) 完整内容（对抗模式）
- `doc.md`：+ Read [references/doc_template.md](../references/doc_template.md) 完整内容（doc 模板）

## 规则

每个步骤开始前，必须先 ultrathink 并完成结构化思考——这是不可跳过的硬性前置。思考环节不可整体跳过，但**执行载体分主备两档**：

- **首选**：调用 `sequential-thinking` MCP 工具（`mcp__sequential-thinking__sequentialthinking`），至少 3 步（步骤定义里另有要求除外，如至少 4~5 步），每步对应该步骤声明的子项之一。上下文能看到该 tool_call 记录即为合规证据。
- **降级**：若当前环境未配置该 MCP（`~/.claude.json` 的 `mcpServers` 与项目根 `.mcp.json` 均无 `sequential-thinking` server，或已配置但 ToolSearch 取不到/调用失败），则必须以显式的「结构化思考」文字块替代——在回复中先输出一个 `### 结构化思考` 块，逐项完成该步骤要求的子项（每项一小段，不可省略），再进入产出。该文字块即为合规证据。

> **判定 MCP 是否可用**（关键：MCP 工具为懒加载，且 AI 解析 deferred 列表不可靠——实测部分模型在长逗号列表中漏匹配工具名。**治本：跳过列表解析，直接 ToolSearch 验证**）：
>
> **第一步·直接 ToolSearch 验证**（不依赖 AI 对 deferred 列表的文本解析）：
>
> 1. **直接调用 ToolSearch**：`query="select:mcp__sequential-thinking__sequentialthinking"` 取 schema
> 2. **ToolSearch 返回 schema** → 工具可用，进入「第二步·首选路径执行」
> 3. **ToolSearch 返回空/无命中** → 再 Read `~/.claude.json` 确认是否配置了 `sequential-thinking` server：
>    - `~/.claude.json` 有配置 → 用 `query="sequential-thinking"`（模糊搜索）再试一次 ToolSearch
>    - `~/.claude.json` 无配置 → 进入「降级路径」
>
> **第二步·首选路径执行**（ToolSearch 确认 schema 可用后）：
>
> 1. 实际调用 `mcp__sequential-thinking__sequentialthinking` 工具，至少 3 步，每步对应该步骤声明的子项之一
> 2. 调用成功 -> 完成思考
> 3. 调用返回错误/超时 -> 才能进入降级路径
>
> **禁止误判场景**（历史实测的踩坑模式，逐条禁止）：
>
> - ⛔ **未实际调用 ToolSearch 就判定"deferred tools 无 X"** —— 这是早期版本的踩坑根因：AI 试图手动解析系统提示中的 deferred 列表但匹配失败。**必须直接调 ToolSearch，不以 AI 文本解析结果为判断依据**
> - ⛔ **ToolSearch 首次精确搜索无命中但 ~/.claude.json 有配置时不再试模糊搜索** —— 必须再试一次模糊搜索，ToolSearch 对某些工具名的精确匹配可能因前缀差异（`mcp__` vs server 名）失败
> - ⛔ **未实际调用 `mcp__sequential-thinking__sequentialthinking` 就判定"调用失败"** —— 必须有真实的调用返回错误/超时证据
> - ⛔ **看到顶层工具列表里没有该 MCP 就判定"不可用"** —— 顶层看不到 ≡ deferred 池可见，是懒加载不是缺失
> - ⛔ **凭记忆推断未经 Read 配置文件判定"无 server"** —— 必须实际 Read `~/.claude.json`，未读到配置才能说"无 server"
>
> **降级路径的合法前置**（满足以下**任一组**即可走降级文字块）：
>
> **配置缺失组**（必须同时满足）：
> 1. 已实际 ToolSearch 取 schema（精确 + 模糊各至少 1 次）-> 均无命中
> 2. 已实际 Read `~/.claude.json` 的 `mcpServers` **与** 项目根 `.mcp.json`（若有）-> **都**无 `sequential-thinking` server
>
> **解析失败组**（必须同时满足）：
> 1. 已实际 ToolSearch 取 schema（精确 + 模糊各至少 1 次）-> 均无命中
> 2. 已实际 Read `~/.claude.json` 的 `mcpServers` **与** 项目根 `.mcp.json`（若有）-> 至少**一处**有 `sequential-thinking` server
> 3. 降级声明**必须注明**"ToolSearch 解析失败，配置有 server"（区别于配置缺失组），并**提示用户运行 `/mcp` 检查 sequential-thinking 连接状态**（配置存在 ≠ 本会话已连接——MCP 连接是会话级快照，server 未连接时 ToolSearch 恒空，需重连或重开会话恢复；思考质量不受降级影响，按文字块照常完成）
>
> **两组均不满足即不可走降级**，必须坚持首选路径。
>
> **两种载体任选其一即可，但思考环节本身不可省略**——未呈现任一形式的思考证据，该步骤产出视为不合规。

## 通用流程（每步执行）

1. 输出 `ultrathink` 触发词（触发更长的内部推理 budget）
2. **显式 Read 本步骤引用的 references 文件**（每步必须重新 Read，同会话已读不豁免——显式Read是深度思考的前置仪式，凭记忆会降级思考质量），Read 后在回复中输出确认行 `📖 已 Read references/xxx.md` 作为合规证据
3. **MCP 调用 gate**（不可跳过）：在结构化思考开始前，先处理本步 🟢 MCP（按 [mcp_per_step.md](mcp_per_step.md)「强证据场景判定」）：
   - 列出本步满足强证据场景的 🟢 MCP（**不含 sequential-thinking**，它由第 4 步承载；其余 🟢 MCP 由本 gate + 各 step 执行步骤内嵌点承载）
   - 对每个 🟢 MCP：ToolSearch 取 `mcp__<name>__<tool>` schema -> **实际调用一次** -> 把调用结果（成功/空/失败）写进思考块「MCP 调用」段
   - 调用失败/返回空 -> 思考块写明降级原因（MCP 不可用 / 无相关结果 / 不适用场景）才能跳过；**未经实际调用就标降级 = 反偷懒第 21 条违规**
   - ⚪ MCP（强证据场景不满足）无需评估无需声明
   - **本步若无 🟢 MCP**（全 ⚪）：gate 直接通过，思考块记"本步无 🟢 MCP（强证据场景均不满足）"
4. 完成结构化思考（sequential-thinking MCP 优先，不可用则降级文字块），至少 3 步，每步对应该步骤声明的子项之一
5. 不得跳过思考直接产出——所有 Write/Edit 必须在思考证据之后

## 层级关系（API 层 / Hook 层 / Prompt 层 概览）

- API 层：`CLAUDE_CODE_EFFORT_LEVEL=max` + `model=opus`（控制推理 effort）
- Hook 层：`UserPromptSubmit` 拦截 `/icode` 命令，缺思考证据时注入提醒
- Prompt 层：SKILL.md「强制思考前置」段 + 本文件 + 各 step 文件声明的子项
