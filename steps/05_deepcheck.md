# 步骤 5 — 三阶段递进深度复检

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_deepcheck.md`（合并三阶段产物，不再单独存 JSON）
**会话**: 主会话

> **fast 模式降级**（`metadata.mode == "fast"`）：fast 模式下本步骤**只跑 Reverse 阶段**，完成后直接终止，不切换 Fixed / Free。详见 [steps/fast.md](fast.md)。具体行为：
>
> - Reverse 跑完后，`deepcheck_phase` **不切到 `"fixed"`**，状态直接置 `deepcheck_done`
> - 跳过 Fixed 7 维度检查（业务一致性 / 异常处理 / 边界等深度维度）
> - 跳过 Free 15 角度 + A6 独立 3 质疑者 spawn
> - 输出标记：`▶ 步骤5 fast 模式：仅 Reverse 阶段`
> - 依赖 plan + 1 轮 review + Reverse 单阶段 + audit 四道关卡承担检查职责（fast 设计取舍）

## 前置校验

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`。

## 前置：Code Review Fix 复检产物读取（**软依赖，不阻塞**）

**目的**：读取步骤4末尾 1.5 复检产物 `04_code_review_fix.md`（若存在），让 deepcheck 知道哪些 4 维度未通过项需要重点复检。

1. Read `{ICODE_OUT_DIR}/04_code_review_fix.md`（不存在则跳过本段——可能是旧工单无 1.5 子段，或 fast/full 流程跳过了）
2. 读 `.ico_metadata.json.code_review_fix_with_issues` 字段（缺失视为 `false`）
3. **若 `code_review_fix_with_issues=true`**：
   - 入口输出警告 `⚠️ 步骤4 Code Review Fix 复检未通过：{从 04_code_review_fix.md 摘录未通过维度清单}`
   - Reverse/Fixed/Free 三阶段重点关注未通过维度（如 4 维度竞态死锁未通过 → A7 并发与重入安全加重权重）
   - deepcheck 报告中标注"步骤4 Code Review Fix 未通过 → 本步骤重点复核"
4. **若 `code_review_fix_with_issues=false` 或字段缺失**：
   - 正常进入三阶段复检，不做特殊处理
5. **绝不阻断**：本段失败（旧文件/字段缺失）→ 静默跳过，记 `[跳过-Code Review Fix 读取]`，主流程继续

## 前置：schema 迁移（自动/幂等/原子）

> 自动识别 + 自动迁移：用户无需任何操作，进入步骤 5 时若检测到 `.ico_metadata.json.template_version` 缺失或旧于当前版本，**自动**在已存在的 `05_deepcheck.md` 末尾追加 blast-radius 三链基线段、补写 metadata、原子落盘；幂等（已含迁移段则跳过），失败兜底静默（不阻塞主流程）。

**自动执行**（强制，写在「阶段 1 — Reverse」前）：

1. Read `.ico_metadata.json` —— 读 `template_version` 字段（缺失视为 `"v0"`）
2. 与当前 `DEEPCHECK_TEMPLATE_VERSION = "v1.1"` 比对（v1.1 含本步骤 blast-radius 三链自检段 + 代码新鲜度强制 + Free 表格化），若 `template_version` 已经 ≥ `"v1.1"` → 跳过本次迁移
3. 否则执行迁移 **（原子，不破坏既有正文）**：
   a. 解析 `code_files` 列表（缺失或空 → 跳过本步迁移，记 `[迁移跳过-无 code_files]`）
   b. 对每个 code_file 跑三条 grep（caller / import / test，命令见「阶段 1 — Reverse」的 blast-radius 三链自检段）
   c. grep 结果追加到 `05_deepcheck.md` 末尾的 `## blast-radius 三链自检（v1.1 自动迁移）` 段（不存在则新建段；用 **`grep -F 'blast-radius 三链自检（v1.1 自动迁移）'`** 检测是否已含——字面量模式，marker 内 `(`/年份不做正则匹配；走幂等分支）
   d. 原子写：先写 `.tmp` 再 `mv` 覆盖；保留原文件前 N 行不动
   e. 写回 metadata：增字段 `template_version = "v1.1"`、增 `migration_log` 数组追加 `{from, to, at, files=[code_files 全集]}` 条目、`at = date +%Y-%m-%dT%H:%M:%S` 取系统时间（不写死）
   f. 输出 `▶ 自动迁移 → v1.1：补全 blast-radius 三链基线段（迁移到 .ico_metadata.migration_log）`
4. 任一步失败（文件 I/O 错、grep 不可用、code_files 缺失）→ 静默跳过迁移、记 `[迁移跳过-原因]` 到 metadata migration_log，主流程继续（绝不阻塞步骤 5）

**与步骤 4 迁移的关系**：步骤 4 迁移在 `03_plan_final.md` 写基线（三链预扫段），步骤 5 迁移在 `05_deepcheck.md` 写基线（blast-radius 三链自检段）；两者解耦，单独执行、互不依赖；migration_log 数组会同时记录两次迁移。

**向后兼容**：

- 旧工单（缺 `template_version`）一律视为 `v0`，自动升级到 v1.1
- 已迁移的 metadata 再次进入步骤 5 不会重复追加（按 `grep -F 'blast-radius 三链自检（v1.1 自动迁移）'` 字面量去重）
- 用户手改过的 `05_deepcheck.md` 原文**不会被覆盖**——只在末尾追加新段
- metadata 字段新增（`template_version` / `migration_log`）一律非破坏性，旧 metadata 缺这字段视为默认（`v0` / `[]`），写回时整对象保留

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

> **fast 模式特例**（`metadata.mode == "fast"`，最高优先级）：Reverse 完成后**不切换 Fixed，直接终止**。`deepcheck_phase` 保留 `"reverse"`，状态置 `deepcheck_done`，`completed_steps` 追加 `"5"`。即使 Reverse 发现 has_issues 走修复循环，修复完仍只重跑 Reverse（不切 Fixed/Free）——fast 模式的核心检查职责交给 audit。

## 关键：代码新鲜度

**每轮开始前必须重新读取所有代码文件**（基于步骤4记忆写=偷懒=不合规）。上一轮发现的问题已修复，必须基于最新代码分析。**每阶段开始必须输出 Read 确认行**：`📖 已 Read 代码文件（最新版）：<file1>, <file2>, ...`（列出本次实际 Read 的代码文件路径，无确认行=没读=不合规）。

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

**Free 阶段 A6 深检/争议验证——必须独立 spawn 3 质疑者子代理**：若 Free 阶段发现任何深检 issue 或需争议性验证的点，**必须按 [references/adversarial.md](../references/adversarial.md) 模式独立 spawn 3 个质疑者子代理**（证据质疑者/替代解释者/充分性质疑者各一，不得合并 spawn，少任一视为不合规——见反偷懒第14条；**spawn 规格**：`subagent_type: "general-purpose"` + schema 强制结构化，**禁用 Explore** 防只调研不裁决被截断）。产物（`05_deepcheck.md` 的「对抗验证」段）必须记录每个质疑者的 **独立 spawn Agent ID** 作为调用证据。子代理失败时按 adversarial.md「子代理失败处理」重试1次→仍失败诚实降级为 `[未验证-子代理对抗失败]`，**绝不改由主代理自演裁决**。

## 执行步骤

1. 检测最新目录，确定 `ICODE_OUT_DIR`
2. 读取 `03_plan_final.md` 和 `.ico_metadata.json`
   - 若 `.ico_metadata.json.code_compile_failed == true`，输出 `⚠️ 步骤4编译失败，仍继续复检` 警告
3. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking_core.md](../references/thinking_core.md) 完整内容（核心规则每步必读）+ 按需 Read [references/thinking_detail.md](../references/thinking_detail.md) 对应小节（各步骤子项/历史参考）+ [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规））：本步骤子项（至少3步）= 梳理代码清单 → 回顾计划要点 → 制定逆推/Fixed/Free 检查策略
4. **分步续跑**：若 `status == "deepcheck_in_progress"`，从 metadata 恢复 `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`，同时读取已存在的 `05_deepcheck.md`（若含「Reverse 逆推」段则跳过 Reverse）
5. 否则初始化 `deepcheck_clean_rounds = 0`, `deepcheck_total_rounds = 1`, `deepcheck_phase = "reverse"`, `status = deepcheck_in_progress`
6. 输出：`▶ 步骤5 复检开始`

### 阶段 1 — Reverse（逆推）

**重新读取所有代码文件** + 输出 `📖 已 Read` 确认行。基于代码**逆推**需求规格——不允许参考计划或需求文档，只从代码推断。

**逆推内容**：
- 列出所有导出函数/接口（签名、参数、返回值）
- 列出所有数据结构/类型/枚举
- 描述每个模块/函数的实际行为（含分支、错误处理、边界）
- 描述跨文件调用关系和数据流
- 验证代码注释与实际执行路径是否一致
- 列出**从代码无法确定**的需求（标注 "unclear"）

> **注释完备性 + 日志覆盖**不在 Reverse 逆推阶段重复检查——留待 Fixed 第5维度与 Free A15 统一查（避免同步骤三处重复）

写入 `{ICODE_OUT_DIR}/05_deepcheck.md` 的「Reverse 逆推」段（**人类可读摘要，不单独存 JSON**）。

**对比**：读取 `03_plan_final.md`（**注意：不是 `01_plan.md`，是步骤3 定稿产物**），与逆推规格做机械 diff：
- **欠实现**：计划有，逆推没有
- **偏离/冗余**：逆推有，计划没提
- **调用模式与工程不一致**（新增维度，独立于上面两类代码-计划 diff）：对代码中每个"跨模块/跨端点/跨层"调用，grep 同文件/同模块既有同类调用的写法，核对新增调用是否对齐工程主导模式。**这层对比专门抓"计划自己写错调用模式、代码按计划实现了、代码-计划 diff 无偏离但模式本身错了"的情况**——计划-代码 diff 发现不了，必须对照工程既有模式才能发现。若新增调用与工程主导模式不符（如工程统一走路由、同函数既有同类调用走路由，新增却直调）→ 标 issue，**计入 has_issues**（即使代码与计划一致，计划本身可能错）

- **blast-radius 三链自检（新增）**：对 `code_files` 每个文件，**serena 优先**（v2.2 执行步骤内嵌）：若 serena 可用，ToolSearch 取 `mcp__serena__find_referencing_symbols` schema -> 对每个改动符号调用找所有引用点（语义精准 vs grep 文本匹配）；serena 不可用/无 LSP -> 降级下方三条 grep，降级说明只进思考块，不写入产物文件。**未经实际调用 serena 就标降级 = 反偷懒第 21 条违规**。grep 结果作为"修改影响面证据"，与 Reverse 逆推的"跨文件调用关系"段互相印证。任一链 0 命中即不合规（未扫 = 自欺）。
  1. **caller 链**：`grep -rn '<改动的 func/类/全局符号>(' <project>` —— 列出所有 caller（含行号）
  2. **import 链**：`grep -rn '<改动的 header>' <project>` 或等价的 `import/from` 检索 —— 列出所有依赖入口
  3. **test 链**：`grep -rln '<符号\|<路径>' <test 目录>` —— 列出覆盖测试；无测试时显式标 `[无测试覆盖-符号 X]`，**不静默跳过**（让 has_issues 路径可触发）
  > **兼容旧产物**：本自检作用于本轮 05_deepcheck.md 输出；旧工单（已完成 deepcheck_done）不重跑也不强制。如需对旧工单重做 blast-radius，复制 03_plan_final.md + code_files 列表到 `/tmp/manual_blast_radius.md` 用同三条 grep 离线跑一遍即可。

**处理分流**（区分该修的 vs 该留的）：
- **该修的偏离**（代码错误/漏实现/与计划冲突的不合理偏差、**调用模式与工程不一致**）：用 Edit 修复代码使其符合计划/工程模式，**计入 has_issues**（触发修复→重跑循环）
- **合理偏离**（因约束必须不同、或代码比计划更优的实质偏差）：**不修代码**，保留实现，记录到 `05_deepcheck.md` 留待步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段，**不计入 has_issues**（无需修复，不触发循环）

发现问题则按上述分流处理。更新计数器，写入 `05_deepcheck.md` 的「Round {N}」段。`deepcheck_phase` 切换为 `"fixed"`。

### 阶段 2 — Fixed（固定维度）

**重新读取所有代码文件**（含 Reverse 修复后的最新版）+ 输出 `📖 已 Read` 确认行。

7 维度逐项检查（**每维度必须列 file:line 证据 + 评分理由 ≥2 句实质，不得只概括**）：
1. 计划实施一致性 — 逐条对照每个功能点/接口/约束
2. 逻辑闭环 — 数据流、控制流、跨文件调用链
3. 异常处理 — 错误码、异常场景、边界条件
4. 边界场景 — 空值、越界、超时、并发
5. 规范写法 + 注释完备性 + 日志覆盖 + **优雅度** — 项目代码风格；导出函数/接口/关键分支/数据结构注释是否完备（对照步骤4 第6条）；关键路径（错误返回/状态跳转/外部交互/决策分支/降级重试）日志是否覆盖（对照步骤4 第7条）；**优雅度6条**（对照步骤4 第9条）：①复用优先——新增工具函数 grep 工程是否已有等价，有则必须复用 ②风格对齐——命名/错误处理/日志/注释格式与同模块既有代码一致 ③调用链模式一致——组织方式（注册/属性中心/RAII/错误码）与工程既有模式一致 ④最小侵入——git diff 无"顺手重构"式无关改动 ⑤接口克制——public 符号都必要，能 static 就不 public ⑥**调用路径选择（架构一致性）**——新增跨模块/跨端点/跨层调用时，grep 同文件/同模块既有同类调用，若工程有主导模式（如统一走路由/事件分发而非直调），必须沿用，不得以"内部触发更简单"为由另选直调；若路由/接收器已注册，优先走已建链路而非直调绕过；**同函数内既有同类调用是最强信号**，新增调用必须与之风格一致。检查方法：grep 新增调用涉及的目标方法名/路由类型，看既有调用点怎么写的。不达标记 issue

> **ADR 复用项实证验证**（针对计划 ADR 中明确声明"复用 X 函数"的项）：不只验证代码"看起来用了"，必须用 Grep 工具实证确认调用点（如 `grep -n "calc_gcd(" calc.c`）。若声明复用 calc_gcd 但实际重写了欧几里得算法，必须标记 issue（"ADR-2 声称复用 calc_gcd，但代码实际重写 GCD 逻辑，未真正复用"）。常见陷阱：复用声明与代码实现脱节、代码看似调用但参数处理不一致、复用了不同函数名等
6. 潜在隐患 — 内存泄漏、死锁、资源竞争、安全漏洞
7. 跨文件一致性 — 接口变更全链路同步

写入 `05_deepcheck.md` 的「Fixed 7维度」段（**人类可读摘要，不单独存 JSON**）。

### 阶段 3 — Free（自由探索）

**重新读取所有代码文件** + 输出 `📖 已 Read` 确认行。一次性完整覆盖全部 15 个角度（A1-A15），每个角度须给出 ≥3 具体检查点（文件名+行号）。严禁"整体通过"等偷懒措辞。**输出强制表格**（填不满=偷懒一眼可视）：

| 角度 | 检查点1（file:line） | 检查点2（file:line） | 检查点3（file:line） | 结论 |
|------|------|------|------|------|
| A1 计划实施一致性 | <file:line> | <file:line> | <file:line> | pass/issue |
| A2~A15 | ... | ... | ... | ... |

### 循环控制

- `deepcheck_total_rounds += 1`
- **实时落盘**：`status = deepcheck_in_progress`，写入当前 `deepcheck_total_rounds`/`deepcheck_clean_rounds`/`deepcheck_phase` 到 metadata
- **阶段切换时重置 `deepcheck_clean_rounds = 0`**（每个阶段独立计数）
- Reverse 阶段：单次执行后始终进入 Fixed，不参与循环
  > **fast 模式特例**（`metadata.mode == "fast"`，最高优先级，命中即跳过 Fixed/Free）：Reverse 单次执行后**直接终止**——`deepcheck_phase` 保留 `"reverse"`，状态置 `deepcheck_done`，`completed_steps` 追加 `"5"`。即使发现 has_issues 走修复循环，修复完仍只重跑 Reverse（不切 Fixed/Free）。
- has_issues → 修复 → `deepcheck_clean_rounds = 0`。**阶段分流**：Reverse/Fixed 阶段 → 回到**当前阶段**重新执行（重新读代码）；**Free 阶段 → 修复后直接终止**（Free 一次性完整覆盖，不重跑——重跑会重复 15 角度检查）
- 无 issues → `deepcheck_clean_rounds += 1`
  - Fixed 首次全 clean（`deepcheck_clean_rounds` 达 1）→ 切换 `deepcheck_phase = "free"`，`deepcheck_clean_rounds = 0`
  - Free 完成后 → 终止
- 终止后更新 `.ico_metadata.json`：`status = deepcheck_done`，`completed_steps` 追加 `"5"`
- 全流程模式：**立即继续执行步骤6**

## 完成前自检（必须填，未填项标 ❌=不合规）

- □ Reverse/Fixed/Free 三阶段都输出了 `📖 已 Read` 确认行（列出实际 Read 的代码文件）
- □ Free 每个角度 ≥3 检查点（file:line），表格填满
- □ Fixed 每维度有 file:line 证据 + 评分理由 ≥2 句实质
- □ 无"整体通过""无问题"等空泛结论（每条结论有具体证据）
## MCP 推荐（v2.2 强证据二元化）

按 [references/mcp_per_step.md](../references/mcp_per_step.md)「强证据场景判定」，本步骤 MCP：

| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| sequential-thinking | 🟢 | 强制思考 |
| serena | 🟢* | 找所有调用点评估 blast-radius--有可索引源码时（Reverse 阶段内嵌） |
| playwright | 🟢* | 跑 E2E--前端工程时 |
| vision-bridge | 🟢* | UI 截图复检--用户给图时 |
| context7 | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |

**强制约束（v2.2）**：🟢 必须调（满足强证据场景）；🟢* 默认 🟢 但需满足强证据场景才必调（不满足降 ⚪，无需声明）；⚪ 无需评估。serena 由执行步骤内嵌点承载，其余 🟢/🟢* 由 [thinking_core.md](../references/thinking_core.md) MCP gate 承载。详见 [SKILL.md](../SKILL.md)「MCP 调用覆盖强制化」+ [mcp_per_step.md](../mcp_per_step.md)「双保险机制」。
