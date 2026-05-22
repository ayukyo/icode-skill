# 步骤 5 — 无限轮循环深度复检（双阶段递进）

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_review_rounds.json`
**模型**: 全流程模式 `sonnet`；分步模式使用当前会话模型

## 前置校验

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`。

## 双阶段说明

| 阶段 | 条件 | 检查方式 |
|------|------|----------|
| **Fixed**（固定维度） | `phase == "fixed"` | 7 个固定维度，逐项覆盖 |
| **Free**（自由探索） | `phase == "free"` | AI 自主选择检查角度，轮次间不重复 |

**阶段切换规则**：Fixed 阶段首次出现全 clean 后，自动切换到 Free 阶段。随后在 Free 阶段继续复检，直到连续 5 轮 clean 终止。

## 执行流程

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 初始化计数器 `clean_rounds = 0`, `total_rounds = 1`, `phase = "fixed"`
3. 读取 `{ICODE_OUT_DIR}/03_plan_final.md` 和 `.ico_metadata.json` 中的 `code_files` 字段获取代码文件列表
4. 按「通用规则」确定当前模型
5. 按「通用规则」启动子 Agent，根据当前 `phase` 选择对应 prompt 模板：
   - 如果 `phase == "free"`：读取 `{ICODE_OUT_DIR}/05_review_rounds.json`
     - 每条记录中，优先取 `focus_angles` 字段（Free 记录）；若无则取 `dimension_results` 的 key 列表（Fixed 记录），转为角度描述
     - 汇总所有历史角度为"前几轮已检查角度列表"填入 Free prompt

### Fixed 阶段 Prompt（`phase == "fixed"`）

```
当前使用模型：{当前模型名称}。
**请先进行深度思考（对照计划逐模块检查→逐维度评估→汇总问题），再开始执行。**

请对以下已实施代码进行全面复检。

**硬性要求：必须逐项检查全部 7 个复检维度，每个维度都必须给出明确结论（通过/发现问题），不得跳过或遗漏任何维度。**

最终计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

已实施代码：
{读取 {ICODE_OUT_DIR}/.ico_metadata.json 中的 code_files 字段，列出所有代码文件路径和内容}

复检维度（必须全部覆盖，缺一不可）：
1. 功能落地 — 计划中的每个功能点是否都已实现
2. 逻辑闭环 — 数据流、控制流是否完整无断裂，**跨文件调用链是否一致**（函数签名/参数/返回值是否匹配、头文件声明与实现是否对齐）
3. 异常处理 — 错误码、异常场景、边界条件是否处理
4. 边界场景 — 空值、越界、超时、并发等
5. 规范写法 — 是否遵循项目代码风格
6. 潜在隐患 — 内存泄漏、死锁、资源竞争、安全漏洞
7. **跨文件一致性** — 接口变更是否全链路同步、上下游数据结构是否对齐、修改的文件是否遗漏了关联文件的同步修改

输出要求：
- 必须先逐维度说明检查结果（通过/发现问题），再汇总 issues
- 只输出 JSON 格式结果（不要输出其他内容）
- 注意：`has_issues` 为 boolean 类型，true=有问题需修复，false=全部通过
- 示例如下：
{
  "round": {total_rounds},
  "dimension_results": {
    "1": "通过",
    "2": "通过",
    "3": "问题",
    "4": "通过",
    "5": "通过",
    "6": "通过",
    "7": "通过"
  },
  "has_issues": true,
  "issues": [
    {
      "severity": "中",
      "file": "文件路径",
      "dimension": "3",
      "description": "问题描述",
      "suggestion": "修复建议"
    }
  ],
  "summary": "总体评估（需明确提及覆盖了哪些维度、每个维度的结论）"
}
```

### Free 阶段 Prompt（`phase == "free"`）

**核心规则**：对计划涉及的代码实施效果开启**无限轮次多维度复检**。每一轮发现问题并修正后**重置轮次计数**，直到**连续 5 轮无问题才停止**。不可偷懒、不可提前终止。

```
当前使用模型：{当前模型名称}。
**请先进行深度思考（阅读代码→选择检查角度→逐角度分析→汇总问题），再开始执行。**

请对以下已实施代码进行自由深度复检。

**本轮模式：自由探索 —— 请自行选择 2-3 个与本轮之前不同的新角度切入。**

**硬性规则**：
- 无限轮次多维度复检，发现问题修正后重置计数
- 连续 5 轮无问题才可停止
- 不可偷懒、不可跳过、不可提前终止

最终计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的内容}

已实施代码：
{读取 {ICODE_OUT_DIR}/.ico_metadata.json 中的 code_files 字段，列出所有代码文件路径和内容}

之前轮次已检查过的角度（本轮请避开这些，选新角度）：
{前几轮已检查角度列表，没有则填"无"}

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

输出要求：
- 先声明本轮检查角度（文本形式），再输出 JSON 结果
- JSON 格式如下：
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

### 强制操作（每轮完成后必须执行）

- **必须将本轮 Agent 返回的 JSON 记录追加写入 `{ICODE_OUT_DIR}/05_review_rounds.json`**（JSONL 格式，每行一条）
- 解析本轮 JSON：
  - `total_rounds += 1`
  - 如果 `has_issues == true`：
    a. 读取 issues 列表，逐个修复代码问题（用 Edit 工具）
    b. **必须使用 Write 工具将本轮 JSON 追加到 `{ICODE_OUT_DIR}/05_review_rounds.json`**
    c. **重置 `clean_rounds = 0`**
    d. 回到步骤 5.3 重新读取文件并开始下一轮复检（**保持当前 phase 不变**）
  - 如果 `has_issues == false`：
    a. **必须使用 Write 工具将本轮 JSON 追加到 `{ICODE_OUT_DIR}/05_review_rounds.json`**
    b. **`clean_rounds += 1`**
    c. **阶段切换检查**：如果 `phase == "fixed"` 且本轮为首次全 clean（`clean_rounds == 1`），将 `phase` 切换为 `"free"`
    d. 如果 `clean_rounds < 5`，回到步骤 5.3 重新读取文件并开始下一轮复检
    e. 如果 `clean_rounds >= 5`，**终止复检**，输出完成信息

**严格执行**：必须连续满 5 轮全程无任何问题、无任何遗漏、无任何隐患，才可正式终止复检流程。

**复检完成后强制操作**：

- **更新 `{ICODE_OUT_DIR}/.ico_metadata.json`**：将 `status` 设为 `review_complete`，`completed_steps` 追加 `"5"`，并写入 `deepcheck_total_rounds`、`deepcheck_clean_rounds` 和 `deepcheck_phase` 字段
- 如果是全流程模式：**立即继续执行步骤6**
