# 步骤 3 — 吸纳评审意见、合并优化定稿

**命令**: `/icode merge`
**产出**: `{ICODE_OUT_DIR}/03_plan_final.md`
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 「定稿机器硬校验」不通过（`03_plan_final.md` 非完整计划副本：编号正文章节 <9 或缺「实现偏差备忘」段） | 报错退出，禁止进入步骤4，回到「写入定稿」把 `01_plan.md` 全文复制后再叠加标记 |
| **L1·致命** | 当前工单是 debug 工单（`metadata.debug == true`） | 报错退出，提示：`/icode merge` 不接受 debug 工单（debug 工单不入索引、不参与主流程，纯作为正常工单的对照；详情见 [references/debug_mode.md](../references/debug_mode.md)） |

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/02_review.md` 是否存在，缺失则报错并提示先执行对应步骤。

**用户语义变更检测（O-4，同 [02_review.md](02_review.md) 前置校验）**：读 `metadata.scope_contract`（缺失视为 null＝未冻结，跳过，向后兼容旧工单）；若**用户本次输入**改变冻结契约语义（状态身份或生命周期 / 允许或拒绝条件 / 持久化一致性或回滚承诺 / 验收条件、调用方语义或真实环境验证场景）——先分类写入 `metadata.requirement_deltas`（追加，分类枚举与判定同 02_review 前置：`clarification_only` / `a_now_with_evidence` / `needs_user_confirm` / `needs_replan`），**未分流不得继续本步骤**（`needs_user_confirm` 未确认 / `needs_replan` 未重跑 plan → 停止定稿流程等待处理）。若用户输入仅澄清不改变契约，则无 delta，正常继续。

## 执行步骤

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/01_plan.md`（原始计划）和 `{ICODE_OUT_DIR}/02_review.md`（审查意见）
3. **思考分级（L1：决策记录）**（按 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段执行）：本步骤为 L1，不调用 sequential-thinking；决策字段记入 `.decision_anchors.json`（见 [references/decision_anchors.md](../references/decision_anchors.md)「L1 决策记录契约」）= 逐条甄别审查意见 → 判断采纳/驳回 → 规划修改策略
4. 输出步骤确认：`▶ 步骤3 定稿开始`

### 合并定稿

> **步骤 3 merge 跨轮 review 汇总（新增，合并定稿前）**：步骤 2 末尾的 `_review_summary.md` 只是**单轮**压缩（`02_review.md` 第 453 行明确"压缩本轮审查输出"），多轮累积时主代理仍需读 N 个 JSON 文件手工合并。**本段新增跨轮汇总**：
> - 收集 `review_round_*.json`（按 `total_rounds` 顺序）+ metadata `pending_verification` 字段，做**跨轮 issue 合并**（confirmed 去重 / refuted 去重 / pending 状态继承），按"维度归属 + 严重度"分组
> - **gate 绑定**：本段 = gate `merge.cross_round_summary`（tool=summarize）。阈值 `merge_min_rounds`（gates.json 常量 =2，即 >1 轮才 eligible）
> - **多轮（review_rounds ≥ 阈值）**：调 `mcp__cheap-research__summarize(text=<合并后 issue JSON 字符串>, max_tokens=1000, focus="按维度归属 + 严重度分组")` 压到 ≤1K token，写入 `{ICODE_OUT_DIR}/_review_merged_summary.md`（**与单轮 `_review_summary.md` 共存**：单轮摘要作每轮历史快照，merged_summary 作多轮合并视图）→ trace `eligible=true, decision=called/cache_hit/degraded_after_attempt, evidence={review_rounds, threshold}`
> - 主代理看 merged_summary 决定采纳/驳回/分流，**不替代逐 JSON 细读**（细节仍以 JSON 为准）
> - **降级**：cheap-research 不可用 → 跳过合并 summarize，主代理直接读各轮 JSON 原文合并；写 `[降级-merge 跨轮 summarize 不可用]` + trace `decision=degraded_after_attempt, attempted=true`
> - **N=1 场景**（未触达自动延长）：`_review_summary.md` 已足够，跳过本段；写 `▶ merge 跨轮汇总跳过：仅 1 轮 review，合并无意义` + trace `merge.cross_round_summary: eligible=false, skipped_not_eligible, evidence={review_rounds:1, threshold:2}`——**必须写 trace**（让"合理没调用"与"漏调"可区分）

解析审查意见——如果 `{ICODE_OUT_DIR}/_review_summary.md` 存在，先读其**审查轮次 + 总问题数 + 关键 HIGH 问题 + 未解决标记**获取概览；再读 `review_round_*.json` 文件（按 `total_rounds` 顺序读取所有轮 JSON；**clean 轮无 JSON 文件**——步骤2 约定无 issue 的轮跳过写文件，遇到缺失的轮号直接跳过）提取结构化审查数据，重点关注：
- 首轮的 `file_review.key_findings`（通读实际代码发现的问题）
- 所有轮的 `new_issues`（**仅含 `verification_status == confirmed` 的问题**，含步骤 2.4 实证 confirmed 与步骤 2.5.5 对抗 confirmed 两类来源，含 `affected_sections`/`suggestion`/`rejection_risk`/`evidence_pointer` 结构化字段）
- 所有轮的 `refuted_issues`（被对抗推翻的 issue，**默认不采纳**，仅作记录）
- 所有轮的 `pending_verification`（`needs_more_evidence`，证据不足未达 confirmed，**必须重点复核**）

> **`pending_verification` 数据源**：以 `.ico_metadata.json` 的 `pending_verification` 字段为准（该字段是**最终仍待验证**的快照——步骤2会在后续轮动态移除已证实/证伪的 issue）。各轮 `review_round_*.json` 中的 `pending_verification` 仅是该轮当时的历史快照，**不重复处理**已解决项，仅作追溯用。若 metadata 缺失该字段，则回退到各轮 JSON 累积去重。

**要求**：
1. 逐条甄别审查意见（含 `new_issues` 和 `file_review.key_findings`），两者同等重要
2. 利用 issue 的 `affected_sections` 字段定位计划中需修改的章节，利用 `suggestion` 字段理解建议修改，利用 `rejection_risk` 评估否决后果，利用 `evidence_pointer` 回指验证问题确实存在
3. 对每条 issue 做出判断：采纳 / 部分采纳 / 否决。否决必须写明理由（rejection_reason）
4. `file_review.key_findings` 中的接口约束、命名模式、隐式依赖等，若计划未覆盖，必须补充
5. **`pending_verification` 复核**：对每条 `needs_more_evidence` 的 issue，定稿阶段必须补充证据后做出明确判断——能补证据证实的标 `[待验证-已证实]` 并采纳；仍无法证实的标 `[待验证-证据仍不足]` 并写入 `03_plan_final.md` 的风险评估章节作为"待验证假设"，**不得默认采纳也不得默认丢弃**
6. **`refuted_issues` 处理**：被对抗推翻的 issue 不纳入修改，但在定稿中标注 `[对抗否决 #编号: 推翻原因]`，留痕便于追溯
7. 每处修改标注 `[审查采纳 #编号]` 或 `[通读发现]` 或 `[审查否决 #编号: 理由]` 或 `[对抗否决 #编号: 推翻原因]` 或 `[待验证-已证实/证据仍不足]` 标记
8. 保持整体架构不变
9. 输出前必须自检：章节完整、编号连续、校验项 checkbox 格式正确
10. **`scope_escalations` 定稿检查（反偷懒第 33 条）**：Read `metadata.scope_escalations`（字段缺失视为 `[]`），检查 review 阶段（02_review 2.5.6）写入的分类记录——存在 `classification=B_confirm` 且未获用户确认的条目时，**定稿不得直接采纳实施**：向用户确认该条（确认 → 记录 `user_confirm` 后允许纳入定稿；拒绝/暂缓 → 标 `C_follow_up` 进范围外或 `refuted` 丢弃，不入定稿正文）；`A_now` 条目核对计划是否已含对应方案（未含 → 补入 §3 架构设计并在 §4.5 落盘 `fix_tiers`，**纳入后同步刷新 `scope_contract.summary`**——A_now 改变 A/B/C 分档即契约边界变化，与第 11 条 delta 分流刷新一致）；`C_follow_up`/`refuted` 不入定稿实施范围。未确认的 B_confirm 不得出现在 `03_plan_final.md` 实施范围中
11. **`requirement_deltas` 分流检查（O-4 语义冻结）**：Read `metadata.requirement_deltas`（字段缺失视为 `[]`），存在**未分流**条目（`classification` 未定，或 `needs_user_confirm` 未获 `user_confirm`，或 `needs_replan` 未重跑 plan 更新 `scope_contract`）时，**禁止定稿**——先完成分流：向用户确认 `needs_user_confirm`（确认 → 记录 `user_confirm` 后纳入定稿；拒绝 → 标 `clarification_only` 或排除）；`needs_replan` → 提示用户重跑 `/icode plan` 更新契约后再定稿；`a_now_with_evidence` → 核对计划已含对应方案（未含 → 补入）。已分流条目若改变 A 档/验收边界，同步刷新 `scope_contract.summary`

**冻结 TDD 测试契约**（行为变更 A 档，`metadata.tdd` 非 `exempt` 时）：定稿时把计划 §4.5 的 TDD 测试契约与 A 档方案**一起冻结**进 `03_plan_final.md`（目标行为 / 测试命令 / RED 预期 / GREEN 标准 / 回归范围）。review 若修改了目标行为 → **必须同步修改 RED 断言和 GREEN 标准**；**禁止**合并后只更新实现计划、不更新测试契约（测试契约与实现脱节 = 步骤4 无 RED 基线可循，步骤5/6 无法核验因果）。冻结的契约同步写入 `metadata.tdd`（`mode`/`reason`/`test_files`/`red.expected`/`green` 等）。

**写入定稿**：使用 Write 工具写入 `{ICODE_OUT_DIR}/03_plan_final.md`。

> **`03_plan_final.md` 必须是完整计划，不是元数据摘要**：先把 `01_plan.md` 全文**复制**为 `03_plan_final.md` 主体（保留 10 个正文章节（含 §10 需求质量清单），每段不省略），再在其上叠加审查意见采纳标记（如 `[审查采纳 #1]`）与末尾「实现偏差备忘」空段。**不要把"无修改"理解为"不复制"**——只要 01_plan.md 章节没有需要修改的地方，应直接复制全文；只有在采纳了审查意见需要修改时，才改动对应章节。步骤 5 逆推对比、步骤 6 追溯矩阵均以本文件为计划侧输入，仅写元数据会导致功能点无法回指代码位置。

### 定稿自检

读取刚写入的 `{ICODE_OUT_DIR}/03_plan_final.md`，逐项检查：

- 10 个正文章节完整（概述、功能需求、架构设计、架构决策记录ADR、详细设计、异常处理、实现步骤、校验项、风险评估、需求质量清单）
- **预留「实现偏差备忘」空段**（正文10章之外的附加段，不计入10章）：在 `03_plan_final.md` 末尾追加 `## 实现偏差备忘（步骤6 终审回写）` 空段（仅标题 + 占位说明"待步骤6 回写"），供步骤6 终审时回写实质偏差。定稿阶段不填内容
- 校验项 checkbox 格式正确（`- [ ]` 或 `- [x]`）
- 章节编号连续无重复
- 所有 [审查采纳] / [审查否决] / [对抗否决] / [待验证-已证实] / [待验证-证据仍不足] 标记与审查意见/对抗记录一一对应
- 任何缺失、断裂、矛盾处必须修复后再继续
- **无需添加新功能或重构，仅修复格式和结构问题**

### 定稿机器硬校验（L1，不通过不得进入步骤4）

**目的**（本轮实测教训）：定稿若写成"方案摘要"而非完整计划副本，步骤 5 逆推对比 / 步骤 6 追溯矩阵以它为计划侧输入会断裂，功能点无法回指代码位置。故在步骤4 之前做**机器校验**——不满足 = L1，报错阻止进入步骤4：

```bash
python3 -c "
import re,sys
t=open('{ICODE_OUT_DIR}/03_plan_final.md').read()
n=len(re.findall(r'^## \d+(\.\d+)?\. ', t, re.M))          # 编号正文章节（## 1. ~ ## 10.）
has_memo='实现偏差备忘' in t                                      # 末尾预留空段
print(f'编号正文章节: {n}（规格 10 节，判据 ≥9 兼容历史 9 节工单）; 含实现偏差备忘: {has_memo}')
sys.exit(0 if (n>=9 and has_memo) else 1)
"
```

- 退出码 0 → 通过，可进入步骤4；非 0 → **停止，报 L1**："`03_plan_final.md` 不是完整计划副本（正文章节数不足或缺「实现偏差备忘」段），禁止进入步骤4，请按上方「写入定稿」把 `01_plan.md` 全文复制为 `03_plan_final.md` 再叠加标记"
- 全流程模式下同样执行本门禁，通过后才推进步骤4

### 强制操作

- **更新 `.ico_metadata.json`**：`status = plan_finalized`，`completed_steps` 追加 `"3"`（写回前按 SKILL.md「status 写回校验」对照词表）
- 全流程模式：**通过「定稿机器硬校验」后立即继续执行步骤4**

## MCP 推荐

- **sequential-thinking**：本步骤为 L1 不调用（决策记录进 `.decision_anchors.json`），见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段
- **cheap-research**（🟢*）：**仅多轮 review（>1 轮）** 时，跨轮 issue 合并汇总调 `mcp__cheap-research__summarize`（见上方「合并定稿」段；N=1 轮跳过，不评估）。其他 5 个 MCP 本步骤不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
