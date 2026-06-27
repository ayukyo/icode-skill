# 步骤 3 — 吸纳评审意见、合并优化定稿

**命令**: `/icode merge`
**产出**: `{ICODE_OUT_DIR}/03_plan_final.md`
**会话**: 主会话

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/02_review.md` 是否存在，缺失则报错并提示先执行对应步骤。

## 执行步骤

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/01_plan.md`（原始计划）和 `{ICODE_OUT_DIR}/02_review.md`（审查意见）
3. **强制思考前置**（不可跳过，缺证据视为不合规）：先输出 `ultrathink` 触发词；再完成结构化思考——**首选**调用 `sequential-thinking` MCP（至少 3 步），**MCP 不可用时降级**为输出 `### 结构化思考` 文字块（逐项完成，不可省略）；每步/每项对应一个子项：逐条甄别审查意见 → 判断采纳/驳回 → 规划修改策略
4. 输出步骤确认：`▶ 步骤3 定稿开始`

### 合并定稿

解析 02_review.md 中的审查意见——从每个 json 代码块提取 JSON，重点关注：
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

**写入定稿**：使用 Write 工具写入 `{ICODE_OUT_DIR}/03_plan_final.md`。

### 定稿自检

读取刚写入的 `{ICODE_OUT_DIR}/03_plan_final.md`，逐项检查：

- 9 个正文章节完整（概述、功能需求、架构设计、架构决策记录ADR、详细设计、异常处理、实现步骤、校验项、风险评估）
- **预留「实现偏差备忘」空段**（正文9章之外的附加段，不计入9章）：在 `03_plan_final.md` 末尾追加 `## 实现偏差备忘（步骤6 终审回写）` 空段（仅标题 + 占位说明"待步骤6 回写"），供步骤6 终审时回写实质偏差。定稿阶段不填内容
- 校验项 checkbox 格式正确（`- [ ]` 或 `- [x]`）
- 章节编号连续无重复
- 所有 [审查采纳] / [审查否决] / [对抗否决] / [待验证-已证实] / [待验证-证据仍不足] 标记与审查意见/对抗记录一一对应
- 任何缺失、断裂、矛盾处必须修复后再继续
- **无需添加新功能或重构，仅修复格式和结构问题**

### 强制操作

- **更新 `.ico_metadata.json`**：`status = plan_finalized`，`completed_steps` 追加 `"3"`
- 全流程模式：**立即继续执行步骤4**
