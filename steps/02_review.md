# 步骤 2 — 多轮专项审查

**命令**: `/icode review [N]`
**产出**: `{ICODE_OUT_DIR}/02_review.md`
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 前置产物缺失（`01_plan.md` 不存在） | 报错退出，提示先跑 `/icode plan` |
| **L1·致命** | 当前工单是 debug 工单（`metadata.debug == true`） | 报错退出，提示：`/icode review` 不接受 debug 工单（debug 工单不入索引、不参与主流程，纯作为正常工单的对照；详情见 [references/debug_mode.md](../references/debug_mode.md)） |
| **L2·关键** | 触达 `absolute_cap = max(10, N×2)` 仍有新问题 | 落盘告警 + 记入 metadata（`unresolved_issues_at_cap=true`）+ 流程继续（不阻断；user 可事后回 plan 修订） |

**L3·重要**（矩阵段定义）：每轮 `clean_rounds` 未达 2 但有 new_issues 进自动延长（流程继续）。

> **fast 模式行为（区分两种场景）**（`metadata.mode == "fast"`）：详见 [steps/fast.md](fast.md)。「自动串联」与「单步升级」两类场景行为不同，判定依据是用户**是否带参 N**：
>
> - **场景一·自动串联**（`/icode fast` 调起步骤2，**未带参 N**，`param_max_rounds` 为空）：fast 精简语义，**固定 1 轮、无对抗验证**——`max_rounds` 强制为 1、跳过步骤 2.5.5 对抗（步骤 2.5 产出 issue 直接标 `verification_status=confirmed` 计入 `new_issues`，**降级为单视角审查**，由用户自负其责）、循环控制 `total_rounds >= 1` 直接终止。输出标记：`▶ 步骤2 fast 模式：1 轮审查，无对抗验证`
> - **场景二·单步升级**（fast 工单上用户显式跑 `/icode review N`，**带了正整数 N**，`param_max_rounds` 非空）：视为 fast→full 升级意图，**N 优先级最高**——`max_rounds = N`、`absolute_cap = max(10, N × 2)`、**恢复步骤 2.5.5 对抗验证**、走正常 (a)(b)(c) 循环控制（**不触发** fast 特例）。输出标记：`▶ 步骤2 fast 工单单步升级：按 N={N} 轮 + 对抗验证执行`
> - **场景判定**：见步骤3「分步续跑检测」——以 `param_max_rounds` 是否非空区分（非空→场景二升级；空→场景一锁死1轮）

采用**独立计划对比 + 多轮循环审查**模式：
- **首轮**：先基于原始需求独立编制简要计划，再与步骤1计划逐项对比，最后做7维度审查
- **后续轮次**：**增量审查**，只审查上一轮修改的部分 + 跨章节影响分析。**软上限 N 轮**（N 由 `/icode review [N]` 指定，默认 3）；达到 N 但**仍有新问题**时**自动延长 +2 轮**，直到连续 2 轮无新问题，或触达**硬上限** `absolute_cap = max(10, N×2)`
- **终止条件**：以下任一满足即终止——(a) 连续 2 轮无新问题；(b) 触达 `absolute_cap`（若此时仍有新问题，落盘告警并提示用户回到步骤1修计划）；(c) `clean_rounds < 2` 但 `total_rounds > max_rounds`（已用满轮数预算但未达连续2轮 clean，正常终止，详见「循环控制」）

## 前置校验

> **读决策锚点**（启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取上游关键决策摘要（requirement_digest/key_decisions/design_4dims/deviations/open_risks）作本步骤上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/.ico_metadata.json` 是否存在，缺失则报错。

> **模块文档检索（新增）**：在强制思考前置之前，Read `~/.claude/icode_data/project_docs/<project_id>/<branch_safe>/_meta.json` 取 `module_deps` 列表，对每个 dep 检 `~/.claude/icode_data/module_docs/<key>/_meta.json` 的 `current_commit` 是否与 `_meta.json.module_deps[].commit` 一致（不一致标 `⚠️ commit 漂移`，仍读但附警告）。命中模块的章节（如 `01_overview.md` / `02_api.md`）作为上下文**只进思考块，不写入产物文件**——本步骤（review）是消费方，模块文档已在 01_plan 阶段由段零检索固化到 `01_plan.md` §1.5 工程结构快照（如果当时命中），本步骤无须再写入产物（避免重复污染 + 与"消费方不二次写入"契约一致）。**降级**：路径不存在或 Read 失败 → 静默跳过本段，主流程继续（与 01_plan 段零检索降级策略一致）。**复用缓存**：本 ticket 内已通过 01_plan 段零检索注入过的模块文档不重复 Read（`_inject_cache.json` 按 `(source, ref_id, slice)` 去重，slice=`section:<file>`，与 01_plan 共用同一缓存文件，路径 `{ICODE_OUT_DIR}/_inject_cache.json`——参见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段）。**前提契约**：本段依赖 01_plan 阶段已执行过段零检索（**正常 `/icode start` 全流程自动满足**）；若用户先跑 `/icode review` 再补跑 `/icode plan`（异常场景），`_inject_cache.json` 不存在 → 退化为"全量 Read 模块文档"（不报错，行为降级），写 `▶ 步骤 2 模块文档检索退化：无 _inject_cache.json 可复用，全量 Read`。

**用户语义变更检测（O-4 语义冻结，写 requirement_deltas）**：读 `metadata.scope_contract`（缺失视为 null＝未冻结，跳过本检测，向后兼容旧工单）；若**用户本次输入**改变了冻结契约的语义——状态身份或生命周期、允许/拒绝条件、持久化一致性或回滚承诺、验收条件/调用方语义/真实环境验证场景——不得静默按新语义继续审查，须**先分类写入 metadata `requirement_deltas`**（追加，字段缺失视为 `[]`）：`clarification_only`（仅澄清不改变实现，可继续）/ `a_now_with_evidence`（改变 A 档但已有直接证据，记 impact 后按 A 档处理）/ `needs_user_confirm`（需用户确认，未确认前**停止自动串联**等待确认）/ `needs_replan`（需回到 plan/review 重新定稿，**先停本步骤提示用户重跑 plan**）。**delta 未分流前不得继续扩大代码设计或验收矩阵**（冻结点）。每条含 `{at, user_input_summary, changed_aspect, classification, impact, user_confirm}`，见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「requirement_deltas 字段族」。

## 执行流程

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `.ico_metadata.json` 获取原始需求（`requirement` 字段）。**注意**：本步骤**只读 metadata 取原始需求，不读 `01_plan.md`**——`01_plan.md` 留到步骤 2.2 对比分析时再读，避免步骤 2.1 独立编制计划时受步骤1计划污染
3. **分步续跑检测**（必须在强制思考之前，决定本轮是首轮还是续跑，并判定 fast 场景）：
   - **解析命令参数**：若 `/icode review N` 提供了正整数 N，记 `param_max_rounds = N`（非空）；否则 `param_max_rounds` 为空。
   - **判定 fast 场景**（读 `metadata.mode`，缺失视为 `"full"`）：若 `mode == "fast"`，按本文件顶部「fast 模式行为」区分两种场景，**参数是否带 N 是场景判定的唯一依据**：
     - **场景一·自动串联**（`param_max_rounds` 为空，即 `/icode fast` 调起、未带参 N）：设标志 `FAST_LOCKED = true`。`max_rounds` 强制为 1，`absolute_cap = max(10, 1 × 2) = 10`（但**永远不触达**，场景一不走延长）。后续步骤 2.5.5 跳过对抗、循环控制走 fast 特例（`total_rounds >= 1` 直接终止）。
     - **场景二·单步升级**（`param_max_rounds` 非空，即 fast 工单上显式跑 `/icode review N`）：设 `FAST_LOCKED = false`。`max_rounds = param_max_rounds`、`absolute_cap = max(10, param_max_rounds × 2)`，**恢复步骤 2.5.5 对抗验证**、走正常 (a)(b)(c) 循环控制（**不触发** fast 特例）。
     - 即：fast 模式下 **`param_max_rounds` 非空→场景二升级（N 生效）；空→场景一锁死1轮**——这是 fast→full 升级机制的核心（详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「步骤2/5 读 mode 字段的契约」段）。
   - 若 `mode != "fast"`（含缺失视为 full）：`FAST_LOCKED = false`，`param_max_rounds` 正常参与 `max_rounds` 决策。
   - 若 `.ico_metadata.json.status == "review_in_progress"`，**续跑**（审查中断未终止）：从 metadata 恢复 `total_rounds` / `clean_rounds` / `extended_rounds` / `pending_verification` 字段；`max_rounds` / `absolute_cap` 按**新参数优先**原则——若 `param_max_rounds` 非空，则 `max_rounds = param_max_rounds`、`absolute_cap = max(10, param_max_rounds × 2)`，并更新 metadata；否则沿用 metadata 旧值（首次执行时写入）。**场景一 `FAST_LOCKED=true` 时强制 `max_rounds=1`（覆盖上述决策）**。读取所有已存在的 `review_round_*.json` 汇总历史问题，跳过已完成轮次，从当前 `total_rounds` 继续
   - 输出续跑信息：`▶ 步骤2 续跑，从第{total_rounds}轮开始（已完成{total_rounds-1}轮，当前轮数上限{max_rounds}，已扩展{extended_rounds}次，硬上限{absolute_cap}轮）`
   - 否则**首轮初始化**（status 为 `plan_done`/`review_done`/其他非 in_progress 态）：`status=review_done` 表示上一轮审查已收敛终止，再调 `/icode review` 视为**重新审查**——`clean_rounds = 0`, `total_rounds = 1`, `extended_rounds = 0`，`max_rounds` 由参数决定（`param_max_rounds` 非空用 `param_max_rounds`，否则默认 3），`absolute_cap = max(10, max_rounds × 2)`，设 `status = review_in_progress`，将 `max_rounds` / `absolute_cap` / `extended_rounds` 写入 metadata。**场景一 `FAST_LOCKED=true` 时强制 `max_rounds=1`（覆盖上述决策）**。**重新审查会覆盖旧 `review_round_*.json` 与 `02_review.md`**——若用户想在中断处续跑，应确保 status 是 `review_in_progress`（中断态）而非 `review_done`（终止态）
4. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）；基于上述第3步「分步续跑检测」的判定结果选择思考路径：
   - **首轮**（`total_rounds == 1`）子项（至少3步）：需求分解 → 独立方案构思 → 对比要点预判
   - **续跑**（`total_rounds > 1`）子项（至少3步）：回顾历史轮次问题 → 增量审查范围界定 → 跨章节影响预判
5. 输出步骤确认：`▶ 步骤2 审查开始（{max_rounds}轮内完成；如最后一轮仍有新问题，自动延长 +2 轮，最多扩展至 {absolute_cap} 轮）`

### 首轮审查（`total_rounds == 1`）

**步骤 2.1 — 独立编制计划**：
基于原始需求独立编制一份简要项目计划（架构思路、功能模块、核心接口、实现步骤），**不要参考步骤1计划**。

**步骤 2.2 — 对比分析**：
读取步骤1计划，与你的独立计划逐项对比：遗漏点、偏差点、多余点，给出裁决。

**步骤 2.3 — 逐文件通读（必须先执行）**：
从步骤1计划中识别所有涉及的文件（新建文件、修改文件、依赖文件），逐一通读。

**依赖关系审查（grep 优先）**：对计划涉及的每个待改符号，`grep -rn '<symbol>('` 找所有调用方（"这个函数被哪些地方调用？"），结果作为维度6"现有实现对照"的依赖关系证据；跨仓库/子仓库检索见 反偷懒第 21 条「跨仓库/子仓库检索」段。检索结果只进思考块，不写入产物文件。
- 对每个现有源文件，从头到尾阅读：函数/结构体/宏定义签名、调用关系、命名风格、错误处理模式
- 对每个计划新建文件，列出其对外的接口承诺

**输出通读记录**：列出读过的文件路径 + 关键发现（无通读记录=没读=不合规），特别是计划**未提及但实际代码存在**的约束。

**步骤 2.4 — 断言验证审查**：
重点审查计划中标记为 `[未验证]` 的断言，优先用 Read/Grep 实证验证。验证失败的问题直接记为 issue。

> **实证 issue 的 `verification_status`**：本步骤已用 Read/Grep 实证验证失败的 issue，证据确凿，**直接标 `verification_status = confirmed`**，无需再进步骤 2.5.5 对抗验证（已有铁证，对抗无意义）。但仍必须填写 `evidence_pointer`（指向实证的代码行/文件）。这类 issue 直接计入 `new_issues`。

**步骤 2.5 — 逐维审查（7个维度，全部覆盖）**：
1. 逻辑合理性、2. 流程完整性、3. 场景覆盖度、4. 风险遗漏、5. 落地可行性、6. 现有实现对照、7. 必要性

> **维度 4「风险遗漏」子项（防"语义碰撞"型根因遗漏）**：本修改涉及的状态值若来自外部模块（SDK / 其他进程 / 共享库），必须额外勾对以下 3 条（缺失任一视为审查不完整，对抗验证可直接攻击「未做跨模块枚举对照」）。**前置证据**：log 工单应已 Read `log_analysis.md §2.2 跨模块枚举对照表`，本维度审查以该表为对照基线；init 工单无此表，按 plan §4.5 维度 2 子项的「跨模块枚举对齐」设计态独立审查：
> - [ ] 是否对照了上下游枚举定义（两侧 file:line 都贴出）？是否存在「同名不同义」风险（如上游某枚举值 N = 终局态，下游某枚举值 N = 过渡态，或反之）？
> - [ ] 修复是否落在正确的边界层——优先「数据入口一次归一化」，避免「N 处散补丁」（后者会让修改面膨胀、未来同类型根因再次出现时无收敛点）？是否识别出哪些消费者不受本修复影响（如 nav 转发保留原值）？
> - [ ] 归一化后是否保留原始值用于日志 / 调试（双值日志），防止归一化后丢失上游语义信息导致二次定位困难？

> **维度 7「必要性」（防重复实现）**：审查计划的每个功能点是否在解决一个**尚未被现有代码解决**的问题——全工程 `rg -in '<需求关键词>'` 检索（不限计划涉及模块）+ Read 命中处上下文、消费点追行为链，确认无等价实现。完整执行规则见 [references/necessity_check.md](../references/necessity_check.md)。**发现等价实现 → 实证 issue，走步骤 2.4 实证快速通道**（Read/Grep 证据确凿，直接标 `verification_status=confirmed` 计入 `new_issues`，**无需进 2.5.5 对抗**——已有铁证，对抗无意义）：`功能点 X 已由 file:line 实现，计划重复`，`evidence_pointer` 指向命中处，建议"删除功能点或改为复用现有实现"。**判定要点**："现有实现路径存在" ≠ "已覆盖需求"；"新实现写出来也不会执行到（被已有入口/拦截先返回挡掉）" = 重复，比"功能近似"更确凿。
>
> **数值/数学边界自检**（针对涉及数值计算的算法，如 lcm、gcd、pow、sqrt 等）：审查计划中的"预期结果"必须**自行验证数学正确性**，不能照搬历史经验。常见陷阱：
> - `46341² ≈ 2.147×10⁹` < INT_MAX，不溢出（√INT_MAX≈46340.95）
> - `50000² = 2.5×10⁹` > INT_MAX，溢出
> - `INT_MAX * 2` 必溢出
> - `INT_MIN * -1` 溢出
> - `gcd(0,0)` 数学未定义，工程需明确约定
> 计划中所有"预期结果为 X"类断言（特别是测试期望值），应在 2.4 阶段用 Read/Grep + 数学推导验证；无法验证的标 `[未验证-数值边界]`

> **产出要求**：本步骤产出的每条 issue **必须当场填写 `evidence_pointer`**（计划章节号/行号 + 代码路径:行号），作为步骤 2.5.5 对抗验证的输入底座。2.5 阶段无法提供证据回指的"问题直觉"不得作为 issue 提出——先回到 2.3/2.4 用 Read/Grep 实证定位，再提 issue。

**步骤 2.5.5 — 对抗验证（独立质疑者子代理，不可跳过）**：

> **[已废弃段·issue 分组 summarize 设计]（自当前版本起不再生效，仅作变更追溯）**：曾设计在 spawn 3 质疑者子代理之前调 cheap-research `summarize` 给主代理做"issue 分组概览"，自检发现以下问题已废弃——
> ①summarize 是 LLM 推理（非廉价机械），调用成本与"主代理手工分组"相比无优势
> ②主代理从 issue 列表本身（id/affected_sections/suggestion/rejection_risk/evidence_pointer）就能直接做"维度分配"，无需额外压缩
> ③相关审计字段与 adversarial_verification 既有 schema 兼容成本 > 收益
> ④summarize 输出是自然语言，主代理从自然语言抽"分组"是又一次 LLM 推理，与直接看结构化 issue 列表等价
> **结论**：价值低、复杂度高、风险中，**移除**。主代理在 spawn 3 质疑者时直接按 `affected_sections` 字段做维度分配（既有行为）。
>
> ⚠️ **本段不生效**——主代理看到此段时**跳过**，继续走下方 "fast 场景一跳过对抗" 段。AI 不应复活此段逻辑。

> **fast 场景一跳过对抗**（`FAST_LOCKED == true`，即 `/icode fast` 自动串联、未带参 N）：不 spawn 任何质疑者子代理，直接把步骤 2.5 产出 issue 标 `verification_status=confirmed` 计入 `new_issues`。`adversarial_verification` 字段写 `null` 并标注「fast 场景一：无对抗」。**这是设计上的单视角审查，由用户自负其责**——fast 入口警告已明示。
>
> **fast 场景二恢复对抗**（`FAST_LOCKED == false`，即 fast 工单上显式跑 `/icode review N` 升级）：**与 full 模式完全一致**——必须 spawn 3 个独立质疑者子代理做对抗验证，不得跳过。fast→full 升级一旦触发即恢复完整对抗流程。

步骤 2.5 产出的 issue 清单是**主代理单视角**的结论，存在确认偏误风险。本步骤强制引入**独立质疑者**对每条 issue 做对抗验证，只有经对抗仍成立的 issue（或步骤 2.4 已实证验证为 `confirmed` 的 issue）才能进入 `new_issues`。

**对抗模式**（3质疑者/subagent_type=schema 强制结构化/裁决优先级/诚实降级/独立性硬约束/零待对抗快速通道/子代理失败处理）——**必须先 Read [references/adversarial.md](../references/adversarial.md) 完整内容**（不得凭概述/记忆执行）。本步骤分析对象 = 步骤 2.5 产出的 issue（步骤 2.4 实证 issue 例外，已有铁证直接 `confirmed` 无需对抗）。**质疑者 prompt 组装后过 anti-coaching 扫描 + freshness 检查**（见 [references/adversarial.md](../references/adversarial.md)「输入契约」段）；续跑/增量轮**禁止**把历史对抗记录（上轮裁决/依据/Agent ID）喂给本轮质疑者。

> **spawn 等待规格**（引用 [references/adversarial.md](../references/adversarial.md)「显式等待 + 超时机制」段）：spawn 3 质疑者必须**显式等 verdict**——统一走 `Agent` **后台 spawn（`run_in_background: true`）拿 task_id + `TaskOutput(task_id, block=true, timeout=...)` 阻塞等**（唯一可机械执行超时的等待方式，超时把控制权交回主代理）。**禁止**：后台 spawn 后被动等任务通知（挂死/断连永无通知，看门狗失守）/ `run_in_background: false` 裸同步 spawn 当唯一等待手段（`Agent` 工具无 timeout 参数，挂死卡死主代理）/ spawn 后不等待直接进下一步。**超时档位**：首次 spawn 走 10 分钟档（`TaskOutput(block=true, timeout=600000)`，`BACKGROUND_WATCHDOG_SECONDS=600`，超时判疑似断连 → `TaskStop` → 前台重来）；前台重来/重试走 `TIMEOUT_SECONDS = 120` 档（可由 metadata.task_timeout_seconds 覆盖，超时先 `TaskStop` 停句柄再重试 1 次，换措辞 + 可换 subagent_type 兜底），二次仍超时走 `[未验证-子代理对抗失败]`。**禁止**未等待就标 `[未验证-子代理对抗失败]`——该标签留给「确认失败」的子代理，不得给「仍在跑/返回晚」的子代理（2026-07-29 实测踩坑）。判定状态四态枚举（`sync_ok` / `timeout_retry_used` / `still_failed_after_retry` / `env_no_spawn`）必须写入 `adversarial_verification` 字段便于审计（详见 [references/adversarial.md](../references/adversarial.md)「显式等待 + 超时机制」段）。**防 `max_output_tokens` 截断**：质疑者 spawn 须含输出预算硬约束（verdict 第一动作/禁回显/总输出≤2000 token 宽松上限勿压紧，见 adversarial.md「spawn 规格要求」第 4 条）。
> **整合墙钟硬截止**（引用 [references/adversarial.md](../references/adversarial.md)「整合墙钟硬截止」段，双保险第二层，防"等待质疑者返回后整合定稿"拖成几小时）：spawn 3 质疑者后对抗等待+整合阶段**墙钟上限 20 分钟**（`INTEGRATION_WALL_CLOCK_DEADLINE_SECONDS=1200`）。**3 质疑者必须一次性并行后台 spawn**（全拿 task_id 再逐个 `TaskOutput` 阻塞等，禁止串行 spawn——串行总等待放大 3 倍）；**到 deadline 无论还差几个未返回，立即强制收敛**：未返回者先 `TaskStop` 停句柄 → 按**已返回** verdict 执行裁决优先级整合 → 未返回者标 `[未验证-子代理对抗失败]` 计入 pending → **禁止为等最后一个质疑者延长 deadline / 无限等待**（宁可少验一条，不卡流程几小时）。
> **子代理失败处理**（实测痛点：质疑者偶尔只返回开场白/被截断）：**禁止改由主代理自演裁决**。失败时按 adversarial.md「子代理失败处理」重试 2 次（含 1 次换 subagent_type）→仍失败诚实降级为 `[未验证-子代理对抗失败]` 计入 `pending_verification`，绝不伪造 `confirmed`。主代理 Read/Grep 实证铁证不算自演（属事实核查），判断性结论才必须独立 spawn。

> **log 阶段对抗验证结论复用**（针对方式D log→start 工单）：如果当前工单来自 `/icode log` 入口（`completed_steps` 含 `"log"`），log 阶段已对根因做对抗验证（3 质疑者独立 spawn），步骤2 **可复用**该结论，不需重新 spawn 3 质疑者对抗根因。但**仍需**对"步骤1 计划本身"（9 章节结构、ADR 合理性、错误处理充分性等）做 3 轮审查（不依赖对抗验证）。复用的具体方式：把 log_analysis.md 第 6 章「对抗分析记录」作为已确认的根因引用，在 review_round_*.json 中标注 "log_phase_adversarial=reused" 字段。

**输入契约**（喂质疑者）：`01_plan.md` 路径 + 相关代码文件路径 + **`rg -in '<需求关键词>'` 命中的非计划文件**（质疑者看不到重叠文件就永远不会质疑必要性，grep 命中文件必须喂入——主代理自己都不知道已有等价实现时，只有重叠文件能让质疑者发现它）+ 待验证 issue 清单（含 `id`/`affected_sections`/`suggestion`/`rejection_risk`/`evidence_pointer`）。

**输出对抗记录**：把每个质疑者的裁决 + 依据 + 最终状态汇总写入 `adversarial_verification` 字段（见步骤 2.6 JSON 结构）。**每个质疑者必须记录独立 spawn 的 Agent ID**（如 `agentId: ac32afbc15a278f3f`）——无 Agent ID=未独立 spawn=自演=不合规，必须重跑对抗。裁决结果分桶：`confirmed` 进 `new_issues`、`refuted` 进 `refuted_issues`、`needs_more_evidence` 进 `pending_verification`。


**步骤 2.5.6 - over-design 审查（反偷懒第 26 条）**：检查 plan 修复方案是否分 A/B/C 三档呈现。检查点：①分档？②A 档真根因（非兜底）？③B 档标注"A 修复后触发概率"？④机制层修复是否被误归 B 档（应按"不改会复现吗"判定）？⑤A 档标"跨工程"是否有证据（非借跨工程逃避实施）？判定：B/C 混入 A 档主方案 = `confirmed` issue（需 plan 修订分档）。**核对 metadata.fix_tiers**：plan 未把分档落盘（字段缺失但 `03_plan_final.md` §4.5 有分档文本）→ 提示 plan 补落盘；落盘分档与文本分档不一致 → `confirmed` issue（需 plan 修订）。对抗质疑者追问补："这个修改点是 A 还是 B？B 在 A 修复后还会触发吗？机制层不改会复现吗？A 档标跨工程有证据吗？"

**审查中发现新问题的范围升级（scope_escalations，反偷懒第 33 条）**：审查过程发现**计划之外**的新问题/新架构信号（新增持久化协议、全局门控、生命周期语义、跨职责边界组件、新故障模型），拟建议纳入实施范围的——**不得静默采纳**（审查采纳 ≠ 实施授权），须按 `scope_escalations` 分类并写 metadata：标 `A_now` 必须给出**直接复发证据链**（回答"不做这一项、完成已有 A 项后，哪个已记录证据场景会再次产生原故障"），无证据回指**默认 `B_confirm`**（需用户确认，未获确认前不得进入编码，与 `confirmed_B_fixes` 机制一致）；标 `C_follow_up` 进范围外；`refuted` 丢弃。分类结果追加写入 metadata `scope_escalations`（字段缺失视为 `[]`；每条含 `at`/`source_step`/`change_desc`/`classification`/`evidence`/`user_confirm`/`impact`）。

**步骤 2.5.7 — 语义重复函数检测（轻量 top 5）**：

> **强证据场景判定**（详见 [references/mcp_per_step.md §2 review](../references/mcp_per_step.md)）：
>
> - cheap-research 🟢（`mcp__cheap-research__extract` 可用）
> - **函数数 ≥ 50**（ripgrep catalog.json 函数条目数判定）
>
> **任一不满足 → 整个 §2.5.7 跳过**，在思考块 `MCP 调用` 段写明降级原因，不写产物文件。

**执行步骤**（AI 直接照填）：

1. **函数目录抽取**（ripgrep 优先）：

   **优先用 ripgrep**（快 10 倍，一次性抽所有函数）：

   ```bash
   # 通用模式：匹配函数定义（C/C++/Java/Go/Rust/Python/JS/TS 主流 10 种）
   rg -n --no-heading \
     -e '^(static\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s+[*&]?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(static\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(async\s+)?function\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(async\s+)?(const|let|var)\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*(async\s+)?\(' \
     -e '^(async\s+)?(const|let|var)\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*(async\s+)?function' \
     -e '^def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^func\s+(\([^)]*\)\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(pub\s+)?fn\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^\s+(public|private|protected)?\s*(static\s+)?[a-zA-Z_][a-zA-Z0-9_*]+\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     --glob '!*.test.*' --glob '!*.spec.*' --glob '!**/__tests__/**' \
     "$PROJECT_ROOT" | head -2000
   ```

   输出按行解析 → catalog.json 格式：`[{file, name, line, signature, context}, ...]`。context 取函数定义行 + 后 5 行（用 `rg -A 5` 重抽或 Read 补足）。

   **ripgrep 不可用**（未装）：整个 §2.5.7 跳过
2. **函数数判定**（阈值与分批）：
   - 函数数 < 50 → 输出 `▶ §2.5.7 跳过：函数数 {N} < 50，工程规模太小无需 dedup`，整个 §2.5.7 结束
   - 函数数 > 500 → 分批（每批 100）。**理由**：high质量模型(用户配置)单次处理 5-10 函数合理，10000 函数全跑高质量模型不可能；500 函数通常分 ~30-50 sub_category，每个 5-15 函数，高质量模型 50 次 ≈ $0.04 cost。
3. **分类阶段（haiku 降本）——双层分类**：调 `mcp__cheap-research__extract` 输入 = catalog.json 文本 + **双层分类 schema**（实测单层分类后处理映射会跨家族合并——例如把"JSON解析"/"字典合并"/"列表过滤"全部归到 `data-transform`，高质量模型找重复时跨家族做无意义比较）。**必须用 `parent_category`（23 类标准化）+ `sub_category`（LLM 自由细粒度）双层**，高质量模型按 `sub_category` 分组工作，输出 → `{ICODE_OUT_DIR}/<ticket>/dedup/categorized.json`：

   ```json
   {
     "type": "object",
     "properties": {
       "results": {
         "type": "array",
         "items": {
           "type": "object",
           "properties": {
             "name": {"type": "string"},
             "parent_category": {"type": "string", "description": "父类,严格从 23 类清单选一(file-ops/string-utils/validation/error-handling/http-api/date-time/data-transform/database/logging/config/async-utils/testing/ui-helpers/crypto/provider-impl/tool-impl/event-handling/session-management/compaction/other/hardware-abstraction/protocol-impl/build-system)"},
             "sub_category": {"type": "string", "description": "子类,LLM 自由(双语标签:英文例 GPIO/UART/JSON解析,中文例 字符串长度/列表过滤),保持细粒度区分同父类下的不同家族"}
           }
         }
       }
     }
   }
   ```

   **prompt 模板必含的 3 个边界说明**：
   - **硬件抽象 vs 协议实现边界**：按实现层次分。**直接寄存器操作**（`volatile uint32_t *uart = ...; while(...)`）归 `hardware-abstraction`；**通过协议栈 API**（`uart_send_string()` 等抽象层）归 `protocol-impl`。
   - **sub_category 标签**：允许**英文 + 中文混合**。高质量模型找重复按 sub_category 字符串精确匹配，不影响。
   - **每个函数独立类别**：prompt 明确说"每个函数分配到合适类别"，避免 LLM 自由聚类（实测：模糊 prompt → 10/11 归 data-transform；明确 prompt → 25/25 严格归类）。

   **后处理映射**（必做，LLM 不严格遵守 23 类清单——实测会返回"Number Parsing"/"Math"/"String Manipulation"等自由类别）。**只映射 `parent_category` 字段**，`sub_category` 保持原样：

   | LLM 自由类别（parent_category）| 映射到 23 类 |
   | --- | --- |
   | Number Parsing / Math / Calculation / parsing / formatting | data-transform |
   | String Manipulation / 字符串操作 | string-utils |
   | Validation / Check / 验证 | validation |
   | Date / Time / Format / 日期 | date-time |
   | Logging / Print / 日志 | logging |
   | Error / Exception / 错误 | error-handling |
   | Network / HTTP / API | http-api |
   | DB / SQL / Query | database |
   | Config / Settings / 配置 | config |
   | Test / Mock / Fixture / 测试 | testing |
   | UI / DOM / Render | ui-helpers |
   | Crypto / Hash / Encrypt / 哈希 | crypto |
   | File / IO / Path | file-ops |
   | JSON解析 / 字典合并 / 列表过滤 (高级抽象) | data-transform (必须归此类,因 23 类无更细粒度) |
   | 其他无法映射 | other |

   写入 categorized.json 前用此表做 `parent_category` 字段归一化。`sub_category` 字段保持 LLM 自由输出（用于第 4 步拆分 + 第 5 步高质量模型按子家族分组）。
4. **排序选 top 5**：按 `sub_category` 函数数降序排序 `categorized.json`，**取 top 5 子家族**（不按 parent_category——否则跨家族被合并，高质量模型找重复无意义）。**长尾分布是预期行为**。< 3 的 sub_category 直接跳过（不调高质量模型找重复，避免低 ROI 成本）。
5. **找重复（高质量模型逐类）**：对每个 top 5 **sub_category**，调 `mcp__cheap-research__extract` 输入 = 子家族函数列表 + **简化 schema（嵌套字段用 string，规避 array-of-array schema validation failed）**。

   **⚠️ 高质量模型输出格式已知风险**：高质量模型输出**始终自适应**，主代理必须 try 链式解析：
   - **functions 字段 5 种格式**：
     - 格式 A：JSON 字符串数组 `[{file, name, line, notes}, ...]`（多函数 + 复杂时）
     - 格式 B：`|` 分隔符纯文本 `"name1: desc1 | name2: desc2"`（少函数 + 有差异描述）
     - 格式 C：`,` 分隔符纯文本 `"name1, name2, name3"`（少函数 + 简单列表，实测默认格式）
     - 格式 D：单个纯字符串（极端情况）
     - 格式 E：`;` 分隔多组纯文本（组内 `,` 分隔）`"name1, name2; name3"`（多组场景；**必须先检查 `;`**——`split(',')` 优先会破坏 `;` 分组，见下方代码骨架）
   - **recommendation 字段 3 种格式**：
     - 格式 A：完整 JSON `{"action": "CONSOLIDATE", "survivor": "fn", "reason": "..."}`
     - 格式 B：Python dict 风格 `{action: 'INVESTIGATE', survivor: null, reason: '...'}`（实测频繁出现，单引号 + 无引号 key）
     - 格式 C：自由中文段落（如"可统一为 list_filter_above..."，无 action 字段）

   **降级策略**：解析失败 → 标 `{action: "INVESTIGATE", reason: <原文本>}` 让用户人工审。**绝不可漏判**（宁可让用户审，不可错误合并/拆分）。
   - 格式 A：`functions` 是 JSON 字符串数组 `[{file, name, line, notes}, ...]`
   - 格式 B：`functions` 是带分隔符的纯文本 `"name1: desc1 | name2: desc2 | ..."`（函数数少时高质量模型倾向此格式）

   主代理代码骨架（含 4 种解析格式 + 2 个解析陷阱）：
   ```python
   import json
   import re

   def normalize_python_dict_to_json(s):
       """,
       JSON 标准下非法,主代理必须规范化。示例: {action: 'INVESTIGATE', ...} → {"action": "INVESTIGATE", ...}
       """
       if not isinstance(s, str): return s
       # 单引号字符串值 → 双引号（避免破坏字符串内的双引号）
       s = re.sub(r"'([^']*)'", r'"\1"', s)
       # 无引号 key → 加双引号
       s = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', s)
       return s

   def parse_functions_field(s):
       """高质量模型 functions 字段 5 种输出格式,主代理必须 try 链式解析。"""
       if not isinstance(s, str): return s
       # 格式 A: JSON 字符串数组
       try: return json.loads(s)
       except (json.JSONDecodeError, TypeError): pass
       # 格式 B: | 分隔符纯文本（"name1: desc1 | name2: desc2"）
       funcs = []
       for part in s.split("|"):
           if ":" in part:
               name, notes = part.split(":", 1)
               funcs.append({"name": name.strip(), "notes": notes.strip()})
       if funcs: return funcs
       # **格式 E 必须先检查**——高质量模型多组用 ; 分隔,内组用 , 分隔(实测发现 33:split(',') 优先会破坏 ; 分组)
       if ";" in s:
           funcs = []
           for part in s.split(";"):
               names = [n.strip() for n in part.split(",") if n.strip()]
               for n in names:
                   funcs.append({"name": n})
           if funcs: return funcs
       # 格式 C: 逗号分隔纯文本（"name1, name2, name3"，无 ; 时）
       funcs = [{"name": n.strip()} for n in s.split(",") if n.strip()]
       if funcs: return funcs
       # 格式 D: 单个纯字符串（极端情况）
       return [{"name": s.strip()}]

   def parse_recommendation_field(s):
       """高质量模型 recommendation 字段 3 种输出格式 + 1 个 Python dict 风格。"""
       if not isinstance(s, dict):  # 已是 dict
           try: return json.loads(s)  # 格式 A: JSON 字符串
           except (json.JSONDecodeError, TypeError): pass
           try: return json.loads(normalize_python_dict_to_json(s))  # 格式 B: Python dict 风格
           except (json.JSONDecodeError, TypeError): pass
           # 格式 C: 自由文本描述,默认 INVESTIGATE 让用户审
           return {"action": "INVESTIGATE", "survivor": None, "reason": s}
       return s  # 已是 dict

   高质量模型返回结果含 duplicates 数组，遍历["duplicates"]:
       dup["functions"] = parse_functions_field(dup["functions"])
       dup["recommendation"] = parse_recommendation_field(dup["recommendation"])

       # 用 categorized.json 回填 file/line
       for f in dup["functions"]:
           if "name" in f and (f.get("file") == "unknown" or not f.get("file")):
               cat_match = next((c for c in categorized if c["name"] == f["name"]), None)
               if cat_match:
                   f["file"] = cat_match["file"]
                   f["line"] = cat_match["line"]
   ```

   输出 → `{ICODE_OUT_DIR}/<ticket>/dedup/duplicates/<sub_category>.json`
6. **生成报告**：在 02_review.md 末尾追加 `## 语义重复检测报告（§2.5.7 轻量 top 5）` 段，格式：

   ```markdown
   ## 语义重复检测报告（§2.5.7 轻量 top 5）

   **函数总数**：{N} | **扫描类别数**：5/{K 总 sub_category} | **生成时间**：{ISO timestamp}

   ### HIGH 置信度重复（建议立即合并）
   | Intent | SubCategory | 推荐保留 | 应删除函数 |
   |--------|-------------|----------|-----------|
   | ...    | ...         | ...      | ...       |

   ### MEDIUM 置信度重复（建议人工审查）
   | Intent | SubCategory | 推荐保留 | 差异点 |
   |--------|-------------|----------|--------|
   | ...    | ...         | ...      | ...    |

   ### LOW 置信度（可能相关，时间允许时复核）
   | Intent | SubCategory | 函数对 |
   |--------|-------------|--------|
   | ...    | ...         | ...    |

   ### 已扫描但无重复
   | SubCategory | 函数数 | 备注 |
   |-------------|--------|------|
   | uart        | 3      | 发送 vs 接收方向不同,无重复 |
   | cmake       | 2      | 子家族<3 不进高质量模型,扫描跳过 |

   **中间产物**：`{ICODE_OUT_DIR}/<ticket>/dedup/{catalog,categorized,duplicates/*.json}`
   **复用**：本步骤生成的 `categorized.json` 可被 §5 05_deepcheck §9.4 复用（避免重跑分类）
   ```

   **⚠️ "已扫描但无重复"段必填**：高质量模型某 sub_category 返回 `duplicates: []` 时，**不省略该子家族**——必须显式列在"已扫描但无重复"段，让用户知道"该家族已扫描、确认无重复"，避免用户怀疑"是不是没跑"。

7. **产物文件附加**：每条 HIGH/MEDIUM 重复函数对同时作为 issue 计入本轮 `new_issues`（用下方「Issue 结构化模板」段），`evidence_pointer` 指向 `dedup/duplicates/<category>.json:<line>`，`suggestion` 写"合并为 `<survivor>` + 删除其他实现"，`verification_status` 直接标 `confirmed`（已用高质量模型推理，**无需再进步骤 2.5.5 对抗验证**——单视角推理质量足够（详细理由同 §2.5.7），且 §2.5.5 的覆盖范围是"步骤 2.5 维度审查 + 步骤 2.4 实证"两类 issue，不含 §2.5.7 dedup）。

**降级路径**：

- cheap-research 不可用 → 整个 §2.5.7 跳过，记 `[降级-cheap-research 不可用]`
- extract 返回 `schema_validation_failed` → 重试 1 次（自动改 instruction 加"严格按 schema 输出"），仍失败标"分类降级-单类跳过"
- 高质量模型某类返回空数组 → 该类跳过（无重复），不报错

**反偷懒第 21 条合规**：步骤末尾在思考块输出 `cheap-research 调用: extract x {1+5}` 或对应降级声明，**无记录 = 违规**。

**与 §2.5.5 对抗验证的衔接**：dedup 的 issue **不进入** §2.5.5 对抗验证流程（§2.5.5 的覆盖范围是"§2.5 维度审查 + §2.4 实证"两类 issue）。理由：dedup 用高质量模型单次推理 + cheap-research schema 强约束 + 23 类预定义约束 = 等效"强约束推理"，质量足够；重复 3 次 spawn 成本翻 3 倍但收益边际递减。


**步骤 2.6 — 写入结果**：
以 JSON 格式写入 `{ICODE_OUT_DIR}/review_round_1.json`，包含：independent_plan_summary、file_review（files_read + key_findings）、comparison_analysis、dimension_results、adversarial_verification（每个质疑者的裁决+依据+最终状态；**零待对抗 issue 即跳过对抗验证时为 `null`**）、has_new_issues、new_issues（仅含 `verification_status == confirmed` 的 issue，含步骤 2.4 实证 confirmed 与步骤 2.5.5 对抗 confirmed 两类来源，每条遵循下方 Issue 结构化模板）、refuted_issues（被对抗推翻的 issue + 推翻原因）、pending_verification（`needs_more_evidence` 的 issue，标 `[未验证-证据不足]`）、summary。

再写入 `{ICODE_OUT_DIR}/02_review.md`（**人类可读摘要，不嵌套完整 JSON**），格式为：

````markdown
## 第N轮审查

### 维度结果
{7维度一句话结论}

### 对抗验证
- 质疑者1（Agent ID）: 裁决 + 一句依据
- 质疑者2（Agent ID）: 裁决 + 一句依据
- 质疑者3（Agent ID）: 裁决 + 一句依据
- 最终: confirmed X / refuted Y / needs_more Z

### 结论
{has_new_issues + clean_rounds + 一句话总结}
````

> **02_review.md 不复制 review_round_*.json 全文**——JSON 文件单独存结构化数据供步骤3读取，02_review.md 只存人类可读摘要。

### 后续轮次 — 增量审查（`total_rounds > 1`，不再重复通读文件）

**增量审查范围**：

1. **修改区域审查**：只审查上一轮 new_issues 导致计划修改的章节，而非全量重审
2. **跨章节影响分析**：检查修改区域对其他章节的连带影响（如接口变更影响调用方、数据结构变更影响解析逻辑）
3. **断言验证跟进**：审查上一轮 `[未验证]` 断言是否已在计划更新中解决
4. **遗漏深挖**：基于之前轮次的发现继续深入，检查更深层次风险

维度同首轮，但仅针对增量范围。**增量轮次同样必须执行步骤 2.5.5 对抗验证**（只对增量 issue），不得因"上一轮已审过"而跳过对抗。**增量轮的"断言验证跟进"若发现新的断言验证失败，同样适用步骤 2.4 实证快速通道**（直接标 `confirmed` 计入 `new_issues`，无需对抗），但必须填写 `evidence_pointer`。

> **review_round_*.json 写入规则**（避免空文件噪音）：仅当本轮有 `new_issues` 或 `pending_verification` 或 `refuted_issues` 中任意一类非空时才写 `review_round_{total_rounds}.json`；clean 轮（无 issue）跳过写文件，仅在 02_review.md 中标注 "第 N 轮：clean"。避免 N 轮审查产生 N 个空 JSON 文件。

写入 `review_round_{total_rounds}.json` 后追加写入 `02_review.md`（**人类可读摘要格式同首轮，不嵌套 JSON**）。

### Issue 结构化模板

每条 issue 必须包含以下字段（首轮和后续轮次统一）：

```json
{
  "id": "R{轮次}-{序号}",
  "affected_sections": ["受影响的计划章节编号/标题"],
  "suggestion": "具体建议修改内容",
  "rejection_risk": "若不采纳可能导致的后果",
  "evidence_pointer": "证据回指——计划章节号/行号 + 代码路径:行号（如 '01_plan.md §3.2 / src/driver.c:142'），做不到回指的 issue 不得标记为 confirmed",
  "verification_status": "验证最终状态（步骤2.5.5对抗验证或步骤2.4实证验证）：confirmed / refuted / needs_more_evidence"
}
```

**字段约束**：
- `evidence_pointer` 是 issue 成立的客观底座。**无证据回指的 issue 一律视为 `needs_more_evidence`**，不得凭"经验判断""看起来不合理"直接确认。
- `verification_status` 由步骤 2.5.5 对抗验证填写（步骤 2.4 实证验证为 `confirmed` 的 issue 除外，详见步骤 2.4 说明）；未跑对抗验证且非 2.4 实证的 issue 默认 `needs_more_evidence`。
- `confirmed` 状态的 issue 计入 `new_issues`；`refuted` 记入 `refuted_issues`；`needs_more_evidence` 记入 `pending_verification`。

### 循环控制

> **`has_new_issues` 判定基准**：只有 `verification_status == confirmed` 的 issue 存在时 `has_new_issues = true`。`pending_verification`（证据不足）和 `refuted_issues`（被推翻）**不计入**新问题计数——前者是"待验证"而非"已确认问题"，后者已被对抗排除。这一基准防止"靠未验证的猜测撑轮次"或"靠已被推翻的伪问题假收敛"。

每轮结束后：

1. `total_rounds += 1`
2. **实时落盘**：保持 `status = review_in_progress`，写入当前 `total_rounds` / `clean_rounds` / `max_rounds` / `absolute_cap` / `extended_rounds` / `pending_verification` 到 metadata。`pending_verification` 维护规则：本轮新增的 `needs_more_evidence` issue 追加进清单；**已在后续轮被证实（升为 confirmed）或证伪（降为 refuted）的 issue 从清单移除**，避免已解决项残留；仅保留仍处于待验证状态的 issue。
3. **判定下一步**（按顺序检查，命中即定）：

   > **fast 场景一特例**（`FAST_LOCKED == true`，即 `/icode fast` 自动串联、未带参 N，最高优先级，命中即跳过 (a)(b)(c)）：
   > `total_rounds >= 1` 时**直接终止**——fast 场景一固定 1 轮无对抗，不走延长逻辑与连续 2 轮 clean 收敛。状态置 `review_done`，`clean_rounds` 保留当前值，`completed_steps` 追加 `"2"`。即使 `has_new_issues == true`，场景一也不强制回到步骤1——用户自负其责（fast 入口警告已明示）。
   >
   > **fast 场景二走正常循环**（`FAST_LOCKED == false`，即 fast 工单上显式跑 `/icode review N` 升级）：**不触发本特例**，落入下方 (a)(b)(c) 正常判定——可延长、可连续2轮clean收敛、可触达硬上限告警，与 full 模式一致。

   **(a) 触达硬上限**（`total_rounds > absolute_cap`）：
   - **终止**。若最后一轮 `has_new_issues == true`，落盘告警（见下"触达硬上限处理"）；否则按"无新问题"正常终止

   **(b) 有新问题（`has_new_issues == true`）**：
   - `clean_rounds = 0`
   - 若 `total_rounds <= max_rounds`：继续下一轮（正常流程）
   - 若 `total_rounds > max_rounds`（已达原定上限，但仍有问题）：**自动延长**——`max_rounds = min(max_rounds + 2, absolute_cap)`，`extended_rounds += 1`，输出 `🔄 第{total_rounds-1}轮仍有新问题，自动延长至{max_rounds}轮（已扩展{extended_rounds}次，硬上限{absolute_cap}轮）`，继续下一轮

   **(c) 无新问题（`has_new_issues == false`）**：
   - `clean_rounds += 1`
   - 若 `clean_rounds >= 2`：**正常终止**（连续 2 轮 clean，达到稳定收敛）
   - 若 `clean_rounds < 2` 且 `total_rounds <= max_rounds`：继续下一轮（在 `max_rounds` 内验证稳定性）
   - 若 `clean_rounds < 2` 且 `total_rounds > max_rounds`：**正常终止**（已用满用户指定/已扩展的轮数预算，不再为单轮 clean 跨过上限继续跑）

4. **触达硬上限处理**（分支 (a) 命中且最后一轮 `has_new_issues == true`）：
   - 在 `02_review.md` 顶部插入告警块：

     ```markdown
     ## ⚠️ 未解决问题告警

     已审查 {total_rounds-1} 轮（含 {extended_rounds} 次自动延长），触达硬上限 {absolute_cap} 轮，但**最后一轮仍发现新问题**。
     **建议**：回到步骤1（`/icode plan`）重新审视计划本身的根本性缺陷，而非继续在步骤2修补。
     **未解决问题概览**：见最后一轮 `review_round_{total_rounds-1}.json` 的 `new_issues` 字段。
     **待验证问题**：见 metadata `pending_verification`（证据不足未达 confirmed 的 issue，步骤3定稿时必须重点复核）。
     ```

   - 在 metadata 中记录 `unresolved_issues_at_cap = true`
   - 输出告警：`⚠️ 步骤2 触达硬上限{absolute_cap}轮仍有未解决问题，建议回到步骤1`

5. **终止后更新 metadata**：`status = review_done`，`completed_steps` 追加 `"2"`，保留 `extended_rounds` / `unresolved_issues_at_cap` / `pending_verification` 字段供后续步骤参考
6. **审查输出压缩（供 merge 步骤消费）**：调 `mcp__cheap-research__summarize` 压缩本轮审查输出（`review_round_*.json` 的 `new_issues` + 对抗裁决 + 维度审查结论），摘要 ≈ 300-500 token，写入 `{ICODE_OUT_DIR}/_review_summary.md`（**仅含**：审查轮次 + 总问题数 + 关键 HIGH 问题 + 未解决标记）。**降级**（cheap-research 不可用）：跳过，`_review_summary.md` 不存在时 merge 步骤直接读各轮 JSON 原文。
7. **全流程模式**：
   - 若 `unresolved_issues_at_cap == true`：**暂停**全流程串联，输出 `⚠️ 步骤2 存在未解决问题，请手动决定是否继续 /icode merge 或回到 /icode plan`
   - 否则：**立即继续执行步骤3**
## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| vision-bridge | 🟢* | 截图分析--用户给图时 |
| **cheap-research** | 🟢* | **降本**：diff_summary（增量审查）+ summarize（审查输出压缩，供 merge 步骤消费）+ fill_template（维度结果）+ retrieve_similar（历史 issue）+ scan_patterns（grep 扫描）+ trace_refs（引用追溯）。不接管决策：3 质疑者对抗/审查合成走主会话 |
| context7 | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
