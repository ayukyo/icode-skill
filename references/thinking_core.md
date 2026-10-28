# 强制思考前置——核心规则

> 本文件是 icode 所有步骤共享的「强制思考前置」**核心规则**。按下方「共享规则加载」首次完整读取、失效重载；每步仍独立执行思考与门禁。
> 各步骤子项详见 [thinking_detail.md](thinking_detail.md)「各步骤思考子项」段，按需 Read 自身步骤对应小节（各 step 文件本已声明本步骤子项，主要作为速查）。
>
> 历史参考小节（init/plan/log/start 检索命中时）按 verdict 分流标注在 [thinking_detail.md](thinking_detail.md)「历史参考小节」段。
>
> **分级思考治理（reasoning gate）**：本文件从「所有步骤固定调用 sequential-thinking ≥3 次」改为「按复杂度分级 L0～L3 选择思考载体」。分级判定机器真源 = `mcp/reasoning-gate/gates.json`（默认等级/升级触发器**只从这里读**）；运行痕迹 = `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`（每 step 一条最终判定）；校验器 = `python3 tools/lint_thinking_gate.py <out_dir> [--step <step>] [--strict] [--json]`。分级规则完整定义以 `mcp/reasoning-gate/gates.json` 为准；历史出处见 [docs/adr/ADR-0001-optimization-proposal-provenance.md](../docs/adr/ADR-0001-optimization-proposal-provenance.md)。
>
> **工单执行模型（ticket-scoped 执行步骤必做）**：完整 Read [execution_model.md](execution_model.md)，按 `step start → 边界 check → artifact/operation 回执 → step finish → transition` 执行。输入漂移必须按机器返回 route 回流；有副作用动作禁止盲重试。无工单目录的 help/install/list 等纯辅助入口不创建伪执行事件；`verify --plan` 只生成派生计划，也不创建验证执行事件。

## 强证据化总览

本节为强证据化机制索引——列出 4 项机制 + 落地点，避免机制散落各 step 文件后无人能找全。各机制的真源仍在对应文件，本节只起导航作用。

| # | 机制 | 真源 | 落地点 |
|---|---------|------|--------|
| 1 | **跨层枚举对齐修复模式**（防"同值不同义"型根因遗漏） | cross-layer-enum-normalization-pattern（外部参考案例，不随仓库分发） | log.md §2.1 对照表生成 + §0 §2.2 占位 + §3.1 扫描字段 + 阶段3「上游语义追问」；01_plan.md §4 ADR 场景 + §4.5 维度 2 子项；02_review.md 维度 4 风险遗漏子项；04_code.md 优雅度6条第 7 条 + 维度 4 复检双值日志 |
| 2 | **段零文档/姐妹工程/关联工程检索强证据化**（防"只看自己工程代码"） | [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」段（含 3.5 反查父项目 + **3.6 关联工程源码路径定位三级兜底**）+ [references/anti_laziness.md](anti_laziness.md) 第 24 条 | log.md §2.0 自动发现姐妹工程 + 段零 3.6 关联工程源码路径（project_path + manifest + 兜底三级）+ §2.1 段零文档盘点 + 阶段3 对抗质疑者 prompt 喂入 |
| 3 | **TB 附件视频/图片研读强制化**（防"分析错时间点"） | [references/anti_laziness.md](anti_laziness.md) 第 23 条 | log.md「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」段 |
| 4 | **可恢复执行模型**（端口/Reactive 边界/统一轨迹/副作用策略/回执） | [execution_model.md](execution_model.md) + `gates.json.execution_model` | ticket-scoped step 开始、写入/等待/副作用/转换边界与长动作终结 |

## 强制思考前置·统一契约（step 文件如何引用本文件）

> 所有 step 文件的「强制思考前置」段落**统一**用本契约引用，不可重新展开三件套 Read 长句（防重复；本段为真源，各 step 文件若展开完整长句则视为与本段重复）。

每个 step 文件的「强制思考前置」段落**必须**按以下统一结构（不展开三件套 Read 长句，也不展开 L0～L3 分级规则）：

```text
N. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行，先按 reasoning gate 分级再选载体）：本步骤默认等级 <L0/L1/L2/L3>，思考子项（L1 决策字段 / L2·L3 thought 职责）= <step-specific 子项列表>。
```

**三件套 Read 要求**（统一契约真源，step 文件不得重复展开）：

1. **[thinking_core.md](thinking_core.md) 完整内容**——核心规则 + reasoning gate（L0～L3）+ MCP gate + L2/L3 思考载体（sequential-thinking、降级文字块）
2. **[thinking_detail.md](thinking_detail.md) 对应小节**（按需 Read）——各步骤思考子项 + 历史参考小节
3. **[anti_laziness.md](anti_laziness.md) 完整内容**——39 条偷工反例 + 正面合规要求

### 共享规则加载

- **首次实读**：当前上下文首次执行相关步骤时，按声明范围实读共享规则；要求完整内容的必须完整读，指定小节的读完整小节。只作导航或历史出处的链接不触发递归加载；有条件的规则在条件命中时读取。
- **同上下文复用**：完整原文仍在当前上下文、路径相同且已用当前内容 SHA-256 核对未变时，可以复用已读规则，不重复输出全文。首次 Read 记录路径及 SHA-256；复用只需简短说明引用的已读来源，不新增工单缓存文件或逐步回执。哈希相等只证明内容未变，不能替代首次实读。
- **失效重载**：新会话、上下文压缩后原文未保留、切换源码/安装根、内容变化、只保留摘要，或不能确认原文仍可用时，重新按声明范围实读。不能凭磁盘缓存、其他代理阅读或历史确认行声称已经读过。
- **适用边界**：本节只复用静态共享规则。每步分级、真实工具调用、当前状态/计划/源码新鲜度、独立审查及阶段 Read/assessment 仍按各自合同执行；步骤明确要求重新 Read 的业务证据不得复用。全流程、fast、独立步骤与续跑均使用本节，不因入口不同豁免。

**多 Read 追加**：本步骤额外要求 Read 其他 references 时，**追加**在 step 文件强制思考前置段落末，格式 `+ Read [references/xxx.md](../references/xxx.md) 完整内容` 即可。当前已识别的多 Read 场景：

- `02_review.md` / `log.md`：+ Read [references/adversarial.md](../references/adversarial.md) 完整内容（对抗模式）
- `doc.md`：+ Read [references/doc_template.md](../references/doc_template.md) 完整内容（doc 模板）
- `docx.md`：+ Read [tools/docx/README.md](../tools/docx/README.md) 完整内容（自管运行时与视觉验收边界）

## 分级思考（reasoning gate）规则

每个步骤开始前，**先按 reasoning gate 分级（L0～L3），再按等级选择思考载体**——这是不可跳过的硬性前置。等级由机器可检查信号驱动（默认等级 + 确定性升级触发器），不允许代理用「我觉得不需要」跳过思考；也不允许在 L0/L1 用调用 sequential-thinking 凑仪式化合规（记录 `over_invoked`，灰度观察）。

**分级模型**（默认等级表与升级触发器唯一真源 = `mcp/reasoning-gate/gates.json`，本节只给摘要）：

| 等级 | 适用问题 | 必须动作 | sequential-thinking |
|---|---|---|---|
| **L0 确定性执行** | 输入明确、单一路径、结果可由脚本直接校验（status/list/help/install/bak/learn） | 执行现有状态/文件/schema/安全门禁；`mechanism=deterministic_checks` | 不调用 |
| **L1 简短决策** | 有少量取舍但根因/方案已被直接证据确认（readme/ppt/docx/study/close/reopen/worktree/init/doc/limit/merge） | 写简短决策记录（study 写库外私有溯源；工单步骤复用 `.decision_anchors.json`）；`mechanism=decision_record` | 不调用 |
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

> **判定 MCP 是否可用**（仅 L2/L3；L0/L1 不做发现探测）：先区分当前会话工具、宿主注册、独立服务握手三个事实，不能互相替代。
>
> **第 0 判据·直接可见即可用**：先检查当前宿主提供的工具目录。目标工具已有完整 schema（标准 `mcp__<server>__<tool>` 或代理前缀）时直接调用，禁止再以 ToolSearch 空结果否定它；没有暴露也不能直接断言未安装。可见性检查不替代实际调用。
>
> **第一步·直接 ToolSearch 验证**：仅在工具不可见且宿主实际提供 ToolSearch 时，先用 `select:mcp__sequential-thinking__sequentialthinking` 精确发现，无命中后再用 `sequential-thinking` 补充发现。无 ToolSearch 的宿主不虚构调用，直接进入下方配置读取判定。每个宿主工具目录版本只需发现一次；会话重载、目录变化、配置修复或原调用失败后必须重新检查，不能用旧发现结果跳过本步实际思考。
>
> **配置读取判定**：按实际宿主检查，Claude Code 为 `~/.claude.json`，Codex 为 `~/.codex/config.toml` / `codex mcp list`，CodeBuddy / WorkBuddy 分别为其 `mcp.json`；仅在该宿主支持时另查项目 `.mcp.json`。优先用 `python3 tools/mcp_doctor.py --json` 得到不含配置值的只读诊断。不能用 Claude 配置代替 Codex 配置，不能把读取失败当作未注册。
>
> - 发现不到但已注册：区分静态入口/平台/PATH 错误、握手失败、工具过滤和当前会话未加载。只有实际日志或协议结果支持时才指定根因；配置存在不证明服务已安装完整，独立 MCP 握手成功不证明宿主已暴露工具。
> - Windows 的 Node + npx-cli.js 检查子进程 Node PATH；`node_runner_not_found` 先按现有安装器的 PATH 构造修复，不用延长超时替代根因修复。
> - 默认诊断不执行配置中的命令、不下载依赖、不写配置；有授权的修复先展示具体差异、备份，再验证原启动环境及重载后的真实工具调用。原会话未重新暴露时保留待验，禁止称完全恢复。
>
> **第二步·首选路径执行**：实际调用 sequential-thinking 3～5 步（步骤另有要求除外），每步完成本步骤声明的职责；L3 另做独立对抗。调用成功才记工具成功；实际错误/超时按真实结果进入降级，不把发现动作、配置健康或其他代理调用写成工具执行成功。
>
> **降级路径的合法前置**：先完成上述发现与宿主检查，只能按已取得的事实选择以下分组，保留真实失败/发现依据并完成结构化文字思考。
>
> | 组 | 前置事实 | 声明 |
> |---|---|---|
> | 配置缺失组 | 工具不可见，宿主与适用项目配置实际可读且均无该服务；有 ToolSearch 时完成精确及模糊发现 | `强制思考: 降级文字块（未配置 server：<实际宿主配置路径>；按该宿主安装入口补注册）` |
> | 注册但未暴露组 | 工具不可见、发现无命中或宿主无发现工具，实际宿主配置有该服务 | `强制思考: 降级文字块（配置有 server，本会话工具未暴露：<已核实原因或尚未确定>；<修复及重载后验证动作>）` |
> | 调用失败组 | 当前工具可见/发现成功，实际调用返回错误或超时 | `强制思考: 降级文字块（工具实际调用失败：<具体错误类别>）` |
>
> 不能满足分组事实时保留未知并继续取证；不得虚构 ToolSearch、调用尝试或成功。按原 trace 合同记录真实边界，不把独立协议客户端结果冒充当前会话调用。无论采用何种载体，思考本身与证据要求均不可省略。

## 通用流程（每步执行）

1. **分级**：按 [mcp_per_step.md](mcp_per_step.md)「默认等级表」+ `mcp/reasoning-gate/gates.json` 升级触发器确定本步等级（L0～L3），把等级与触发原因写入 `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`（每 step 最终一行；schema/词表见 [thinking_detail.md](thinking_detail.md)「thinking gate trace」段）。
2. 输出 `ultrathink` 触发词（触发更长的内部推理 budget）——**L0/L1 可省略**（纯机器门禁/决策记录不依赖推理预算）。
3. **加载本步骤适用的 references**：按「共享规则加载」首次实读或失效重载，实际 Read 后输出 `📖 已 Read references/xxx.md`；满足复用条件时引用已有来源，不伪造本步新 Read。
4. **ICODE 本地 MCP 路由门（所有等级）**：读取 `mcp/icode-mcp-policy/policy.json` 当前 step 的 routes；若 `icode-mcp-policy.evaluate_call` 当前会话可见则用它复核目标 server/tool/operation，否则直接按同一 JSON 判定。只有 condition 被当前事实满足才调用目标服务；`required=true` 的命中项在工具可见时必须先实际调用；工具未暴露时记录发现依据，随后按 [mcp_integration.md](mcp_integration.md) 的底层确定性工具降级。policy 缺失/损坏时默认拒绝新增 MCP 调用，不影响原有 Read/rg/git/ssh/adb 路径。不得为探测 MCP 而读取无关工程文件。
5. **MCP 调用 gate**（L2/L3 不可跳过）：在结构化思考开始前，先处理本步 🟢 MCP（按 [mcp_per_step.md](mcp_per_step.md)「强证据场景判定」）：
   - 列出本步满足强证据场景的 🟢 MCP（**不含 sequential-thinking**，它由第 5 步承载；其余 🟢 MCP 由本 gate + 各 step 执行步骤内嵌点承载）
   - 对每个 🟢 MCP：**若该工具已在工具列表直接可见（完整 schema）则直接调用**，不可见才 ToolSearch 取 `mcp__<name>__<tool>` schema -> **实际调用一次** -> 把调用结果（成功/空/失败）写进思考块「MCP 调用」段
   - 调用失败/返回空 -> 思考块写明降级原因（MCP 不可用 / 无相关结果 / 不适用场景）才能跳过；**工具可调用但未经实际调用就标降级 = 反偷懒第 21 条违规**；工具未暴露按下文 unavailable_before_call 合同记录，不伪造尝试
   - ⚪ MCP（强证据场景不满足）无需评估无需声明
   - **本步若无 🟢 MCP**（全 ⚪）：gate 直接通过，思考块记"本步无 🟢 MCP（强证据场景均不满足）"
6. **完成思考（按等级选载体）**：
   - **L0**：只执行既有机器门禁（状态/文件/schema/安全），无思考块。
   - **L1**：写 `.decision_anchors.json` 决策摘要（`reasoning_gate` 对象，字段见 [decision_anchors.md](decision_anchors.md)「L1 决策记录契约」），列事实、风险和验证动作。
   - **L2/L3**：sequential-thinking MCP 优先（3～5 步，每步对应该步骤声明的子项之一；L3 另加独立对抗），不可用则降级文字块。
7. 不得跳过思考直接产出——所有 Write/Edit 必须在思考证据之后（L0 为机器门禁通过后）。

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
- catalog的 `evidence_fields/evidence_types`是必需字段/类型合同；called必须attempted=true，cache_hit必须有效cache_key和input_digest；fast未进入阶段只需mode/phase且不得伪装为full跳过。真实调用结果的来源引用应保留，不能仅自报字符串证明调用。
- 工具coverage只检查调用声明与形状，不能证明实际执行、输入覆盖、Read理解或Dedup语义裁决；阶段覆盖证据另按[审查证据合同](inspection_evidence.md)检查。历史不足不自动补造trace。
- **trace 禁止保存**：thought 正文（`thought`/`thought_text`/`raw_thought` 等字段名）、密钥、Cookie、设备凭据、大段日志正文
- 同一 step 重跑允许追加新行，校验器以最后一条为当前状态并保留历史
- **L0/L1 出现 sequential-thinking 调用**：记录 `over_invoked=true`（灰度观察项，默认不阻断）；`--strict` 时升级为阻断
- 兼容规则：旧工单没有 trace → `legacy-untracked` 警告，不阻断 `status`/`list`/`readme`/`close`；新工单在步骤转换前校验当前步骤 trace

## cheap-research 执行门（gate）流程

> 把 cheap-research 的「双保险」从两套自然语言提示词升级为**幂等可机检**状态机。
> gate 机器真源 = `mcp/cheap-research/gates.json`（阈值**只从这里读**，禁止在 step 文档/脚本各自写常量）；
> 运行痕迹 = `{ICODE_OUT_DIR}/.mcp_gate_trace.jsonl`（每 gate 一条最终判定，JSON Lines）；
> 校验器 = `python3 tools/lint_mcp_coverage.py <out_dir> [--step <step>] [--strict] [--json] [--require-trace]`。
> 新建工单 metadata 必须含 `"mcp_gate_schema_version": 2`；旧工单缺失时校验器输出 legacy-untracked 兼容警告，不阻断。

**gate 全流程（每步到对应执行点时执行）**：

1. **加载 gate catalog**：Read `mcp/cheap-research/gates.json`，取本 step 相关 gate 与阈值（`tb_comment_extract_min` / `long_text_threshold_bytes` / `dedup_min_functions` / `merge_min_rounds` / `max_input_bytes_per_call`）。
2. **确定性计算 eligibility 并立刻写 trace**：按 gates.json 的 condition + 事实文件（TB 评论数 / 候选日志字节 / 函数 catalog / review round 数 / mode）算出 `eligible`，先追加一行 trace（`decision` 暂填 `pending`，`at` 为当前 ISO-8601）；**不得用"我觉得没必要"当 skip 理由**。
3. **eligible 时先查缓存**：Read `{ICODE_OUT_DIR}/.cheap_research_cache.json` 查 `tool + args_hash`（语义见 SKILL.md「cheap-research 15 工具会话内缓存」段）。
4. **有效缓存命中**：把 trace 行更新为 `decision=cache_hit`、`attempted=false`、`result=success`、`cache_key=<args_hash>`、`input_digest=<包含tool/args及源码hash的64位sha256>`——**gate 直接 fulfilled，不再重复调用**。
5. **未命中才实际调用**：调 `mcp__cheap-research__<tool>`（先可见性自检；不可见才 ToolSearch 取 schema）。调用成功/返回空/失败后**更新最终 trace**：
   - 成功 → `decision=called`、`attempted=true`、`result=success`、`source_files=[...]`
   - 空/错误/超时 → `decision=degraded_after_attempt`、`attempted=true`、`result=empty|error|timeout`、`error_class=<类名>`
   - 并把结果写回缓存（atomic 写 `.tmp` + `mv`）
   - 工具未暴露且发现流程仍无法取得 schema → `decision=unavailable_before_call`、`attempted=false`、`result=unavailable`，并带 `availability={tool_visible:false,discovery_ref:<会话工具发现证据引用>,reason:not_exposed|discovery_failed,fallback:<替代方法>,evidence_ref:<替代结果引用>}`。工具直接可见不得用此分支，不伪造实际尝试；业务 eligibility 仍由事实计算。
6. **step 正文到达同一 gate 时读取最终 trace**：已 fulfilled（called/cache_hit/degraded_after_attempt/unavailable_before_call 已记录）则复用，**避免 A/B 两层重复调用**；未 fulfilled 才执行上述流程。
7. **step 转换前运行 validator**：`python3 tools/lint_mcp_coverage.py {ICODE_OUT_DIR} --step <step> --strict`——有 eligible 未履行 gate 时不得标记该步流程合规，先回补再转换。

**trace 行约束**（校验器强制）：

- `decision` 词表：`called` / `cache_hit` / `skipped_not_eligible` / `skipped_stage_not_reached` / `degraded_after_attempt` / `unavailable_before_call`
- `eligible=true` 只允许 `called` / `cache_hit` / `degraded_after_attempt` / `unavailable_before_call`；`degraded_after_attempt` 必须 `attempted=true` 且 `result=error|empty|timeout`
- `eligible=false` 只允许 `skipped_not_eligible` / `skipped_stage_not_reached`，且必须有结构化 `evidence`（不能只写自然语言）
- catalog的 `evidence_fields/evidence_types`是必需字段/类型合同；called必须attempted=true，cache_hit必须有效cache_key和input_digest；fast未进入阶段只需mode/phase且不得伪装为full跳过。真实调用结果的来源引用应保留，不能仅自报字符串证明调用。
- 工具coverage只检查调用声明与形状，不能证明实际执行、输入覆盖、Read理解或Dedup语义裁决；阶段覆盖证据另按[审查证据合同](inspection_evidence.md)检查。历史不足不自动补造trace。
- **trace 禁止保存**：工具完整结果、日志正文、API key、Cookie、远程 URL 查询参数、设备凭据
- 同一 `step + gate_id` 重跑允许追加新行，校验器以最后一条为当前状态并保留历史

**候选导航不等于裁决**：cheap-research 输出始终是候选/摘要，主代理负责实证与最终结论；根因、架构、对抗、修复、终审裁决一律不回落到 cheap-research。

## 层级关系（API 层 / Hook 层 / Prompt 层 概览）

- API 层：`CLAUDE_CODE_EFFORT_LEVEL=max` + `model=opus`（控制推理 effort）
- Hook 层：`UserPromptSubmit` 拦截 `/icode` 命令，缺思考证据时注入提醒
- Prompt 层：SKILL.md「强制思考前置」段 + 本文件 + 各 step 文件声明的子项
