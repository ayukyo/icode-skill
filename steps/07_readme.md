# 步骤 7 — 交付报告生成（可选，手动触发）

**命令**: `/icode readme`
**产出**: `{ICODE_OUT_DIR}/{动态文件名}.md`（文件名按需求提炼，非固定名）
**会话**: 主会话
**定位**: **可选后置步骤，不参与步骤1~6流程推进**。步骤6完成后用户手动触发，生成面向人的自包含交付报告。不改 status（仍 `completed`），不改 completed_steps（不追加"7"）。

## 前置校验

检查 `{ICODE_OUT_DIR}/.ico_metadata.json` 的 `status == "completed"`（步骤6已完成）。若未完成则报错提示先执行步骤6。

## 文件名生成

从 metadata 的 `requirement` 提炼关键词，生成 `{工程简名}_{需求关键词}.md`：
- 工程简名：`project_path` 的 basename
- 需求关键词：从 requirement 提取核心动词+对象（如"增加取模和幂运算"→`modulo_power`，"溢出修复"→`overflow_fix`）
- 全小写 + 下划线分隔
- 示例：`calc_modulo_power.md`、`calc_power_overflow_fix.md`、`calc_abs_function.md`

## 智能模板选择

读 metadata 的 `completed_steps`，若含 `"log"` → **查BUG模板**，否则 → **功能开发模板**。

### 功能开发模板（方式A/B/C）

```markdown
# {需求标题} — 交付报告

## 1. 需求概述
{原始需求 + 背景目标。从 00_init.md/命令行参数提取，自包含不引用内部文件}

## 2. 设计方案
{架构设计 + ADR 决策 + 关键接口签名。从 03_plan_final.md 提取要点，自包含}

## 3. 代码变更
{改了哪些文件 + 每个文件改了什么 + 调用链。从 code_files + 03_plan_final.md 提取，自包含}

**调用链流程图**（ASCII，必含）：
```
{入口} → {函数A} → {函数B} → {返回}
```
用 ASCII 箭头画出从入口到返回的完整调用路径，让读者一眼看懂数据流。

## 4. 审查与复检
{审查发现的问题 + 对抗结论 + 复检结果。从 02_review.md + 05_deepcheck.md 提取要点，自包含}

## 5. 偏差与修复
{实现与计划的偏差 + 修复记录。从 03_plan_final.md 偏差段 + 06_audit.md 提取，自包含}

## 6. 使用说明
{如何编译/运行/验证 + 接口调用示例。从 Makefile + 代码实际扫描，自包含}

## 7. 已知限制
{残留风险 + 待验证假设。从 06_audit.md 提取，自包含}
```

### 查BUG模板（方式D，含 log 步骤）

```markdown
# {BUG标题} — 交付报告

## 1. BUG概述
{症状 + 影响 + 日志来源。从 log_analysis.md 提取，自包含}

## 2. 根因分析
{根因假设 + 对抗验证 + 决定性证据。从 log_analysis.md 提取，自包含}

## 3. 修复方案
{改了什么 + 为什么这么改。从 03_plan_final.md + 06_audit.md 提取，自包含}

## 4. 代码变更
{文件:行号 + 调用链。从 code_files + 代码扫描，自包含}

**修复前后调用链对比**（ASCII，必含）：
```
修复前：{入口} → {函数A} → {漏洞点} → {错误返回}
修复后：{入口} → {函数A} → {新增防御} → {正确返回}
```
用 ASCII 箭头画出修复前后调用路径对比，让读者一眼看懂修了哪、怎么修的。

## 5. 审查与复检
{审查发现 + 复检结果。从 02_review.md + 05_deepcheck.md 提取，自包含}

## 6. 验证方法
{如何确认 BUG 已修复。从 main.c 测试 + 运行输出，自包含}

## 7. 已知限制
{残留风险 + 待验证。从 06_audit.md 提取，自包含}
```

## 自包含约束（重要）

**全文不得出现 icode 内部文件名和术语**——所有信息从产物中**提取后用通俗语言重写**。读者不需要知道 icode 是什么，不需要翻其他文件。

**禁止出现的内部术语**（必须替换为通俗表述）：
- ❌ `01_plan.md`/`03_plan_final.md`/`review_round_*.json`/`05_deepcheck.md`/`06_audit.md`/`02_review.md`/`00_init.md`/`log_analysis.md` 等文件名 → ✅ 直接写内容
- ❌ `Agent ID`/`agentId`/`spawn`/`子代理` → ✅ "独立审查者"
- ❌ `质疑者`/`证据质疑者`/`替代解释者`/`充分性质疑者` → ✅ "独立审查者"
- ❌ `confirmed`/`refuted`/`needs_more_evidence` → ✅ "确认/推翻/待验证"
- ❌ `clean`/`clean_rounds`/`收敛` → ✅ "无新问题/审查通过"
- ❌ `has_new_issues`/`pending_verification` → ✅ "发现问题/待验证项"
- ❌ `code_deviations`/`adversarial_verification` → ✅ "实现偏差/对抗验证"
- ❌ `步骤2`/`步骤5`/`步骤6` 等步骤编号 → ✅ "审查阶段/复检阶段/终审阶段"

## 强制思考前置

**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规）。本步骤子项（至少4步）= 通读产物要点 → 识别报告类型(log/功能) → 提炼自包含内容 → 规划章节结构。

## 执行流程

1. 检测最新目录，确定 `ICODE_OUT_DIR`
2. 读取 `.ico_metadata.json`：确认 `status==completed` + 提取 `requirement` + `completed_steps` + `code_files`
3. 读取所有产物（`00_init.md`/`log_analysis.md`/`01_plan.md`/`02_review.md`/`03_plan_final.md`/`05_deepcheck.md`/`06_audit.md`）+ 代码文件 + Makefile
4. 强制思考前置（见上方）
5. **生成文件名**：从 `requirement` 提炼关键词 + 工程简名
6. **选择模板**：`completed_steps` 含 `"log"` → 查BUG模板，否则 → 功能开发模板
7. **逐章提取要点**（自包含，不引用内部文件名）
8. 使用 Write 工具写入 `{ICODE_OUT_DIR}/{动态文件名}.md`
9. 输出：`▶ 步骤7 交付报告生成完成 — {ICODE_OUT_DIR}/{文件名}`

## 反偷懒机制

- **禁止泛泛而谈**：每条说明必须基于具体代码位置（文件:行号）或具体产物内容
- **禁止引用内部文件**：全文不得出现 icode 内部文件名
- **禁止省略章节**：7 个章节必须全部有实质内容，不足处写"无"而非省略
- **禁止复制产物全文**：提取要点而非复制全文（如 ADR 只写决策+理由，不写上下文/选项）

## 完成标志

- 文件已写入 `{ICODE_OUT_DIR}/{动态文件名}.md`
- 7 章节全有实质内容
- 全文无 icode 内部文件名引用
- 至少引用 1 个具体代码位置（文件:行号）
- 输出确认行

## 可重复执行

用户可多次 `/icode readme` 覆盖更新（每次重新提取最新产物内容）。
