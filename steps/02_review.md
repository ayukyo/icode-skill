# 步骤 2 — 多轮专项审查

**命令**: `/icode review [N]`
**产出**: `{ICODE_OUT_DIR}/02_review.md`
**会话**: 主会话

采用**独立计划对比 + 多轮循环审查**模式：
- **首轮**：先基于原始需求独立编制简要计划，再与步骤1计划逐项对比，最后做6维度审查
- **后续轮次**：**增量审查**，只审查上一轮修改的部分 + 跨章节影响分析。**软上限 N 轮**（N 由 `/icode review [N]` 指定，默认 3）；达到 N 但**仍有新问题**时**自动延长 +2 轮**，直到连续 2 轮无新问题，或触达**硬上限** `absolute_cap = max(10, N×2)`
- **终止条件**：以下任一满足即终止——(a) 连续 2 轮无新问题；(b) 触达 `absolute_cap`（若此时仍有新问题，落盘告警并提示用户回到步骤1修计划）

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/.ico_metadata.json` 是否存在，缺失则报错。

## 执行流程

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/01_plan.md` 和 `.ico_metadata.json` 获取原始需求
3. **分步续跑检测**（必须在强制思考之前，决定本轮是首轮还是续跑）：
   - 解析命令参数获取 `max_rounds`：若 `/icode review N` 提供了正整数 N，则 `max_rounds = N`；否则 `max_rounds = 3`
   - 计算 `absolute_cap = max(10, max_rounds × 2)`（硬上限，防止无限循环）
   - 若 `.ico_metadata.json.status == "review_in_progress"`，从 metadata 恢复 `total_rounds` / `clean_rounds` / `max_rounds` / `absolute_cap` / `extended_rounds` / `pending_verification` 字段
   - 同时读取所有已存在的 `review_round_*.json` 汇总历史问题
   - 跳过已完成轮次，从当前 `total_rounds` 继续
   - 续跑时以 metadata 中保存的 `max_rounds` / `absolute_cap` 为准（首次执行时写入 metadata）
   - 输出续跑信息：`▶ 步骤2 续跑，从第{total_rounds}轮开始（已完成{total_rounds-1}轮，当前轮数上限{max_rounds}，已扩展{extended_rounds}次，硬上限{absolute_cap}轮）`
   - 否则初始化 `clean_rounds = 0`, `total_rounds = 1`, `extended_rounds = 0`，`max_rounds` 由参数决定，并设 `status = review_in_progress`，将 `max_rounds` / `absolute_cap` / `extended_rounds` 写入 metadata
4. **强制思考前置**（不可跳过，缺证据视为不合规；基于步骤3的续跑判定结果选择思考路径）：先输出 `ultrathink` 触发词；再完成结构化思考——**首选**调用 `sequential-thinking` MCP（至少 3 步），**MCP 不可用时降级**为输出 `### 结构化思考` 文字块（逐项完成，不可省略）。
   - **首轮**（`total_rounds == 1`）子项：需求分解 → 独立方案构思 → 对比要点预判
   - **续跑**（`total_rounds > 1`）子项：回顾历史轮次问题 → 增量审查范围界定 → 跨章节影响预判
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

> **产出要求**：本步骤产出的每条 issue **必须当场填写 `evidence_pointer`**（计划章节号/行号 + 代码路径:行号），作为步骤 2.5.5 对抗验证的输入底座。2.5 阶段无法提供证据回指的"问题直觉"不得作为 issue 提出——先回到 2.3/2.4 用 Read/Grep 实证定位，再提 issue。

**步骤 2.5.5 — 对抗验证（独立质疑者子代理，不可跳过）**：

步骤 2.5 产出的 issue 清单是**主代理单视角**的结论，存在确认偏误风险（自己审自己提的问题，容易"看着像对就收"）。本步骤强制引入**独立质疑者**对每条 issue 做对抗验证，只有经对抗仍成立的 issue（或步骤 2.4 已实证验证为 `confirmed` 的 issue）才能进入 `new_issues`。

**为什么必须对抗**：计划/代码里几乎总能找到"支持某个问题成立"的片段。只有当独立的一方尝试反驳却反驳不倒，这个 issue 才可信。未经对抗的 issue 视为"待验证"，不计入收敛判据。

**执行方式**：用 `Agent` 工具 spawn 3 个独立质疑者（**必须独立 spawn，不得由主代理自行扮演**，避免自我背书）。三视角分工：

> **零待对抗 issue 快速通道**：若**步骤 2.5 产出 0 条需对抗的 issue**（全维度无问题，或当轮 issue 全部来自 2.4 实证已标 `confirmed` 无需对抗），**跳过本步骤**，直接置 `adversarial_verification = null`，进入步骤 2.6 写入。不得为空清单 spawn 质疑者浪费资源。

| 质疑者 | 视角 | 攻击点 |
|--------|------|--------|
| **证据质疑者** | issue 引用的证据真在文件里吗 | 核对 `evidence_pointer` 指向的计划章节/代码行是否真实存在、是否断章取义、是否误读时间戳/上下文 |
| **替代解释者** | 有没有别的原因能解释 | 必须提至少 1 个竞争解释：该 issue 描述的"问题"是否其实是合理设计/已有约束/误读？能否被推翻？ |
| **充分性质疑者** | 现有证据够不够 | `rejection_risk` 是否被证据充分支撑？还缺什么证据才能定论？ |

**输入契约**（喂给每个质疑者的内容，三份相同）：
- `01_plan.md` 路径 + 相关代码文件路径（让质疑者自己读，**不喂主代理的推理过程**）
- 待验证的 issue 清单（含 `id` / `affected_sections` / `suggestion` / `rejection_risk` / `evidence_pointer`）
- 指令：「独立读文件判断每条 issue，返回裁决 + 具体反驳/支持依据 + 日志/代码行回指。默认怀疑，证据不足时判 `needs_more_evidence` 而非 `confirmed`。」

**裁决汇总**（主代理收集 3 个质疑者结果后判定每条 issue 最终状态）：

**优先级**（按顺序判定，命中即定，未命中则进入下一档；三档都不命中走兜底）：

1. **`refuted`**：≥2 个质疑者判 `refuted`，**或**替代解释者给出被证据支撑的竞争解释且证据质疑者确认原证据失效 → **撤销**，记入 `refuted_issues` 并写明被推翻原因
2. **`needs_more_evidence`（一票降级）**：充分性质疑者判证据不足 → 降级进 `pending_verification`，标 `[未验证-证据不足]`，**不计入 `new_issues`**（证据充分性是 confirmed 的前提，享有否决权）
3. **`confirmed`**：≥2 个质疑者判 `confirmed`（且未被前两档命中）→ 保留进 `new_issues`
4. **兜底 `needs_more_evidence`**：以上三档均未命中（如三态分裂 `1 confirmed + 1 refuted + 1 needs_more`、或 `2 needs_more + 1 其他` 等）→ 降级进 `pending_verification`，如实记录"对抗未收敛"

> **为什么充分性质疑者一票降级**：`confirmed` 的前提是"证据充分支撑结论"。若充分性质疑者认定证据不足，即便另外两人判 confirmed，结论也站不住——此时确认等于伪造共识。故证据充分性享有否决权。
>
> **为什么需要兜底**：三人三态裁决可能出现无任何状态≥2票的分裂组合。兜底确保这类"对抗未收敛"的 issue 不会因规则漏洞被误判为 confirmed，也不会被丢弃——一律降级待验证。

**诚实底线**：若 3 个质疑者对某条 issue 裁决严重分裂、无法形成多数意见，不得"和稀泥"默认 `confirmed`——必须降级为 `needs_more_evidence` 并在产出中如实记录"对抗未收敛"。**宁可降级，也不伪造共识。**

**输出对抗记录**：把每个质疑者的裁决 + 依据 + 最终状态汇总写入 `adversarial_verification` 字段（见步骤 2.6 JSON 结构）。

> **独立性硬约束**：质疑者只能拿到"文件路径 + issue 清单 + 证据指针"，**严禁**把主代理的推理链/分析过程原样塞给质疑者当既定事实。质疑者必须自己读文件独立判断，否则对抗形同虚设。

**步骤 2.6 — 写入结果**：
以 JSON 格式写入 `{ICODE_OUT_DIR}/review_round_1.json`，包含：independent_plan_summary、file_review（files_read + key_findings）、comparison_analysis、dimension_results、adversarial_verification（每个质疑者的裁决+依据+最终状态；**零待对抗 issue 即跳过对抗验证时为 `null`**）、has_new_issues、new_issues（仅含 `verification_status == confirmed` 的 issue，含步骤 2.4 实证 confirmed 与步骤 2.5.5 对抗 confirmed 两类来源，每条遵循下方 Issue 结构化模板）、refuted_issues（被对抗推翻的 issue + 推翻原因）、pending_verification（`needs_more_evidence` 的 issue，标 `[未验证-证据不足]`）、summary。

再追加写入 `{ICODE_OUT_DIR}/02_review.md`，格式为：

````markdown
## 第N轮审查
```json
{完整 JSON 内容}
```
````

### 后续轮次 — 增量审查（`total_rounds > 1`，不再重复通读文件）

**增量审查范围**：

1. **修改区域审查**：只审查上一轮 new_issues 导致计划修改的章节，而非全量重审
2. **跨章节影响分析**：检查修改区域对其他章节的连带影响（如接口变更影响调用方、数据结构变更影响解析逻辑）
3. **断言验证跟进**：审查上一轮 `[未验证]` 断言是否已在计划更新中解决
4. **遗漏深挖**：基于之前轮次的发现继续深入，检查更深层次风险

维度同首轮，但仅针对增量范围。**增量轮次同样必须执行步骤 2.5.5 对抗验证**（只对增量 issue），不得因"上一轮已审过"而跳过对抗。**增量轮的"断言验证跟进"若发现新的断言验证失败，同样适用步骤 2.4 实证快速通道**（直接标 `confirmed` 计入 `new_issues`，无需对抗），但必须填写 `evidence_pointer`。

写入 `review_round_{total_rounds}.json` 后再追加写入 `02_review.md`。

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
