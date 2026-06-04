# 步骤 5 — 三阶段递进深度复检

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_reverse.json`（逆推规格，单条 JSON）+ `{ICODE_OUT_DIR}/05_review_rounds.json`（Fixed/Free 轮次 JSONL）
**模型**: 全流程模式 `sonnet`；分步模式使用当前会话模型

## 前置校验

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`。

## 三阶段说明

| 顺序 | 阶段 | 输入 | 目标 |
|------|------|------|------|
| 1 | **Reverse**（逆推） | 只给代码 | 从代码逆推需求规格，主 Agent 对比计划找差异 |
| 2 | **Fixed**（固定维度） | 计划 + 最新代码 | 7 个固定维度，逐项覆盖 |
| 3 | **Free**（自由探索） | 计划 + 最新代码 | 主 Agent 从 15 角度池分配，每轮指定 2 个未用角度 |

**阶段切换规则**：
- Reverse 阶段：单次执行（无轮次循环），clean 后进入 Fixed
- Fixed → Free：Fixed 首次出现全 clean 后自动切换到 Free
- Free → 终止：Free 连续 5 轮 clean 后终止

**并发约束**：本步骤所有子 Agent 串行启动，同一时刻在跑子 Agent ≤1（满足 SKILL 全局"并发≤3"约束）。**阶段数量与子 Agent 数量无关**，三阶段可串行跑任意轮次。

## 关键：代码新鲜度保证

**每轮启动子 Agent 前，主 Agent 必须重新读取所有代码文件**，将最新内容嵌入 prompt。

原因：上一轮发现的问题已被主 Agent 修复，子 Agent 必须看到修复后的最新代码才能避免：
- 重复发现已修问题
- 漏掉修复引入的新问题
- 基于旧代码做错判断

主 Agent 在本步骤的执行流程中（每次启动子 Agent 前）必须用 Read/Glob 重新拉取 `.ico_metadata.json` 中 `code_files` 列表对应的所有文件内容，替换进 prompt。

## 执行流程

### ⚠️ 强制规则：禁止主 Agent 直接复检

每轮复检**必须由子 Agent 独立完成**。主 Agent 只负责确定目录、读取文件、启动子 Agent、执行修复、写入记录。**主 Agent 不得自行评估代码质量。**

**例外**：Reverse 阶段的主 Agent 比对动作（对比逆推规格与原计划）属于机械 diff，不算"评估代码质量"。

### Free 阶段角度管理

主 Agent 维护**已用角度集合**，禁止子 Agent 自行选择角度。角度池（15个）：

| # | 角度 | 说明 |
|---|------|------|
| A1 | 计划实施一致性 | 逐条对照计划功能点/接口/约束 |
| A2 | 逻辑闭环 | 数据流、控制流、调用链完整性 |
| A3 | 异常处理完备性 | 错误码、异常场景、边界条件 |
| A4 | 边界与极端值 | 空值、越界、极值、零值组合 |
| A5 | 代码规范与风格 | 命名、缩进、注释、文件组织 |
| A6 | 安全漏洞 | 缓冲区溢出、注入、权限、输入校验 |
| A7 | 并发与重入安全 | 竞态条件、死锁、信号安全、线程安全 |
| A8 | 资源与内存管理 | 泄漏、双重释放、句柄管理 |
| A9 | 性能热点 | 不必要拷贝、算法复杂度、内联机会 |
| A10 | 可测试性 | 接口可测性、Mock 友好度、覆盖率缺口 |
| A11 | 可维护性 | 函数复杂度、模块内聚、重复代码 |
| A12 | 编译器/构建兼容 | 警告、平台差异、条件编译、头文件依赖 |
| A13 | 跨平台与移植 | 字节序、类型大小、编译器差异 |
| A14 | API/ABI 兼容 | 符号可见性、C++ 兼容、版本契约 |
| A15 | 文档与注释一致性 | 注释是否与实现一致 |

**主 Agent 行为**：
- 固定从 A1 开始顺序分配，每轮分配 2 个**未被本轮之前使用过**的角度
- 将分配的角度名 + 说明填入 Free 阶段 prompt 的 `{本轮指定角度}` 占位符
- 本轮结束后，将分配的角度加入已用集合
- 如果 15 个角度全部用过仍需要继续，重置已用集合但要求"比上一轮更深一层"

### 执行步骤

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `{ICODE_OUT_DIR}/03_plan_final.md` 和 `.ico_metadata.json` 的 `code_files` 列表
   - 若 `.ico_metadata.json.code_compile_failed == true`，输出 `⚠️ 步骤4编译失败，仍继续复检（已知代码可能有问题）` 警告
3. **分步续跑检测**：
   - 若 `.ico_metadata.json.status == "deepcheck_in_progress"`，从 metadata 恢复 `total_rounds` / `clean_rounds` / `phase` 字段
   - 同时读取已存在的 `05_reverse.json`（若存在，跳过 Reverse 阶段）和 `deepcheck_round_*.json` 汇总历史
   - 跳过已完成的轮次和阶段，从断点继续
   - 输出续跑信息：`▶ 步骤5 续跑，phase=XX，从第N轮开始（已完成M轮）`
   - 否则初始化 `clean_rounds = 0`, `total_rounds = 1`, `phase = "reverse"`，并设 `status = deepcheck_in_progress`
4. 按「通用规则」确定当前模型
5. 输出模型确认：`▶ 步骤5 使用模型：{当前模型名称}`
6. **重新读取所有代码文件**（关键！保证新鲜度）
7. **启动子 Agent** 执行复检，根据 `phase` 选择对应 prompt。**串行启动**（同一时刻仅 1 个子 Agent 运行）
8. 解析本轮结果，更新计数器和 phase，重复第6步

**Reverse 阶段 prompt（`phase == "reverse"`）**：

```
当前使用模型：{当前模型名称}。

请基于以下代码，**逆推**它实现的需求规格。**不允许参考任何计划/需求文档**，只从代码本身推断。

代码文件列表及内容：
{逐个列出文件路径和内容}

**逆推要求**：
- 列出所有导出的函数/接口（签名、参数、返回值）
- 列出所有数据结构/类型/枚举
- 描述每个模块/函数的实际行为（含分支、错误处理路径、边界条件）
- 描述跨文件调用关系和数据流
- 若代码注释声称了某行为，需验证实际执行路径是否一致
- 列出**从代码无法确定**的需求（标注 "unclear"）

**严禁"无问题"敷衍**：必须逐函数分析执行路径，不能只看签名。

直接输出 JSON，以 ===JSON START=== 开头、===JSON END=== 结尾：
{
  "round": 1,
  "interfaces": [{"name":"...","signature":"...","behavior":"..."}],
  "data_structures": [...],
  "modules": [{"name":"...","behavior":"...","callers":[], "callees":[]}],
  "constraints_observed": [...],
  "unclear": [...],
  "summary": "总体描述"
}
```

**主 Agent 处理 Reverse 结果**：
- 提取逆推规格
- 读取 `{ICODE_OUT_DIR}/03_plan_final.md`
- 机械 diff 找出两类问题：
  - **欠实现**：计划有，逆推规格没有
  - **偏离/冗余**：逆推规格有，计划没提
- 若有问题：主 Agent 用 Edit 工具修复代码（**禁止删除现有注释**），**将逆推规格写入 `{ICODE_OUT_DIR}/05_reverse.json`**（单条 JSON，非 JSONL），`total_rounds += 1`，`phase` 切换为 `"fixed"`，进入 Fixed 阶段
- 若无问题：同样写入 `05_reverse.json`（即使无问题也保留记录），`total_rounds += 1`，`phase` 切换为 `"fixed"`，直接进入 Fixed 阶段

**Fixed 阶段 prompt（`phase == "fixed"`）**：

```
当前使用模型：{当前模型名称}。

请对以下已实施代码进行全面复检。

定稿计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的全部内容}

已实施代码文件列表及内容（Reverse 修复后的最新版本）：
{读取代码文件列表，逐个列出文件路径和内容}

**必须先建立计划-代码追溯矩阵**（逐条对照计划中的功能点/接口/约束，标记代码中的对应实现位置和完成状态），再逐维度评估。

复检维度（7个，全部覆盖，缺一不可）：
1. **计划实施一致性** — 逐条对照每个功能点/接口/约束，验证代码100%对齐，逐条陈述实现状态
2. 逻辑闭环 — 数据流、控制流完整，跨文件调用链一致
3. 异常处理 — 错误码、异常场景、边界条件
4. 边界场景 — 空值、越界、超时、并发
5. 规范写法 — 项目代码风格
6. 潜在隐患 — 内存泄漏、死锁、资源竞争、安全漏洞
7. **跨文件一致性** — 接口变更全链路同步，上下游数据结构对齐

直接输出 JSON，以 ===JSON START=== 开头、===JSON END=== 结尾：
{"round":{total_rounds},"dimension_results":{"1":"通过/问题","2":"通过/问题","3":"通过/问题","4":"通过/问题","5":"通过/问题","6":"通过/问题","7":"通过/问题"},"has_issues":true/false,"issues":[{"severity":"高/中/低","file":"文件路径","dimension":"1-7","description":"问题描述","suggestion":"修复建议"}],"summary":"总体评估"}
```

**Free 阶段 prompt（`phase == "free"`）**：

```
当前使用模型：{当前模型名称}。

深度复检第 {total_rounds} 轮，必须尽全力找出遗留问题。

前几轮已检查角度：{历史角度列表}

定稿计划：
{读取 {ICODE_OUT_DIR}/03_plan_final.md 的全部内容}

已实施代码文件列表及内容（Fixed 修复后的最新版本）：
{读取代码文件列表，逐个列出文件路径和内容}

**本轮指定检查角度（只要专注这两个即可）**：
{本轮指定角度}

**每个角度的强制检查点（至少 3 个）**：
- 给出具体的代码位置（文件名+行号）作为证据
- 无问题必须给出分析过程，不可空泛说"通过"

**严禁"快速收尾"**：禁止"无新增问题"、"整体通过"等偷懒措辞，每角度必须提供分析过程。

直接输出 JSON，以 ===JSON START=== 开头、===JSON END=== 结尾：
{"round":{total_rounds},"focus_angles":["角度1","角度2"],"has_issues":true/false,"issues":[{"severity":"高/中/低","file":"文件路径","description":"问题描述","suggestion":"修复建议"}],"summary":"总体评估"}
```

### 强制操作（每轮完成后必须执行）

- **提取子 Agent 回复中的 JSON 内容**（从 ===JSON START=== 和 ===JSON END=== 之间提取），使用 Write 工具写入 `{ICODE_OUT_DIR}/deepcheck_round_{total_rounds}.json`，再**追加写入 `{ICODE_OUT_DIR}/05_review_rounds.json`**（JSONL 格式，每行一条，仅追加 Fixed/Free 轮次；Reverse 结果独立写入 `05_reverse.json`）
- **修复代码时禁止删除现有注释**：仅修改问题相关的代码逻辑，保留原有注释内容
- 解析本轮 JSON：
  - `total_rounds += 1`
  - 如果 `has_issues == true`：
    a. 读取 issues 列表，逐个修复代码问题（用 Edit 工具）
    b. **重置 `clean_rounds = 0`**
    c. 回到执行流程第6步**重新读取所有代码文件**（保证最新），重新启动子 Agent：
       - **Reverse 阶段**：phase 切换为 `"fixed"`，进入 Fixed（单次执行不重跑）
       - **Fixed/Free 阶段**：保持当前 phase 不变，Free 阶段需先分配本轮角度
  - 如果 `has_issues == false`：
    a. **`clean_rounds += 1`**
    b. **阶段切换检查**：
       - 如果 `phase == "fixed"` 且本轮为首次全 clean（`clean_rounds == 1`），将 `phase` 切换为 `"free"`，同时从角度池取 A1+下一个未用角度作为首轮 Free 检查角度
       - 如果 `phase == "free"` 且 `clean_rounds >= 5`，**终止复检**，输出完成信息
    c. 如果还未终止，回到执行流程第6步**重新读取所有代码文件**（保证最新），重新启动子 Agent（**Free 阶段需先分配本轮角度**）

**严格执行**：必须连续满 5 轮全程无任何问题、无任何遗漏、无任何隐患，才可正式终止复检流程。

**复检完成后强制操作**：

- **更新 `{ICODE_OUT_DIR}/.ico_metadata.json`**：将 `status` 设为 `deepcheck_done`，`completed_steps` 追加 `"5"`，并写入 `deepcheck_total_rounds`（实际完成轮次数，即 `total_rounds - 1`）、`deepcheck_clean_rounds` 和 `deepcheck_phase` 字段
- 如果是全流程模式：**立即继续执行步骤6**
