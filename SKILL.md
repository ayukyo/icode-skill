---
name: icode
description: 六步全流程编码工作流，支持分步手动调用：/icode help (帮助), /icode new <需求> (新建+计划), /icode review (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审)
---

**版本**: v1.0.0

# ICode 六步全流程编码工作流

## 概述

端到端编码工作流，将需求到交付拆解为 6 个严格步骤，每步可单独调用，方便你自行切换模型。

## 调用命令

所有输出保存在 `.icode_output_N/`（N 自动递增）目录下：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode new <需求>` | **全流程**：创建新目录 → 步骤1（拟定计划） | ✅ 创建新目录 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划 | ✅ 创建新目录 |
| `/icode review` | **仅步骤2**：专项审查计划 | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：无限轮循环复检 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复 | 用最新目录 |

### 帮助说明

**命令**: `/icode help`
**产出**: 在对话中输出帮助信息（不创建目录和文件）

输出内容即为以下「使用流程示例」和「命令一览」，帮助快速了解工作流调用方式。

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤）
/icode new 实现MCU雨量传感器I2C驱动

# 方式B：分步执行，每步可切换模型
# 步骤1：仅拟定计划，创建新目录
/icode plan 实现MCU雨量传感器I2C驱动
# 输出保存到 .icode_output_1/

# 看完计划后，切换模型，执行审查
/icode review

# 切换模型，合并定稿
/icode merge

# 切换模型，编码实施
/icode code

# 切换模型，循环复检
/icode deepcheck

# 切换模型，终审修复
/icode audit
```

## 工作流执行规则

### 目录管理

**创建新目录（用于 new / plan）**：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  NEXT=1
else
  NEXT=$((LAST + 1))
fi
ICODE_OUT_DIR=".icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

**检测最新目录（用于 review/merge/code/deepcheck/audit）**：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  echo "错误：没有找到已存在的 .icode_output_N 目录，请先运行 /icode new <需求>"
  exit 1
fi
ICODE_OUT_DIR=".icode_output_${LAST}"
```

**前置文件校验（每步执行前必须检查）**：
| 步骤 | 必须存在的文件 |
|------|---------------|
| review | `{ICODE_OUT_DIR}/01_plan.md` |
| merge | `{ICODE_OUT_DIR}/01_plan.md` + `{ICODE_OUT_DIR}/02_review.md` |
| code | `{ICODE_OUT_DIR}/03_plan_final.md` |
| deepcheck | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4创建的代码文件 |
| audit | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4创建的代码文件 |

校验逻辑：执行步骤前先检查前置文件是否存在，同时读取 `{ICODE_OUT_DIR}/.ico_metadata.json` 查看已完成步骤和代码文件列表，缺失则报错并提示需要先执行哪一步。

**元信息文件**（`.ico_metadata.json`）：
每个 `.icode_output_N/` 目录下维护一个元信息文件，记录执行状态和代码文件列表，用于跨会话恢复和步骤间数据传递。

```json
{
  "requirement": "需求描述",
  "created_at": "创建时间",
  "status": "当前步骤状态(plan_done/review_done/plan_finalized/code_done/review_complete/completed)",
  "completed_steps": ["1", "2", "3"],
  "code_files": ["common/include/utils/math_utils.hpp", "common/test/test_math_utils.cpp"]
}
```

步骤执行后必须更新此文件的 `status` 和 `completed_steps` 字段。步骤4编码完毕后必须将创建/修改的文件路径记录到 `code_files` 字段。

### 步骤 1 — 拟定正式项目计划

**命令**: `/icode plan <需求>` 或 `/icode new <需求>`
**产出**: `{ICODE_OUT_DIR}/01_plan.md`

1. 执行目录管理中的「创建新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 启动 Agent：

```
你是一位资深架构师。请根据以下需求编写一份结构完整、流程清晰、边界齐全、可直接落地执行的正式项目实施计划。

**硬性要求：必须包含以下全部 8 个章节，缺一不可，缺少任意章节将导致计划不通过。**

需求描述：
{用户输入的原始需求}

必须包含的章节（逐一输出，不得跳过）：
1. **项目概述** — 目标、范围、约束条件
2. **功能需求** — 所有功能点列表，含输入/输出/边界
3. **架构设计** — 模块划分、数据流、接口定义
4. **模块详细设计** — 每个模块的职责、关键函数、数据结构
5. **异常处理** — 错误码、异常场景、降级策略
6. **实现步骤** — 分阶段实施顺序、依赖关系
7. **校验项** — 可复核检查点列表（用于后续步骤核对）
8. **风险评估** — 技术风险、依赖风险、缓解措施

格式要求：
- 使用 Markdown 格式
- 条理分明，每章有编号
- 校验项以 [ ] checkbox 形式列出
```

3. 将返回内容写入 `{ICODE_OUT_DIR}/01_plan.md`
4. 创建元信息文件 `{ICODE_OUT_DIR}/.ico_metadata.json`，内容：
```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "plan_done",
  "completed_steps": ["1"],
  "code_files": []
}
```
5. 如果是 `/icode new`（全流程模式），接着依次执行步骤2→3→4→5→6 串联完成全流程

---

### 步骤 2 — 专项审查 + 输出文档

**命令**: `/icode review`
**产出**: `{ICODE_OUT_DIR}/02_review.md`

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. **前置文件校验**：检查 `{ICODE_OUT_DIR}/01_plan.md` 是否存在，不存在则报错并提示先执行 `/icode plan`
3. 读取 `{ICODE_OUT_DIR}/01_plan.md` 的内容
4. 启动 Agent：

```
请对以下项目计划做多维度全面审查。

**硬性要求：必须逐项审查全部 5 个维度，每个维度必须给出明确结论，不得跳过或遗漏。禁止跳过分析直接生成报告——必须先逐维度分析问题，再汇总输出审查结论。**

计划文档内容：
{读取 {ICODE_OUT_DIR}/01_plan.md 的内容}

审查维度（必须全部覆盖，缺一不可）：
1. **逻辑合理性** — 流程是否合理、有无矛盾
2. **流程完整性** — 是否有遗漏步骤或环节
3. **场景覆盖度** — 正常/异常/边界场景是否全部覆盖
4. **风险遗漏** — 还有哪些技术/业务风险未提及
5. **落地可行性** — 在当前项目架构下是否可直接执行

执行流程（必须遵循）：
1. 先逐维度阅读计划内容，识别问题
2. 对每个维度记录具体问题点（有则指出，无则明确写"无问题"）
3. 最后汇总所有问题，给出总体结论

输出格式：
- 每个维度独立一节，标题需包含维度编号
- 每个问题标注：严重程度(高/中/低)、问题描述、改进建议
- 末尾给出总体结论（通过/有条件通过/不通过）
```

5. 将审查结果写入 `{ICODE_OUT_DIR}/02_review.md`
6. 更新 `{ICODE_OUT_DIR}/.ico_metadata.json` 的 `status` 为 `review_done`，`completed_steps` 追加 `"2"`

---

### 步骤 3 — 吸纳评审意见、合并优化定稿

**命令**: `/icode merge`
**产出**: `{ICODE_OUT_DIR}/03_plan_final.md`

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. **前置文件校验**：检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/02_review.md` 是否存在，缺失则报错并提示先执行对应步骤
3. 读取 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/02_review.md`
4. 启动 Agent：

```
请将审查意见合并优化进原计划。

原始计划：
{读取 {ICODE_OUT_DIR}/01_plan.md 的内容}

审查意见：
{读取 {ICODE_OUT_DIR}/02_review.md 的内容}

要求：
1. 逐条甄别审查意见合理性
2. 保留采纳所有正确、贴合业务、补齐短板的合理内容
3. 剔除无效、冗余、不贴合实际的建议
4. 完整合并优化进原有计划
5. 保持整体架构不变，只完善补齐内容
6. 在每处修改处标注 [审查采纳] 标记
7. **最终自检**：Agent 输出定稿计划后，**必须**从头到尾通读全文做完整性检查：
   - 章节结构是否完整、编号是否连续
   - 校验项是否全部以 [ ] checkbox 格式列出
   - 计划描述是否准确无歧义
   - 所有 [审查采纳] 标记位置是否合理
   发现问题立即修正，修正后重新输出完整定稿版

输出完整最终定稿版计划。
```

5. 将定稿计划写入 `{ICODE_OUT_DIR}/03_plan_final.md`
6. 更新 `{ICODE_OUT_DIR}/.ico_metadata.json` 的 `status` 为 `plan_finalized`，`completed_steps` 追加 `"3"`

---

### 步骤 4 — 严格落地实施编码

**命令**: `/icode code`
**产出**: 代码文件

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. **前置文件校验**：检查 `{ICODE_OUT_DIR}/03_plan_final.md` 是否存在，不存在则报错并提示先执行 `/icode merge`
3. 读取 `{ICODE_OUT_DIR}/03_plan_final.md` 的内容
4. 启动 Agent：

```
请完全遵照以下最终定稿计划执行全程落地开发。

最终计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

硬性要求：
1. 严格对齐计划中的流程、功能、规范、边界要求
2. 不私自删减逻辑、不简化步骤
3. 不新增计划外额外功能
4. 按标准完成全套代码实施
5. 遵循项目现有代码风格和架构模式
6. 完整的异常处理和边界检查
7. 不破坏现有功能

请开始编码实施。
```

5. Agent 自动创建/修改代码文件。执行完毕后：
   - 记录所有新增/修改的文件路径列表并保存到 `{ICODE_OUT_DIR}/.ico_metadata.json` 的 `code_files` 字段
   - 更新 `status` 为 `code_done`，`completed_steps` 追加 `"4"`

---

### 步骤 5 — 无限轮循环深度复检（双阶段递进）

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_review_rounds.json`

这是最关键的质量关卡。采用**双阶段递进**模式：先固定维度检查，通过后切自由探索，防止 AI 偷懒复读。

#### 双阶段说明

| 阶段 | 条件 | 检查方式 |
|------|------|----------|
| **Fixed**（固定维度） | `phase == "fixed"` | 6 个固定维度，逐项覆盖 |
| **Free**（自由探索） | `phase == "free"` | AI 自主选择检查角度，轮次间不重复 |

**阶段切换规则**：Fixed 阶段首次出现全 clean 后，自动切换到 Free 阶段。随后在 Free 阶段继续复检，直到连续 5 轮 clean 终止。

#### 执行流程

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. **前置文件校验**：检查 `{ICODE_OUT_DIR}/03_plan_final.md` 是否存在，检查步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`
3. 初始化计数器 `clean_rounds = 0`, `total_rounds = 1`, `phase = "fixed"`
4. 读取 `{ICODE_OUT_DIR}/03_plan_final.md` 和 `{ICODE_OUT_DIR}/.ico_metadata.json` 中的 `code_files` 字段获取代码文件列表
5. 根据当前 `phase` 选择对应 prompt 并启动一轮复检 Agent：

**Fixed 阶段 Prompt**（`phase == "fixed"`）：
```
请对以下已实施代码进行全面复检。

**硬性要求：必须逐项检查全部 6 个复检维度，每个维度都必须给出明确结论（通过/发现问题），不得跳过或遗漏任何维度。**

最终计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

已实施代码：
{读取 {ICODE_OUT_DIR}/.ico_metadata.json 中的 code_files 字段，列出所有代码文件路径和内容}

复检维度（必须全部覆盖，缺一不可）：
1. 功能落地 — 计划中的每个功能点是否都已实现
2. 逻辑闭环 — 数据流、控制流是否完整无断裂
3. 异常处理 — 错误码、异常场景、边界条件是否处理
4. 边界场景 — 空值、越界、超时、并发等
5. 规范写法 — 是否遵循项目代码风格
6. 潜在隐患 — 内存泄漏、死锁、资源竞争、安全漏洞

输出要求：
- 必须先逐维度说明检查结果（通过/发现问题），再汇总 issues
- 只输出 JSON 格式结果（不要输出其他内容）：
{
  "round": {total_rounds},
  "dimension_results": {
    "1": "通过/问题",
    "2": "通过/问题",
    "3": "通过/问题",
    "4": "通过/问题",
    "5": "通过/问题",
    "6": "通过/问题"
  },
  "has_issues": false,
  "issues": [
    {
      "severity": "高/中/低",
      "file": "文件路径",
      "dimension": "所属维度编号",
      "description": "问题描述",
      "suggestion": "修复建议"
    }
  ],
  "summary": "总体评估（需明确提及覆盖了哪些维度、每个维度的结论）"
}
```

**Free 阶段 Prompt**（`phase == "free"`）：
```
请对以下已实施代码进行自由深度复检。

**本轮模式：自由探索 —— 请自行选择 2-3 个不同角度切入，不能与前几轮完全相同，禁止偷懒重复相同维度。**

最终计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

已实施代码：
{读取 {ICODE_OUT_DIR}/.ico_metadata.json 中的 code_files 字段，列出所有代码文件路径和内容}

可参考的检查方向（不限于此，欢迎创新）：
- 性能瓶颈：不必要的拷贝、循环效率、算法复杂度
- 资源管理：内存泄漏、文件句柄、锁的正确性
- 并发安全：竞态条件、死锁、原子操作
- 可测试性：依赖注入、Mock 友好度、测试覆盖率缺口
- 可维护性：命名清晰度、函数复杂度、模块内聚性
- 安全漏洞：输入校验、缓冲区溢出、权限检查
- 编译/构建：头文件依赖、链接问题、条件编译
- 跨平台：字节序、类型大小、编译器差异
- 协议兼容：字段对齐、版本兼容、序列化一致性

请先声明你将检查哪些角度，然后逐角度分析，给出具体问题或明确说"无问题"。

只输出 JSON 格式结果（不要输出其他内容）：
{
  "round": {total_rounds},
  "focus_angles": ["角度1", "角度2", "角度3"],
  "has_issues": false,
  "issues": [
    {
      "severity": "高/中/低",
      "file": "文件路径",
      "description": "问题描述",
      "suggestion": "修复建议"
    }
  ],
  "summary": "总体评估"
}
```

6. 解析 Agent 返回的 JSON：
   - `total_rounds += 1`
   - 如果 `has_issues == true`：
     a. 读取 issues 列表，逐个修复代码问题（用 Edit 工具）
     b. 将本轮记录追加到 `{ICODE_OUT_DIR}/05_review_rounds.json`（JSONL 格式，每行一条）
     c. **重置 `clean_rounds = 0`**
     d. 回到步骤 5.4 重新读取文件并开始下一轮复检（**保持当前 phase 不变**）
   - 如果 `has_issues == false`：
     a. 将本轮记录追加到 `{ICODE_OUT_DIR}/05_review_rounds.json`
     b. **`clean_rounds += 1`**
     c. **阶段切换检查**：如果 `phase == "fixed"` 且本轮为首次全 clean（`clean_rounds == 1`），将 `phase` 切换为 `"free"`
     d. 如果 `clean_rounds < 5`，回到步骤 5.4 重新读取文件并开始下一轮复检
     e. 如果 `clean_rounds >= 5`，**终止复检**，输出完成信息

7. **严格执行**：必须连续满 5 轮全程无任何问题、无任何遗漏、无任何隐患，才可正式终止复检流程。
8. 复检完成后，更新 `{ICODE_OUT_DIR}/.ico_metadata.json`：`status` 设为 `review_complete`，`completed_steps` 追加 `"5"`，并记录 `deepcheck_total_rounds`、`deepcheck_clean_rounds` 和 `deepcheck_phase` 字段

---

### 步骤 6 — 终极终审 + 出具报告 + 统一修复

**命令**: `/icode audit`
**产出**: `{ICODE_OUT_DIR}/06_audit.md` + `{ICODE_OUT_DIR}/06_fixes.log`

#### 6.1 出具终审报告

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. **前置文件校验**：检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`
3. 读取 `{ICODE_OUT_DIR}/03_plan_final.md`、`.ico_metadata.json` 获取代码文件列表和复检轮次数据（`deepcheck_total_rounds`、`deepcheck_clean_rounds`）
4. 启动 Agent：

```
请对照原始定稿计划，对当前已实施完成的代码做最终权威终审核验。

原始定稿计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

当前代码：
{读取 {ICODE_OUT_DIR}/.ico_metadata.json 中的 code_files 字段，列出所有代码文件路径和内容}

**硬性要求：必须逐项审核全部 5 个维度，每个维度必须独立评分和评语，不得跳过或遗漏。禁止跳过分析直接输出报告——必须先逐维度审核代码，再汇总输出终审文档。**

审核维度（必须全部覆盖，缺一不可）：
1. **实施完整度** — 计划中所有功能点是否100%落地
2. **执行精准度** — 实现是否与计划描述一致
3. **方案偏离度** — 是否偏离原定方案，偏离是否合理
4. **代码质量** — 可读性、性能、安全性
5. **残留风险** — 还有哪些已知或潜在问题

执行流程（必须遵循）：
1. 先逐维度阅读代码，对照计划进行分析
2. 对每个维度独立评分并撰写评语
3. 汇总所有问题点清单
4. 给出最终结论

输出完整正式终审审查文档。

文档格式要求：
- 总体评分（百分制）
- 每个维度独立评分和评语
- 所有问题点汇总清单（含文件位置、严重程度）
- 最终结论（通过/有条件通过/不通过）
```

5. 将终审报告写入 `{ICODE_OUT_DIR}/06_audit.md`

#### 6.2 统一修复

1. 读取 `{ICODE_OUT_DIR}/06_audit.md` 中的问题清单
2. 按严重程度排序（高 → 中 → 低）逐个修复：
   - 每个问题用 Edit 工具修复
   - 修复后记录到 `{ICODE_OUT_DIR}/06_fixes.log`
3. 全部修复完毕后，做一次全局语法检查（如 `g++` 编译或语法检查）
4. 确认所有问题已关闭
5. 将 `{ICODE_OUT_DIR}/.ico_metadata.json` 的 `status` 更新为 `completed`

#### 6.3 最终交付

输出交付总结：

```
=== ICode 工作流完成 ===
[✓] 步骤1: 计划拟定 — 完成
[✓] 步骤2: 计划审查 — 完成
[✓] 步骤3: 计划定稿 — 完成
[✓] 步骤4: 编码实施 — 完成
[✓] 步骤5: 循环复检 — 读取 .ico_metadata.json 中 deepcheck_total_rounds/deepcheck_clean_rounds
[✓] 步骤6: 终极终审 — 评分{分数}, {结论}
产出目录: {ICODE_OUT_DIR}/
```

## 注意事项

1. **分步切换模型**：每个步骤独立调用，你可以在步骤间切换模型后再执行下一步
2. **前置文件校验**：每步执行前必须先检查前置文件是否存在，缺失则报错终止
3. **跨会话恢复**：关闭会话后重新打开，只需在相同工作目录运行 `ls -d .icode_output_*` 确认目录，然后直接调用对应步骤命令即可
4. **目录自动递增**：每次 `/icode new` 或 `/icode plan` 创建新目录，旧任务不受影响
5. **中断恢复**：产物保存在目录中，重新执行某步骤可覆盖该步骤的输出