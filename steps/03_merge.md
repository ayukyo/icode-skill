# 步骤 3 — 吸纳评审意见、合并优化定稿

**命令**: `/icode merge`
**产出**: `{ICODE_OUT_DIR}/03_plan_final.md`
**模型**: 全流程模式 `opus`；分步模式使用当前会话模型

## 前置校验

检查 `{ICODE_OUT_DIR}/01_plan.md` 和 `{ICODE_OUT_DIR}/02_review.md` 是否存在，缺失则报错并提示先执行对应步骤。

## 执行步骤

### ⚠️ 强制规则：禁止主 Agent 直接合并定稿

定稿合并**必须由子 Agent 独立完成**。主 Agent 只负责确定目录、读取文件、启动子 Agent、写文件。**主 Agent 不得自行撰写定稿计划。**

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/01_plan.md`（原始计划）和 `{ICODE_OUT_DIR}/02_review.md`（审查意见）
3. 按「通用规则」确定当前模型
4. 输出模型确认：`▶ 步骤3 使用模型：{当前模型名称}`
5. **启动子 Agent** 执行合并定稿，prompt 为：

```
当前使用模型：{当前模型名称}。

请将以下审查意见合并优化进原始计划。

原始计划：
{读取 {ICODE_OUT_DIR}/01_plan.md 的全部内容}

审查意见：
{读取 {ICODE_OUT_DIR}/02_review.md 的全部内容}

要求：
1. 逐条甄别审查意见，保留正确合理的，剔除无效冗余的
2. 每处修改标注 [审查采纳] 标记
3. 保持整体架构不变
4. 输出前必须自检：章节完整、编号连续、校验项 checkbox 格式正确

**禁止执行任何工具调用**，所有信息已在 prompt 中提供，基于以上内容直接分析输出。

直接输出完整定稿计划，以 ===FINAL PLAN START=== 开头、===FINAL PLAN END=== 结尾。
```

6. **提取定稿内容**（从 ===FINAL PLAN START=== 到 ===FINAL PLAN END===），写入 `{ICODE_OUT_DIR}/03_plan_final.md`
7. **更新 `.ico_metadata.json`**：`status = plan_finalized`，`completed_steps` 追加 `"3"`
8. 全流程模式：**立即继续执行步骤4**
