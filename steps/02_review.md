# 步骤 2 — 多轮专项审查

**命令**: `/icode review`
**产出**: `{ICODE_OUT_DIR}/02_review.md`
**模型**: 全流程模式 `sonnet`；分步模式使用当前会话模型

采用**独立计划对比 + 多轮循环审查**模式：
- **首轮**：子 Agent 先基于原始需求独立编制简要计划，再与步骤1计划逐项对比，最后做6维度审查
- **后续轮次**：补充审查直到连续2轮无新问题，或达到3轮上限

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/.ico_metadata.json` 是否存在，缺失则报错。

## 执行流程

### ⚠️ 强制规则：禁止主 Agent 直接审查

每轮审查**必须由子 Agent 独立完成**。主 Agent 只负责确定目录、读取文件、启动子 Agent、写入记录。**主 Agent 不得自行撰写审查意见。**

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/01_plan.md` 和 `.ico_metadata.json` 获取原始需求
3. 初始化计数器 `clean_rounds = 0`, `total_rounds = 1`
4. 按「通用规则」确定当前模型
5. 输出模型确认：`▶ 步骤2 使用模型：{当前模型名称}`
6. **启动子 Agent** 执行审查，prompt 如下。

**首轮 prompt（`total_rounds == 1`）**：

```
当前使用模型：{当前模型名称}。

========== 独立编制计划 ==========
原始需求：{读取 .ico_metadata.json 中的 requirement 字段}
请先基于原始需求独立编制一份简要项目计划（架构思路、功能模块、核心接口、实现步骤），不要参考下方步骤1计划。

========== 对比分析 ==========
步骤1计划内容：
{读取 {ICODE_OUT_DIR}/01_plan.md 的全部内容}

将你的独立计划与步骤1计划逐项对比：遗漏点、偏差点、多余点，给出裁决。

========== 逐维审查（6个维度，全部覆盖）==========
1. 逻辑合理性 — 流程是否合理、有无矛盾
2. 流程完整性 — 是否有遗漏步骤或环节
3. 场景覆盖度 — 正常/异常/边界场景是否全部覆盖
4. 风险遗漏 — 还有哪些技术/业务风险未提及
5. 落地可行性 — 在当前项目架构下是否可直接执行
6. 现有实现对照 — 计划方案是否与现有代码重复或冲突

========== 输出 ==========
直接输出 JSON，以 ===JSON START=== 开头、===JSON END=== 结尾：
{"round":1,"independent_plan_summary":"概述","comparison_analysis":[{"type":"遗漏/偏差/多余","independent_view":"我的方案","plan_view":"步骤1方案","verdict":"采纳/折中/驳回","reason":"理由"}],"dimension_results":{"1":"通过/问题","2":"通过/问题","3":"通过/问题","4":"通过/问题","5":"通过/问题","6":"通过/问题"},"has_new_issues":true/false,"new_issues":[{"dimension":"1-6","severity":"高/中/低","description":"问题描述","suggestion":"建议"}],"summary":"总体评估"}
```

**后续轮次 prompt（`total_rounds > 1`）**：

```
当前使用模型：{当前模型名称}。

补充审查第 {total_rounds} 轮。步骤1计划内容：
{读取 {ICODE_OUT_DIR}/01_plan.md 的全部内容}

之前轮次已发现问题：
{之前轮次的问题列表}

检查是否还有遗漏问题或更深层次风险。

维度（全部覆盖）：1.逻辑合理性 2.流程完整性 3.场景覆盖度 4.风险遗漏 5.落地可行性 6.现有实现对照

直接输出 JSON，以 ===JSON START=== 开头、===JSON END=== 结尾：
{"round":{total_rounds},"has_new_issues":true/false,"new_issues":[{"dimension":"1-6","severity":"高/中/低","description":"问题描述","suggestion":"建议"}],"summary":"本轮评估"}
```

7. **提取 JSON**（从 ===JSON START=== → ===JSON END===），写入 `{ICODE_OUT_DIR}/review_round_{total_rounds}.json`，追加到 `{ICODE_OUT_DIR}/02_review.md`
8. **解析 JSON 控制循环**：
   - `total_rounds += 1`
   - 有 issues（`has_new_issues == true`）：`clean_rounds = 0`，若 `total_rounds <= 3` 回到第6步重新启动子 Agent，否则终止
   - 无 issues（`has_new_issues == false`）：`clean_rounds += 1`，若 `clean_rounds < 2` 回到第6步继续，否则终止
9. **更新 `.ico_metadata.json`**：`status = review_done`，`completed_steps` 追加 `"2"`，记录轮次
10. 全流程模式：**立即继续执行步骤3**
