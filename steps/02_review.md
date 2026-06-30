# 步骤 2 — 多轮专项审查

**命令**: `/icode review [N]`
**产出**: `{ICODE_OUT_DIR}/02_review.md`
**会话**: 主会话

> **fast 模式行为（区分两种场景）**（`metadata.mode == "fast"`）：详见 [steps/fast.md](fast.md)。「自动串联」与「单步升级」两类场景行为不同，判定依据是用户**是否带参 N**：
>
> - **场景一·自动串联**（`/icode fast` 调起步骤2，**未带参 N**，`param_max_rounds` 为空）：fast 精简语义，**固定 1 轮、无对抗验证**——`max_rounds` 强制为 1、跳过步骤 2.5.5 对抗（步骤 2.5 产出 issue 直接标 `verification_status=confirmed` 计入 `new_issues`，**降级为单视角审查**，由用户自负其责）、循环控制 `total_rounds >= 1` 直接终止。输出标记：`▶ 步骤2 fast 模式：1 轮审查，无对抗验证`
> - **场景二·单步升级**（fast 工单上用户显式跑 `/icode review N`，**带了正整数 N**，`param_max_rounds` 非空）：视为 fast→full 升级意图，**N 优先级最高**——`max_rounds = N`、`absolute_cap = max(10, N × 2)`、**恢复步骤 2.5.5 对抗验证**、走正常 (a)(b)(c) 循环控制（**不触发** fast 特例）。输出标记：`▶ 步骤2 fast 工单单步升级：按 N={N} 轮 + 对抗验证执行`
> - **场景判定**：见步骤3「分步续跑检测」——以 `param_max_rounds` 是否非空区分（非空→场景二升级；空→场景一锁死1轮）

采用**独立计划对比 + 多轮循环审查**模式：
- **首轮**：先基于原始需求独立编制简要计划，再与步骤1计划逐项对比，最后做6维度审查
- **后续轮次**：**增量审查**，只审查上一轮修改的部分 + 跨章节影响分析。**软上限 N 轮**（N 由 `/icode review [N]` 指定，默认 3）；达到 N 但**仍有新问题**时**自动延长 +2 轮**，直到连续 2 轮无新问题，或触达**硬上限** `absolute_cap = max(10, N×2)`
- **终止条件**：以下任一满足即终止——(a) 连续 2 轮无新问题；(b) 触达 `absolute_cap`（若此时仍有新问题，落盘告警并提示用户回到步骤1修计划）；(c) `clean_rounds < 2` 但 `total_rounds > max_rounds`（已用满轮数预算但未达连续2轮 clean，正常终止，详见「循环控制」）

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/.ico_metadata.json` 是否存在，缺失则报错。

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
4. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规）；基于上述第3步「分步续跑检测」的判定结果选择思考路径）：
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
- 对每个现有源文件，从头到尾阅读：函数/结构体/宏定义签名、调用关系、命名风格、错误处理模式
- 对每个计划新建文件，列出其对外的接口承诺

**输出通读记录**：列出读过的文件路径 + 关键发现，特别是计划**未提及但实际代码存在**的约束。

**步骤 2.4 — 断言验证审查**：
重点审查计划中标记为 `[未验证]` 的断言，优先用 Read/Grep 实证验证。验证失败的问题直接记为 issue。

> **实证 issue 的 `verification_status`**：本步骤已用 Read/Grep 实证验证失败的 issue，证据确凿，**直接标 `verification_status = confirmed`**，无需再进步骤 2.5.5 对抗验证（已有铁证，对抗无意义）。但仍必须填写 `evidence_pointer`（指向实证的代码行/文件）。这类 issue 直接计入 `new_issues`。

**步骤 2.5 — 逐维审查（6个维度，全部覆盖）**：
1. 逻辑合理性、2. 流程完整性、3. 场景覆盖度、4. 风险遗漏、5. 落地可行性、6. 现有实现对照

> **数值/数学边界自检**（针对涉及数值计算的算法，如 lcm、gcd、pow、sqrt 等）：审查计划中的"预期结果"必须**自行验证数学正确性**，不能照搬历史经验。常见陷阱：
> - `46341² ≈ 2.147×10⁹` < INT_MAX，不溢出（√INT_MAX≈46340.95）
> - `50000² = 2.5×10⁹` > INT_MAX，溢出
> - `INT_MAX * 2` 必溢出
> - `INT_MIN * -1` 溢出
> - `gcd(0,0)` 数学未定义，工程需明确约定
> 计划中所有"预期结果为 X"类断言（特别是测试期望值），应在 2.4 阶段用 Read/Grep + 数学推导验证；无法验证的标 `[未验证-数值边界]`

> **产出要求**：本步骤产出的每条 issue **必须当场填写 `evidence_pointer`**（计划章节号/行号 + 代码路径:行号），作为步骤 2.5.5 对抗验证的输入底座。2.5 阶段无法提供证据回指的"问题直觉"不得作为 issue 提出——先回到 2.3/2.4 用 Read/Grep 实证定位，再提 issue。

**步骤 2.5.5 — 对抗验证（独立质疑者子代理，不可跳过）**：

> **fast 场景一跳过对抗**（`FAST_LOCKED == true`，即 `/icode fast` 自动串联、未带参 N）：不 spawn 任何质疑者子代理，直接把步骤 2.5 产出 issue 标 `verification_status=confirmed` 计入 `new_issues`。`adversarial_verification` 字段写 `null` 并标注「fast 场景一：无对抗」。**这是设计上的单视角审查，由用户自负其责**——fast 入口警告已明示。
>
> **fast 场景二恢复对抗**（`FAST_LOCKED == false`，即 fast 工单上显式跑 `/icode review N` 升级）：**与 full 模式完全一致**——必须 spawn 3 个独立质疑者子代理做对抗验证，不得跳过。fast→full 升级一旦触发即恢复完整对抗流程。

步骤 2.5 产出的 issue 清单是**主代理单视角**的结论，存在确认偏误风险。本步骤强制引入**独立质疑者**对每条 issue 做对抗验证，只有经对抗仍成立的 issue（或步骤 2.4 已实证验证为 `confirmed` 的 issue）才能进入 `new_issues`。

**对抗模式**（3质疑者/subagent_type=schema 强制结构化/裁决优先级/诚实降级/独立性硬约束/零待对抗快速通道/子代理失败处理）——**必须先 Read [references/adversarial.md](../references/adversarial.md) 完整内容**（不得凭概述/记忆执行）。本步骤分析对象 = 步骤 2.5 产出的 issue（步骤 2.4 实证 issue 例外，已有铁证直接 `confirmed` 无需对抗）。

> **子代理失败处理**（实测痛点：质疑者偶尔只返回开场白/被截断）：**禁止改由主代理自演裁决**。失败时按 adversarial.md「子代理失败处理」重试1次→仍失败诚实降级为 `[未验证-子代理对抗失败]` 计入 `pending_verification`，绝不伪造 `confirmed`。主代理 Read/Grep 实证铁证不算自演（属事实核查），判断性结论才必须独立 spawn。

> **log 阶段对抗验证结论复用**（针对方式D log→start 工单）：如果当前工单来自 `/icode log` 入口（`completed_steps` 含 `"log"`），log 阶段已对根因做对抗验证（3 质疑者独立 spawn），步骤2 **可复用**该结论，不需重新 spawn 3 质疑者对抗根因。但**仍需**对"步骤1 计划本身"（9 章节结构、ADR 合理性、错误处理充分性等）做 3 轮审查（不依赖对抗验证）。复用的具体方式：把 log_analysis.md 第 6 章「对抗分析记录」作为已确认的根因引用，在 review_round_*.json 中标注 "log_phase_adversarial=reused" 字段。

**输入契约**（喂质疑者）：`01_plan.md` 路径 + 相关代码文件路径 + 待验证 issue 清单（含 `id`/`affected_sections`/`suggestion`/`rejection_risk`/`evidence_pointer`）。

**输出对抗记录**：把每个质疑者的裁决 + 依据 + 最终状态汇总写入 `adversarial_verification` 字段（见步骤 2.6 JSON 结构）。裁决结果分桶：`confirmed` 进 `new_issues`、`refuted` 进 `refuted_issues`、`needs_more_evidence` 进 `pending_verification`。

**步骤 2.6 — 写入结果**：
以 JSON 格式写入 `{ICODE_OUT_DIR}/review_round_1.json`，包含：independent_plan_summary、file_review（files_read + key_findings）、comparison_analysis、dimension_results、adversarial_verification（每个质疑者的裁决+依据+最终状态；**零待对抗 issue 即跳过对抗验证时为 `null`**）、has_new_issues、new_issues（仅含 `verification_status == confirmed` 的 issue，含步骤 2.4 实证 confirmed 与步骤 2.5.5 对抗 confirmed 两类来源，每条遵循下方 Issue 结构化模板）、refuted_issues（被对抗推翻的 issue + 推翻原因）、pending_verification（`needs_more_evidence` 的 issue，标 `[未验证-证据不足]`）、summary。

再写入 `{ICODE_OUT_DIR}/02_review.md`（**人类可读摘要，不嵌套完整 JSON**），格式为：

````markdown
## 第N轮审查

### 维度结果
{6维度一句话结论}

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
6. **全流程模式**：
   - 若 `unresolved_issues_at_cap == true`：**暂停**全流程串联，输出 `⚠️ 步骤2 存在未解决问题，请手动决定是否继续 /icode merge 或回到 /icode plan`
   - 否则：**立即继续执行步骤3**
