# 强制思考前置——核心规则（每步必读）

> 本文件是 icode 所有步骤共享的「强制思考前置」**核心规则**，每步必读。
> 各步骤子项详见 [thinking_detail.md](thinking_detail.md)「各步骤思考子项」段，按需 Read 自身步骤对应小节（各 step 文件本已声明本步骤子项，主要作为速查）。
>
> 历史参考小节（init/plan/log/start 检索命中时）按 verdict 分流标注在 [thinking_detail.md](thinking_detail.md)「历史参考小节」段。
>
> **分级思考治理（reasoning gate）**：本文件从「所有步骤固定调用 sequential-thinking ≥3 次」改为「按复杂度分级 L0～L3 选择思考载体」。分级判定机器真源 = `mcp/reasoning-gate/gates.json`（默认等级/升级触发器**只从这里读**）；运行痕迹 = `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`（每 step 一条最终判定）；校验器 = `python3 tools/lint_thinking_gate.py <out_dir> [--step <step>] [--strict] [--json]`。完整说明见 `ICODE_SEQUENTIAL_THINKING_OPTIMIZATION.md`。

## 强证据化总览

本节为强证据化机制索引——列出 3 项机制 + 落地点，避免机制散落各 step 文件后无人能找全。各机制的真源仍在对应文件，本节只起导航作用。

| # | 机制 | 真源 | 落地点 |
|---|---------|------|--------|
| 1 | **跨层枚举对齐修复模式**（防"同值不同义"型根因遗漏） | cross-layer-enum-normalization-pattern（外部参考案例，不随仓库分发） | log.md §2.1 对照表生成 + §0 §2.2 占位 + §3.1 扫描字段 + 阶段3「上游语义追问」；01_plan.md §4 ADR 场景 + §4.5 维度 2 子项；02_review.md 维度 4 风险遗漏子项；04_code.md 优雅度6条第 7 条 + 维度 4 复检双值日志 |
| 2 | **段零文档/姐妹工程/关联工程检索强证据化**（防"只看自己工程代码"） | [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」段（含 3.5 反查父项目 + **3.6 关联工程源码路径定位三级兜底**）+ [references/anti_laziness.md](anti_laziness.md) 第 24 条 | log.md §2.0 自动发现姐妹工程 + 段零 3.6 关联工程源码路径（project_path + manifest + 兜底三级）+ §2.1 段零文档盘点 + 阶段3 对抗质疑者 prompt 喂入 |
| 3 | **TB 附件视频/图片研读强制化**（防"分析错时间点"） | [references/anti_laziness.md](anti_laziness.md) 第 23 条 | log.md「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」段 |

## 强制思考前置·统一契约（step 文件如何引用本文件）

> 所有 step 文件的「强制思考前置」段落**统一**用本契约引用，不可重新展开三件套 Read 长句（防重复；本段为真源，各 step 文件若展开完整长句则视为与本段重复）。

每个 step 文件的「强制思考前置」段落**必须**按以下统一结构（不展开三件套 Read 长句，也不展开 L0～L3 分级规则）：

```text
N. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行，先按 reasoning gate 分级再选载体）：本步骤默认等级 <L0/L1/L2/L3>，思考子项（L1 决策字段 / L2·L3 thought 职责）= <step-specific 子项列表>。
```

**三件套 Read 要求**（统一契约真源，step 文件不得重复展开）：

1. **[thinking_core.md](thinking_core.md) 完整内容**（每步必读）——核心规则 + reasoning gate（L0～L3）+ MCP gate + L2/L3 思考载体（sequential-thinking、降级文字块）
2. **[thinking_detail.md](thinking_detail.md) 对应小节**（按需 Read）——各步骤思考子项 + 历史参考小节
3. **[anti_laziness.md](anti_laziness.md) 完整内容**——37 条偷工反例 + 正面合规要求

**多 Read 追加**：本步骤额外要求 Read 其他 references 时，**追加**在 step 文件强制思考前置段落末，格式 `+ Read [references/xxx.md](../references/xxx.md) 完整内容` 即可。当前已识别的多 Read 场景：

- `02_review.md` / `log.md`：+ Read [references/adversarial.md](../references/adversarial.md) 完整内容（对抗模式）
- `doc.md`：+ Read [references/doc_template.md](../references/doc_template.md) 完整内容（doc 模板）

## 分级思考（reasoning gate）规则

每个步骤开始前，**先按 reasoning gate 分级（L0～L3），再按等级选择思考载体**——这是不可跳过的硬性前置。等级由机器可检查信号驱动（默认等级 + 确定性升级触发器），不允许代理用「我觉得不需要」跳过思考；也不允许在 L0/L1 用调用 sequential-thinking 凑仪式化合规（记录 `over_invoked`，灰度观察）。

**分级模型**（默认等级表与升级触发器唯一真源 = `mcp/reasoning-gate/gates.json`，本节只给摘要）：

| 等级 | 适用问题 | 必须动作 | sequential-thinking |
|---|---|---|---|
| **L0 确定性执行** | 输入明确、单一路径、结果可由脚本直接校验（status/list/help/install/bak） | 执行现有状态/文件/schema/安全门禁；`mechanism=deterministic_checks` | 不调用 |
| **L1 简短决策** | 有少量取舍但根因/方案已被直接证据确认（readme/ppt/close/reopen/worktree/init/doc/limit/merge） | 写简短决策记录（复用 `.decision_anchors.json`）；`mechanism=decision_record` | 不调用 |
| **L2 复杂推理** | 多候选根因、多文件/多模块、并发/状态机、方案可修订（plan/review/code/patch/log/deepcheck/audit） | sequential-thinking 3～5 步并回写决策摘要；`mechanism=sequential-thinking` | **必须调用**；不可用时结构化降级 |
| **L3 高风险对抗** | 架构变更、破坏性影响、高不确定性、证据矛盾、跨职责边界 | L2 + 独立对抗审查或等价反证验证；`mechanism=sequential-thinking+adversarial` | **必须调用**，但不能充当对抗者 |

**升级触发器**（命中任一升到至少 L2 / L3；不能仅凭命令名向下降级）：
- **升 L2**：多根因/多方案候选（`multiple_candidates`）；≥2 业务模块或 ≥3 代码文件（`multi_module_multi_file`）；并发/异步/竞态/状态机/生命周期/回滚（`concurrency_state_machine`）；代码/日志/历史/用户描述证据冲突（`evidence_conflict`）；偏离 `03_plan_final.md` 或未分流 `scope_escalations`/`requirement_deltas`（`deviation_escalation`）；关键假设无直接证据需设计验证（`unverified_key_assumption`）；修改共享接口/持久化协议/跨进程协议/公共配置 schema（`shared_interface_change`）。
- **升 L3**：破坏数据/不可恢复/线上发布/广泛兼容影响（`destructive_irreversible`）；新增全局门控/改变生命周期身份/跨职责边界重构（`new_global_gate`）；L2 后仍有相互矛盾的高置信证据（`conflicting_high_confidence`）；同一根因结论连续两轮被新证据推翻（`conclusion_repeatedly_overturned`）；用户/供应商/历史结论与当前可执行证据冲突且无法单方裁定（`external_evidence_conflict`）。
- **不能单独作为升级理由**：文档很长 / 用户要求"认真分析" / MCP 已安装 / 过去习惯调用 / 为凑步数拆分同一句结论。

**思考载体（按等级）**：
- **L0/L1**：不进入 sequential-thinking 可用性探测（避免 ToolSearch 与配置读取开销）。L0 只执行既有机器门禁；L1 输出 `.decision_anchors.json` 决策摘要（字段见 §6 L1 决策记录契约）。
- **L2/L3**：**首选**调用 `sequential-thinking` MCP 工具（`mcp__sequential-thinking__sequentialthinking`）3～5 步（L3 另加独立对抗），每步对应该步骤声明的子项之一；**降级**（MCP 未配置/取不到/调用失败，判定见下节）时必须以显式的「### 结构化思考」文字块替代，逐项完成该步骤要求的子项。该文字块即为合规证据。
- **L2/L3 未调用且没有真实调用失败证据 = 不合规**（trace 记录 `attempted=false` 或伪造 `success` 均为违规）。

> **判定 MCP 是否可用**（**仅 L2/L3 需要**——L0/L1 不进入本探测；三态判定：① 工具直接可见 → 直接调用；② 不可见 → ToolSearch 验证；③ 调用报错 → 降级。**先走第 0 判据**）：
>
> **0 前置·强制可见性自检（第 0 判据执行入口）**（**任何 ToolSearch 之前必先执行**，覆盖**全部**场景——思考块、非思考块、MCP 判定、其他工具可用性判断）：① 先扫描当前会话工具列表（顶层工具定义或已加载工具集）；② 目标工具**直接可见**（标准形态 `mcp__<server>__<tool>` 或代理前缀 `__<proxy>_<tool>`）→ **直接调用**，并在回复中输出证据行「📖 工具可见性自检: `<工具名>` 直接可见 → 直接调用」；③ 不可见 → **仅此时**才走下方第 0 判据 / 第一步 ToolSearch。**强制句**：任何 ToolSearch 调用之前，必须先完成本次可见性自检（或已有直接可见证据）；**未自检直接 ToolSearch、并以空结果判"环境无此工具" = 违规**——直接可见工具不在 ToolSearch deferred 懒加载池内，对其 ToolSearch **恒空**是设计使然，空结果不代表不可用。
>
> **第 0 判据·直接可见即可用**（**最高优先级**，先于任何 ToolSearch / Read 配置文件步骤；上方 **0 前置** 为本判据的**强制执行入口**——可见性自检即按本判据判定）：若当前会话工具列表（顶层工具定义或已加载工具集）中**已直接存在**对应 MCP 工具的完整 schema 定义 → **直接调用**即可（工具名按语义识别：标准形态 `mcp__<server>__<tool>` 如 `mcp__sequential-thinking__sequentialthinking`，或代理前缀形态 `__<proxy>_<tool>` 也算直接可见），**无需 ToolSearch、无需 Read `~/.claude.json`**。ToolSearch 仅用于"列表里看不到但怀疑有（懒加载）"的场景——直接可见是最强可用证据，绕过它去查 ToolSearch 并拿空结果判"不可用"是本段要消灭的误判根因。**直接可见时禁止再走 ToolSearch 验证**（已暴露工具不在 ToolSearch deferred 懒加载池内，对其 ToolSearch **恒空**是设计使然，空结果不代表不可用）；**工具列表直接可见却报"不可用/降级" = 违规**，必须直接调用。
>
> **第一步·直接 ToolSearch 验证**（仅当列表**不可见**时走此步；不依赖 AI 对 deferred 列表的文本解析）：
>
> 1. **直接调用 ToolSearch**：`query="select:mcp__sequential-thinking__sequentialthinking"` 取 schema
> 2. **ToolSearch 返回 schema** → 工具可用，进入「第二步·首选路径执行」
> 3. **ToolSearch 返回空/无命中** → 再 Read `~/.claude.json` 确认是否配置了 `sequential-thinking` server：
>    - `~/.claude.json` 有配置 → 用 `query="sequential-thinking"`（模糊搜索）再试一次 ToolSearch
>    - `~/.claude.json` 无配置 → 也用 `query="sequential-thinking"`（模糊搜索）再试一次 ToolSearch（与下方配置缺失组前置 ① 对齐）→ 仍无命中 → 进入「降级路径」
>
> **第二步·首选路径执行**（ToolSearch 确认 schema 可用后，L2/L3）：
>
> 1. 实际调用 `mcp__sequential-thinking__sequentialthinking` 工具，3～5 步（步骤定义里另有要求除外），每步对应该步骤声明的子项之一；L3 在此基础上另加独立对抗验证
> 2. 调用成功 -> 完成思考
> 3. 调用返回错误/超时 -> 才能进入降级路径
>
> **禁止误判场景**（历史实测的踩坑模式，逐条禁止）：
>
> - ⛔ **未实际调用 ToolSearch 就判定"deferred tools 无 X"** —— 这是早期版本的踩坑根因：AI 试图手动解析系统提示中的 deferred 列表但匹配失败。**必须直接调 ToolSearch，不以 AI 文本解析结果为判断依据**
> - ⛔ **ToolSearch 首次精确搜索无命中但 ~/.claude.json 有配置时不再试模糊搜索** —— 必须再试一次模糊搜索，ToolSearch 对某些工具名的精确匹配可能因前缀差异（`mcp__` vs server 名）失败
> - ⛔ **ToolSearch 用模糊词查询并以其空结果判"工具不存在"** —— 精确 `select:` 优先（`query="select:mcp__<name>__<tool>"`）；模糊词（如 `query="sequential-thinking"`）仅允许在精确无命中后按上方流程作**补充重试**，其空结果**不能单独作为"工具不存在"依据**（模糊词可能不匹配，且非 deferred 工具本就不在 ToolSearch 池内）
> - ⛔ **未实际调用 `mcp__sequential-thinking__sequentialthinking` 就判定"调用失败"** —— 必须有真实的调用返回错误/超时证据
> - ⛔ **看到顶层工具列表里没有该 MCP 就判定"不可用"** —— 顶层看不到 ≡ deferred 池可见，是懒加载不是缺失
> - ⛔ **工具已在当前工具列表直接可见（完整 schema），却绕过直接调用去 ToolSearch、并以空结果判"不可用"** —— 直接可见 = 可直接调用（走第 0 判据）；ToolSearch 空结果只对"未直接暴露"场景有判定意义
> - ⛔ **凭记忆推断未经 Read 配置文件判定"无 server"** —— 必须实际 Read `~/.claude.json`，未读到配置才能说"无 server"
>
> **降级路径的合法前置**（进入下表三组判定**之前**，先执行**可见性自检**：反查当前会话顶层工具列表是否已直接可见对应 MCP 工具完整 schema——**若可见，直接调用，禁止走降级**（走第 0 判据）；确认不可见后才按下表判定。满足以下**任一组**即可走降级文字块；降级声明**必须**按下表固定模板原样输出，不得自拟其他措辞——AI 自拟的泛化声明会漏掉"配置有 server"等关键区分信息，造成"配置了却报不可用"的误解）：
>
> | 组 | 必须同时满足 | 降级声明固定模板（原样输出，确认行格式与 steps/08_patch.md 一致） |
> |----|------------|-------------------------------------|
> | **配置缺失组** | ① ToolSearch 取 schema（精确 + 模糊各至少 1 次）→ 均无命中；② Read `~/.claude.json` 的 `mcpServers` **与** 项目根 `.mcp.json`（若有）→ **都**无 `sequential-thinking` server | `强制思考: 降级文字块（未配置 server：~/.claude.json 与 .mcp.json 均无 sequential-thinking，可运行 mcp/sequential-thinking/install.sh 安装）` |
> | **解析失败组** | ① ToolSearch 取 schema（精确 + 模糊各至少 1 次）→ 均无命中；② Read 配置 → 至少**一处**有 `sequential-thinking` server | `强制思考: 降级文字块（ToolSearch 解析失败，配置有 server——本会话未连接/工具未暴露，请运行 /mcp 检查连接状态或重开会话；工具本身已装，思考按文字块照常完成）` |
> | **调用失败组** | ① ToolSearch 取 schema ≥1 次 → **有命中**；② 实际调用工具 ≥1 次 → 返回错误/超时 | `强制思考: 降级文字块（ToolSearch 命中但调用失败：<具体错误>）` |
>
> > **解析失败组根因认知**：配置存在 ≠ 本会话已连接——MCP 连接是会话级快照，server 未连接/工具未暴露时 ToolSearch 恒空；部分会话经代理接入（如 litellm）时 MCP 工具以 `__<proxy>_<tool>` 前缀暴露、不在 ToolSearch deferred 池内，此时 ToolSearch 对**所有** MCP 工具恒空（含已装好可用的），属命名/连接差异、**不代表未安装**。此组降级合法，思考质量不受影响，无需反复怀疑配置缺失。
>
> **三组均不满足即不可走降级**，必须坚持首选路径。
>
> **两种载体任选其一即可，但思考环节本身不可省略**——未呈现任一形式的思考证据，该步骤产出视为不合规。

## 通用流程（每步执行）

1. **分级**：按 [mcp_per_step.md](mcp_per_step.md)「默认等级表」+ `mcp/reasoning-gate/gates.json` 升级触发器确定本步等级（L0～L3），把等级与触发原因写入 `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`（每 step 最终一行；schema/词表见 [thinking_detail.md](thinking_detail.md)「thinking gate trace」段）。
2. 输出 `ultrathink` 触发词（触发更长的内部推理 budget）——**L0/L1 可省略**（纯机器门禁/决策记录不依赖推理预算）。
3. **显式 Read 本步骤引用的 references 文件**（每步必须重新 Read，同会话已读不豁免——显式Read是深度思考的前置仪式，凭记忆会降级思考质量），Read 后在回复中输出确认行 `📖 已 Read references/xxx.md` 作为合规证据。
4. **MCP 调用 gate**（L2/L3 不可跳过）：在结构化思考开始前，先处理本步 🟢 MCP（按 [mcp_per_step.md](mcp_per_step.md)「强证据场景判定」）：
   - 列出本步满足强证据场景的 🟢 MCP（**不含 sequential-thinking**，它由第 5 步承载；其余 🟢 MCP 由本 gate + 各 step 执行步骤内嵌点承载）
   - 对每个 🟢 MCP：**若该工具已在工具列表直接可见（完整 schema）则直接调用**，不可见才 ToolSearch 取 `mcp__<name>__<tool>` schema -> **实际调用一次** -> 把调用结果（成功/空/失败）写进思考块「MCP 调用」段
   - 调用失败/返回空 -> 思考块写明降级原因（MCP 不可用 / 无相关结果 / 不适用场景）才能跳过；**未经实际调用就标降级 = 反偷懒第 21 条违规**
   - ⚪ MCP（强证据场景不满足）无需评估无需声明
   - **本步若无 🟢 MCP**（全 ⚪）：gate 直接通过，思考块记"本步无 🟢 MCP（强证据场景均不满足）"
5. **完成思考（按等级选载体）**：
   - **L0**：只执行既有机器门禁（状态/文件/schema/安全），无思考块。
   - **L1**：写 `.decision_anchors.json` 决策摘要（`reasoning_gate` 对象，字段见 [decision_anchors.md](decision_anchors.md)「L1 决策记录契约」），列事实、风险和验证动作。
   - **L2/L3**：sequential-thinking MCP 优先（3～5 步，每步对应该步骤声明的子项之一；L3 另加独立对抗），不可用则降级文字块。
6. 不得跳过思考直接产出——所有 Write/Edit 必须在思考证据之后（L0 为机器门禁通过后）。

## reasoning gate 执行门（gate）流程

> reasoning gate 是「分级思考」的**幂等可机检**状态机。
> 机器真源 = `mcp/reasoning-gate/gates.json`（默认等级/升级触发器**只从这里读**，禁止在 step 文档/脚本各自写常量）；
> 运行痕迹 = `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`（每 step 一条最终判定，JSON Lines）；
> 校验器 = `python3 tools/lint_thinking_gate.py <out_dir> [--step <step>] [--strict] [--json] [--require-trace]`。
> 新建工单 metadata 必须含 `"thinking_gate_schema_version": 1`；旧工单缺失时校验器输出 legacy-untracked 兼容警告，不阻断只读命令。

**gate 全流程（每步开始时执行）**：

1. **加载 gate catalog**：Read `mcp/reasoning-gate/gates.json`，取本 step 默认等级与升级触发器枚举。
2. **确定性计算等级并写 trace**：按默认等级 + 升级触发器算出最终 `tier`，**先追加一行 trace**（`attempted` 暂填 `false`、`result` 暂填 `pending`，`at` 为当前 ISO-8601）；**不得用"我觉得没必要"当跳过理由，也不得仅凭命令名向下降级**。
3. **按等级执行**：
   - L0 → 执行既有机器门禁后更新 trace：`mechanism=deterministic_checks`、`attempted=true`、`result=success`。
   - L1 → 写 `.decision_anchors.json` 决策摘要后更新 trace：`mechanism=decision_record`、`attempted=true`、`result=success`。
   - L2/L3 → 实际调用 sequential-thinking（先可见性自检；不可见才 ToolSearch 取 schema）。调用成功后更新 trace：`attempted=true`、`result=success`、`mechanism=sequential-thinking`（L3 为 `sequential-thinking+adversarial`）；失败/超时 → `attempted=true`、`result=degraded`、`degraded_reason=<真实失败类别>`。
4. **step 转换前运行 validator**：`python3 tools/lint_thinking_gate.py {ICODE_OUT_DIR} --step <step> --strict`——有 in-scope requires_trace step 未履行（missing / L2/L3 未调用 / tier 降级 / 触发词非法）时不得标记该步流程合规，先回补再转换。

**trace 行约束**（校验器强制）：

- `tier`：`L0` / `L1` / `L2` / `L3`；`tier` 必须 ≥ `default_tier`（只能升级不能降级）
- `mechanism`：`deterministic_checks` / `decision_record` / `sequential-thinking` / `sequential-thinking+adversarial`
- `result`：`success` / `degraded` / `blocked`；`degraded` 必须 `attempted=true` 且 `degraded_reason` 非空
- `triggers`：只允许 catalog 内稳定枚举，禁止随意自然语言扩张；`tier > default_tier` 时 `triggers` 必须非空
- **trace 禁止保存**：thought 正文（`thought`/`thought_text`/`raw_thought` 等字段名）、密钥、Cookie、设备凭据、大段日志正文
- 同一 step 重跑允许追加新行，校验器以最后一条为当前状态并保留历史
- **L0/L1 出现 sequential-thinking 调用**：记录 `over_invoked=true`（灰度观察项，默认不阻断）；`--strict` 时升级为阻断
- 兼容规则：旧工单没有 trace → `legacy-untracked` 警告，不阻断 `status`/`list`/`readme`/`close`；新工单在步骤转换前校验当前步骤 trace

## cheap-research 执行门（gate）流程

> 把 cheap-research 的「双保险」从两套自然语言提示词升级为**幂等可机检**状态机。
> gate 机器真源 = `mcp/cheap-research/gates.json`（阈值**只从这里读**，禁止在 step 文档/脚本各自写常量）；
> 运行痕迹 = `{ICODE_OUT_DIR}/.mcp_gate_trace.jsonl`（每 gate 一条最终判定，JSON Lines）；
> 校验器 = `python3 tools/lint_mcp_coverage.py <out_dir> [--step <step>] [--strict] [--json] [--require-trace]`。
> 新建工单 metadata 必须含 `"mcp_gate_schema_version": 1`；旧工单缺失时校验器输出 legacy-untracked 兼容警告，不阻断。

**gate 全流程（每步到对应执行点时执行）**：

1. **加载 gate catalog**：Read `mcp/cheap-research/gates.json`，取本 step 相关 gate 与阈值（`tb_comment_extract_min` / `long_text_threshold_bytes` / `dedup_min_functions` / `merge_min_rounds` / `max_input_bytes_per_call`）。
2. **确定性计算 eligibility 并立刻写 trace**：按 gates.json 的 condition + 事实文件（TB 评论数 / 候选日志字节 / 函数 catalog / review round 数 / mode）算出 `eligible`，先追加一行 trace（`decision` 暂填 `pending`，`at` 为当前 ISO-8601）；**不得用"我觉得没必要"当 skip 理由**。
3. **eligible 时先查缓存**：Read `{ICODE_OUT_DIR}/.cheap_research_cache.json` 查 `tool + args_hash`（语义见 SKILL.md「cheap-research 14 工具会话内缓存」段）。
4. **有效缓存命中**：把 trace 行更新为 `decision=cache_hit`、`attempted=false`、`result=success`、`cache_key=<args_hash>`——**gate 直接 fulfilled，不再重复调用**。
5. **未命中才实际调用**：调 `mcp__cheap-research__<tool>`（先可见性自检；不可见才 ToolSearch 取 schema）。调用成功/返回空/失败后**更新最终 trace**：
   - 成功 → `decision=called`、`attempted=true`、`result=success`、`source_files=[...]`
   - 空/错误/超时 → `decision=degraded_after_attempt`、`attempted=true`、`result=empty|error|timeout`、`error_class=<类名>`
   - 并把结果写回缓存（atomic 写 `.tmp` + `mv`）
6. **step 正文到达同一 gate 时读取最终 trace**：已 fulfilled（called/cache_hit/degraded_after_attempt 已记录）则复用，**避免 A/B 两层重复调用**；未 fulfilled 才执行上述流程。
7. **step 转换前运行 validator**：`python3 tools/lint_mcp_coverage.py {ICODE_OUT_DIR} --step <step> --strict`——有 eligible 未履行 gate 时不得标记该步流程合规，先回补再转换。

**trace 行约束**（校验器强制）：

- `decision` 词表：`called` / `cache_hit` / `skipped_not_eligible` / `skipped_stage_not_reached` / `degraded_after_attempt`
- `eligible=true` 只允许 `called` / `cache_hit` / `degraded_after_attempt`；`degraded_after_attempt` 必须 `attempted=true` 且 `result=error|empty|timeout`
- `eligible=false` 只允许 `skipped_not_eligible` / `skipped_stage_not_reached`，且必须有结构化 `evidence`（不能只写自然语言）
- **trace 禁止保存**：工具完整结果、日志正文、API key、Cookie、远程 URL 查询参数、设备凭据
- 同一 `step + gate_id` 重跑允许追加新行，校验器以最后一条为当前状态并保留历史

**候选导航不等于裁决**：cheap-research 输出始终是候选/摘要，主代理负责实证与最终结论；根因、架构、对抗、修复、终审裁决一律不回落到 cheap-research。

## 层级关系（API 层 / Hook 层 / Prompt 层 概览）

- API 层：`CLAUDE_CODE_EFFORT_LEVEL=max` + `model=opus`（控制推理 effort）
- Hook 层：`UserPromptSubmit` 拦截 `/icode` 命令，缺思考证据时注入提醒
- Prompt 层：SKILL.md「强制思考前置」段 + 本文件 + 各 step 文件声明的子项
