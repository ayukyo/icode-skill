# 步骤 5 — 三阶段递进深度复检

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_reverse.json` + `{ICODE_OUT_DIR}/05_review_rounds.json`
**会话**: 主会话

## 前置校验

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`。

## 三阶段说明

| 顺序 | 阶段 | 输入 | 目标 |
|------|------|------|------|
| 1 | **Reverse**（逆推） | 只给代码 | 从代码逆推需求规格，对比计划找差异 |
| 2 | **Fixed**（固定维度） | 计划 + 最新代码 | 7 个固定维度，逐项覆盖 |
| 3 | **Free**（自由探索） | 计划 + 最新代码 | 一次性完整覆盖15个角度 |

**阶段切换规则**：
- Reverse：单次执行，完成后进入 Fixed
- Fixed → Free：Fixed 首次全 clean 后切换
- Free：单次完整执行后终止

## 关键：代码新鲜度

**每轮开始前必须重新读取所有代码文件**。上一轮发现的问题已修复，必须基于最新代码分析。

## Free 阶段角度管理（15 个）

| # | 角度 | # | 角度 |
|---|------|---|------|
| A1 | 计划实施一致性 | A9 | 性能热点 |
| A2 | 逻辑闭环 | A10 | 可测试性 |
| A3 | 异常处理完备性 | A11 | 可维护性 |
| A4 | 边界与极端值 | A12 | 编译器/构建兼容 |
| A5 | 代码规范与风格 | A13 | 跨平台与移植 |
| A6 | 安全漏洞 | A14 | API/ABI 兼容 |
| A7 | 并发与重入安全 | A15 | 注释完备性+一致性+日志覆盖 |
| A8 | 资源与内存管理 | | |

Free 阶段一次性完整覆盖全部 15 个角度。

### 反偷懒机制

**必须先建立计划-代码追溯矩阵**（逐条列出计划功能点/接口/约束，标记代码对应位置和完成状态），再逐维度评估。禁止跳过追溯直接给"全部通过"。

## 执行步骤

1. 检测最新目录，确定 `ICODE_OUT_DIR`
2. 读取 `03_plan_final.md` 和 `.ico_metadata.json`
   - 若 `.ico_metadata.json.code_compile_failed == true`，输出 `⚠️ 步骤4编译失败，仍继续复检` 警告
3. **强制思考前置**（不可跳过，缺证据视为不合规；完整规则见 [references/thinking.md](../references/thinking.md)）：本步骤子项（至少3步）= 梳理代码清单 → 回顾计划要点 → 制定逆推/Fixed/Free 检查策略
4. **分步续跑**：若 `status == "deepcheck_in_progress"`，从 metadata 恢复 `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`，同时读取已存在的 `05_reverse.json`（若存在则跳过 Reverse）和 `deepcheck_round_*.json`
5. 否则初始化 `deepcheck_clean_rounds = 0`, `deepcheck_total_rounds = 1`, `deepcheck_phase = "reverse"`, `status = deepcheck_in_progress`
6. 输出：`▶ 步骤5 复检开始`

### 阶段 1 — Reverse（逆推）

**重新读取所有代码文件**。基于代码**逆推**需求规格——不允许参考计划或需求文档，只从代码推断。

**逆推内容**：
- 列出所有导出函数/接口（签名、参数、返回值）
- 列出所有数据结构/类型/枚举
- 描述每个模块/函数的实际行为（含分支、错误处理、边界）
- 描述跨文件调用关系和数据流
- 验证代码注释与实际执行路径是否一致
- 列出**从代码无法确定**的需求（标注 "unclear"）

> **注释完备性 + 日志覆盖**不在 Reverse 逆推阶段重复检查——留待 Fixed 第5维度与 Free A15 统一查（避免同步骤三处重复）

写入 `{ICODE_OUT_DIR}/05_reverse.json`。

**对比**：读取 `03_plan_final.md`，与逆推规格做机械 diff：
- **欠实现**：计划有，逆推没有
- **偏离/冗余**：逆推有，计划没提

**处理分流**（区分该修的 vs 该留的）：
- **该修的偏离**（代码错误/漏实现/与计划冲突的不合理偏差）：用 Edit 修复代码使其符合计划，**计入 has_issues**（触发修复→重跑循环）
- **合理偏离**（因约束必须不同、或代码比计划更优的实质偏差）：**不修代码**，保留实现，记录到 `05_reverse.json` 留待步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段，**不计入 has_issues**（无需修复，不触发循环）

发现问题则按上述分流处理。更新计数器，写入 `deepcheck_round_1.json` 和追加写入 `05_review_rounds.json`。`deepcheck_phase` 切换为 `"fixed"`。

### 阶段 2 — Fixed（固定维度）

**重新读取所有代码文件**（含 Reverse 修复后的最新版）。

7 维度逐项检查：
1. 计划实施一致性 — 逐条对照每个功能点/接口/约束
2. 逻辑闭环 — 数据流、控制流、跨文件调用链
3. 异常处理 — 错误码、异常场景、边界条件
4. 边界场景 — 空值、越界、超时、并发
5. 规范写法 + 注释完备性 + 日志覆盖 — 项目代码风格；导出函数/接口/关键分支/数据结构注释是否完备（对照步骤4 第6条）；关键路径（错误返回/状态跳转/外部交互/决策分支/降级重试）日志是否覆盖（对照步骤4 第7条）
6. 潜在隐患 — 内存泄漏、死锁、资源竞争、安全漏洞
7. 跨文件一致性 — 接口变更全链路同步

写入 `deepcheck_round_{deepcheck_total_rounds}.json` 和追加写入 `05_review_rounds.json`。

### 阶段 3 — Free（自由探索）

**重新读取所有代码文件**。一次性完整覆盖全部 15 个角度（A1-A15），每个角度须给出 3+ 具体检查点（文件名+行号）。严禁"整体通过"等偷懒措辞。

### 循环控制

- `deepcheck_total_rounds += 1`
- **实时落盘**：`status = deepcheck_in_progress`，写入当前 `deepcheck_total_rounds`/`deepcheck_clean_rounds`/`deepcheck_phase` 到 metadata
- **阶段切换时重置 `deepcheck_clean_rounds = 0`**（每个阶段独立计数）
- Reverse 阶段：单次执行后始终进入 Fixed，不参与循环
- has_issues → 修复 → `deepcheck_clean_rounds = 0`。**阶段分流**：Reverse/Fixed 阶段 → 回到**当前阶段**重新执行（重新读代码）；**Free 阶段 → 修复后直接终止**（Free 一次性完整覆盖，不重跑——重跑会重复 15 角度检查）
- 无 issues → `deepcheck_clean_rounds += 1`
  - Fixed 首次全 clean（`deepcheck_clean_rounds` 达 1）→ 切换 `deepcheck_phase = "free"`，`deepcheck_clean_rounds = 0`
  - Free 完成后 → 终止
- 终止后更新 `.ico_metadata.json`：`status = deepcheck_done`，`completed_steps` 追加 `"5"`
- 全流程模式：**立即继续执行步骤6**
