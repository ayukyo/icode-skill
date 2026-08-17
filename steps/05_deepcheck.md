# 步骤 5 — 三阶段递进深度复检

**命令**: `/icode deepcheck`
**产出**: `{ICODE_OUT_DIR}/05_deepcheck.md`（合并三阶段产物，不再单独存 JSON）
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 前置产物缺失（`03_plan_final.md` 或步骤 4 代码文件不存在） | 报错退出，提示先跑 `/icode merge` 或 `/icode code` |
| **L1·致命** | 当前工单是 debug 工单（`metadata.debug == true`） | 报错退出，提示：`/icode deepcheck` 不接受 debug 工单（debug 工单不入索引、不参与主流程，纯作为正常工单的对照；详情见 [references/debug_mode.md](../references/debug_mode.md)） |
| **L1·致命** | 统一拓扑门禁 verdict=blocked（双活动实现根 / 子仓逃逸 / 未完成迁移 / cwd 不符） | 报错退出，输出冲突路径与各自 dirty/commit 情况，提示先 `/icode worktree --update` 或人工裁决（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |

**L3·重要**（矩阵段定义）：Reverse/Fixed/Free 任一阶段发现 issue → 进修复循环（最多 2 轮，clean 后退出）；阶段间切换不阻断。

> **fast 模式降级**（`metadata.mode == "fast"`）：fast 模式下本步骤**只跑 Reverse 阶段**，完成后直接终止，不切换 Fixed / Free。详见 [steps/fast.md](fast.md)。具体行为：
>
> - Reverse 跑完后，`deepcheck_phase` **不切到 `"fixed"`**，状态直接置 `deepcheck_done`
> - 跳过 Fixed 7 维度检查（业务一致性 / 异常处理 / 边界等深度维度）
> - 跳过 Free 15 角度 + A6 独立 3 质疑者 spawn
> - 输出标记：`▶ 步骤5 fast 模式：仅 Reverse 阶段`
> - 依赖 plan + 1 轮 review + Reverse 单阶段 + audit 四道关卡承担检查职责（fast 设计取舍）

## 前置校验

> **读决策锚点**（启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取上游关键决策摘要（requirement_digest/key_decisions/design_4dims/deviations/open_risks）作本步骤上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错并提示先执行 `/icode code`。

## 前置：统一拓扑门禁（共享检查器）

> 进入复检前**必须**调用统一拓扑检查器（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)）。**来源一致性**：本步骤所有验证（Reverse 逆推 / Fixed 维度 / Free 角度 / 编译 / 测试）的代码 Read 路径、构建目录、二进制证据**必须来自活动 checkout**——若构建命令在 `superseded` 或旧 checkout 执行，即使测试通过，**不能作为当前活动实现的通过证据**（来源约束见 §3.8 第⑨步）。发现验证记录引用 superseded checkout → 按 blocked 处理，要求迁移或重新验证。

## 前置：patch 配合

> 工单可能已走过 `/icode patch` 追加修改（`{ICODE_OUT_DIR}/08_patch.md` 存在且有 Patch 段，或 `metadata.patch_count > 0`）。本步骤启动时 **Read `08_patch.md`**（不存在则跳过本段，走原流程），按以下规则配合：

1. **Reverse 对比基准扩展**：Reverse 逆推后与计划对比时，计划侧输入 = `03_plan_final.md` + `08_patch.md` 全部 Patch 段（补丁的增量计划/实施是已落地的设计依据）——**patch 已记录的修改视为"已计划"**，不标"偏离/冗余"；代码中**未在** `08_patch.md` 记录的修改仍按偏离处理。**续跑场景**：`deepcheck_in_progress` 中断态期间存在补丁修改时，续跑**不跳过 Reverse**（有补丁必须重跑逆推覆盖更新，见「执行步骤」第 4 步）
2. **追溯矩阵扩展**：Fixed/Free 阶段的计划-代码追溯矩阵 = `03_plan_final.md` 功能点 + `08_patch.md` Patch 功能点（补丁功能点标注"补丁"来源）
3. **patch 修改照常全维度检查**：patch 引入的代码照常进 Reverse 逆推（行为/边界/错误处理）与 Fixed/Free 全维度（含计划实施一致性、优雅度 6 条、竞态/边界）——补丁记录"已计划"只豁免"计划外修改"误报，**不豁免质量检查**（patch 引入的新问题照常标 issue、进修复循环）
4. **blast-radius 三链自检范围扩展**：`code_files` + `08_patch.md` 最新 Patch N 段涉及的符号一并纳入三链扫描

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

**Free 阶段 A6 深检/争议验证——必须独立 spawn 3 质疑者子代理**：若 Free 阶段发现任何深检 issue 或需争议性验证的点，**必须按 [references/adversarial.md](../references/adversarial.md) 模式独立 spawn 3 个质疑者子代理**（证据质疑者/替代解释者/充分性质疑者各一，不得合并 spawn，少任一视为不合规——见反偷懒第14条；**spawn 规格**：`subagent_type: "general-purpose"` + schema 强制结构化，**禁用 Explore** 防只调研不裁决被截断）。产物（`05_deepcheck.md` 的「对抗验证」段）必须记录每个质疑者的 **独立 spawn Agent ID** 作为调用证据。**质疑者 prompt 组装后过 anti-coaching 扫描 + freshness 检查**（见 [references/adversarial.md](../references/adversarial.md)「输入契约」段）。

> **spawn 等待规格**（引用 [references/adversarial.md](../references/adversarial.md)「显式等待 + 超时机制」段）：spawn 3 质疑者必须**显式等 verdict**——统一走 `Agent` **后台 spawn（`run_in_background: true`）拿 task_id + `TaskOutput(task_id, block=true, timeout=...)` 阻塞等**（唯一可机械执行超时的等待方式，超时把控制权交回主代理）。**禁止**：后台 spawn 后被动等任务通知（挂死/断连永无通知，看门狗失守）/ `run_in_background: false` 裸同步 spawn 当唯一等待手段（`Agent` 工具无 timeout 参数，挂死卡死主代理）/ spawn 后不等待直接进下一步。**超时档位**：首次 spawn 走 10 分钟档（`TaskOutput(block=true, timeout=600000)`，`BACKGROUND_WATCHDOG_SECONDS=600`，超时判疑似断连 → `TaskStop` → 前台重来）；前台重来/重试走 `TIMEOUT_SECONDS = 120` 档（可由 metadata.task_timeout_seconds 覆盖，超时先 `TaskStop` 停句柄再重试 1 次，换措辞 + 可换 subagent_type 兜底），二次仍超时走 `[未验证-子代理对抗失败]`。**禁止**未等待就标 `[未验证-子代理对抗失败]`——该标签留给「确认失败」的子代理，不得给「仍在跑/返回晚」的子代理（2026-07-29 实测踩坑）。判定状态四态枚举（`sync_ok` / `timeout_retry_used` / `still_failed_after_retry` / `env_no_spawn`）必须写入 `adversarial_verification` 字段便于审计（详见 [references/adversarial.md](../references/adversarial.md)「显式等待 + 超时机制」段）。**防 `max_output_tokens` 截断**：质疑者 spawn 须含输出预算硬约束（verdict 第一动作/禁回显/总输出≤2000 token 宽松上限勿压紧，见 adversarial.md「spawn 规格要求」第 4 条）。
> **主代理代行硬禁止**：`no_spawn_env = false` 时**禁止**主代理代行三视角判断！即使部分子代理未回结果（如 1/3 返回 verdict、2/3 超时），其余未回子代理必须按「子代理失败处理」失败链路降级（标 `[未验证-子代理对抗失败]`），不得"补齐"为代理代行。主代理代行**仅在** `no_spawn_env = true`（结构性无 spawn 工具）时允许——环境启动检测 `Agent` 工具不可用时置 true。

子代理失败时按 adversarial.md「子代理失败处理」重试 2 次（含 1 次换 subagent_type）→仍失败诚实降级为 `[未验证-子代理对抗失败]`，**绝不改由主代理自演裁决**。

## 执行步骤

1. 检测最新目录，确定 `ICODE_OUT_DIR`
2. 读取 `03_plan_final.md` 和 `.ico_metadata.json`
   - 若 `.ico_metadata.json.code_compile_failed == true`，输出 `⚠️ 步骤4编译失败，仍继续复检` 警告；若 `test_failures == true`，输出 `⚠️ 步骤4测试未通过（test_outcome=fail），重点复检测试失败相关功能点` 警告
3. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少3步）= 梳理代码清单 → 回顾计划要点 → 制定逆推/Fixed/Free 检查策略
4. **分步续跑**：若 `status == "deepcheck_in_progress"`，从 metadata 恢复 `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`，同时读取已存在的 `05_deepcheck.md`（若含「Reverse 逆推」段 **且 `metadata.patch_count` 为 0/缺失（无补丁修改）** 则跳过 Reverse；**有补丁（`patch_count > 0`）时不跳过**——补丁修改必须重新纳入 Reverse 逆推，重跑 Reverse 覆盖更新逆推段，再进入后续阶段）
5. 否则初始化 `deepcheck_clean_rounds = 0`, `deepcheck_total_rounds = 1`, `deepcheck_phase = "reverse"`, `status = deepcheck_in_progress`
6. 输出：`▶ 步骤5 复检开始`

### 前置强制执行门（防"只写结论不执行"）

> **续跑跳过**：若步骤 2 读取的 `.ico_metadata.json` 中 `status` 已是 `"deepcheck_in_progress"`（说明是步骤 4 分步续跑恢复），跳过本门，直接进入恢复的 `deepcheck_phase` 对应阶段。

**非续跑时，在写入 05_deepcheck.md 任何内容之前，必须依次完成以下动作。未完成即写产物 = 跳过步骤 = 严重违规，不可交付。**

1. **Read 所有代码文件**：Read 步骤4 产出的每个 `.c`/`.cpp`/`.py`/`.ts`/`.go` 等代码文件，输出 `📖 已 Read 代码文件（最新版）：<file1>, <file2>, ...` 确认行
2. **Read 计划产物**：Read `03_plan_final.md`（计划定稿）和 `.ico_metadata.json`（含 code_files 列表）
3. **Read 上游复检产物**：若 `{ICODE_OUT_DIR}/04_code_review_fix.md` 存在则 Read，记录未通过维度
4. **输出 Reverse 逆推要点**：在思考块列出至少 3 个具体逆推方向（如：关键函数签名、数据结构、跨文件调用模式），不可笼统写"理解代码"

**只有以上 4 步全部完成，才能进入「阶段 1 — Reverse」**。思考前置证据（sequential-thinking 或文字块）不可替代上述 Read 确认行——思考是思考，读代码是读代码，缺一不可。

### 阶段间强制校验门（防"阶段偷工"）

**每个阶段开始前，必须完成以下检查才能进入下一个阶段**：

| 检查项 | Reverse 前 | Fixed 前 | Free 前 |
|--------|-----------|---------|--------|
| 已 Read 代码文件（最新版）并输出确认行 | ✅ 必须 | ✅ 必须 | ✅ 必须 |
| 上阶段产物已写入 05_deepcheck.md | N/A | ✅ 必须含 Reverse 段 | ✅ 必须含 Reverse + Fixed 段 |
| 上阶段 has_issues 已处理（修复/分流） | N/A | ✅ Reverse issue 已分流 | ✅ Fixed issue 已分流 |
| 思考块列出本阶段检查策略 | ✅ ≥3 方向 | ✅ 7 维度覆盖策略 | ✅ 15 角度全覆盖策略 |

**任一检查项不满足 → 不切换阶段**，必须回补。禁止"先切阶段再补"（切了就不会补了——实测踩坑模式）。

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
- **现有同类实现对比**（防重复实现，独立于上面三类代码-计划 diff）：对逆推出的每个**新增功能/新增接口**，按 [references/necessity_check.md](../references/necessity_check.md) 全工程检索等价实现（`rg -in '<需求关键词>'` + Read 命中处行为链）。发现等价实现 → 标 issue，**计入 has_issues**——**这层对比专门抓"代码与计划一致、但计划本身是重复实现（已有模块已覆盖该功能）"的情况**，diff 三要素全部发现不了；判定要点：新功能"写出来也不会执行到（被已有入口/拦截先返回挡掉）" = 重复，比"功能近似"更确凿
- **首次激活路径双侧校验一致性检查**（独立于上面代码-计划 diff）：当 Reverse 逆推出的功能涉及**跨层接口 / IPC** 时，核对**跨层接口双侧校验一致性**。定义与完整核对清单见 [references/first_activation_path.md](../references/first_activation_path.md)。对每个接口 action：grep 历史日志确认是否有**成功调用记录**；若无（首次激活路径）→ Read 请求方入参校验 + Read 接收方校验逻辑，逐字段对比两侧对同一字段的接受值要求是否一致 → 不一致标 issue，**计入 has_issues**。**软信号**：历史日志可能不全，命中只触发核对，不阻断。**与代码-计划 diff 的区别**：上面 diff 抓"代码 vs 计划"偏离，本检查抓"**代码与计划一致、但两者都基于有 bug 的首次激活路径**"——计划本身是错的，diff 发现不了

- **blast-radius 三链自检（新增）**：对 `code_files` 每个文件，用 `grep -rn '<符号>('` 对每个改动符号找所有引用点（跨仓库/子仓库见 anti_laziness 第 21 条「跨仓库/子仓库检索」段）。grep 结果作为"修改影响面证据"，与 Reverse 逆推的"跨文件调用关系"段互相印证。任一链 0 命中即不合规（未扫 = 自欺）。
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

**重新读取所有代码文件** + 输出 `📖 已 Read` 确认行。一次性完整覆盖全部 15 个角度（A1-A15），每个角度须给出 ≥3 具体检查点（文件名+行号）。严禁"整体通过"等偷懒措辞。**A7 并发与重入安全特殊要求（O-5 闭环）**：计划含「线程归属矩阵」时，A7 检查点必须**与计划矩阵逐行核对**——每个字段的读/写方/锁序/并发策略按实施代码逐行验证，发现「跨线程无锁共享 / 锁序反向 / 写方无锁且未按矩阵给出并发策略结论」即标 issue，不得只写"无并发问题"笼统通过。**输出强制表格**（填不满=偷懒一眼可视）：

| 角度 | 检查点1（file:line） | 检查点2（file:line） | 检查点3（file:line） | 结论 |
|------|------|------|------|------|
| A1 计划实施一致性 | <file:line> | <file:line> | <file:line> | pass/issue |
| A2~A15 | ... | ... | ... | ... |

### 阶段 4 — Dedup（语义重复函数检测完整版）

> **强证据场景判定**（详见 [references/mcp_per_step.md §5 deepcheck](../references/mcp_per_step.md)）：
>
> - cheap-research 🟢（`mcp__cheap-research__extract` 可用）
> - **函数数 ≥ 50**
>
> **任一不满足 → 整个 §9.4 跳过**，在思考块 `MCP 调用` 段写明降级原因，不写产物文件。
>
> **复用 §2.5.7 产物**：检测 `{ICODE_OUT_DIR}/<ticket>/dedup/categorized.json` 是否存在（由 §2 02_review §2.5.7 生成）→ **复用避免重跑分类阶段**（节省 haiku 调用的 token）。

**执行步骤**（AI 直接照填）：

1. **复用检测**：Read `{ICODE_OUT_DIR}/<ticket>/dedup/` 目录，分别判定：
   - `catalog.json` 存在 → 跳过第 2 步
   - `categorized.json` 存在 → 跳过第 3 步
   - 否则按需重跑
2. **抽取阶段**（如 catalog.json 不存在，ripgrep 优先）：

   **优先用 ripgrep**（同 §2.5.7 第 1 步命令）：

   ```bash
   rg -n --no-heading \
     -e '^(static\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s+[*&]?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(static\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(async\s+)?function\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(async\s+)?(const|let|var)\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*(async\s+)?\(' \
     -e '^(async\s+)?(const|let|var)\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*(async\s+)?function' \
     -e '^def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^func\s+(\([^)]*\)\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^(pub\s+)?fn\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     -e '^\s+(public|private|protected)?\s*(static\s+)?[a-zA-Z_][a-zA-Z0-9_*]+\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(' \
     --glob '!*.test.*' --glob '!*.spec.*' --glob '!**/__tests__/**' \
     "$PROJECT_ROOT" | head -2000
   ```

   **ripgrep 不可用**（未装）：整个 §9.4 跳过
3. **分类阶段**（如 categorized.json 不存在）：调 `mcp__cheap-research__extract`（haiku）**双层分类**（parent + sub）→ `dedup/categorized.json`。**双层 schema** + **后处理映射（只映射 parent_category）** 同 §2.5.7 第 3 步。
4. **拆分阶段**：Claude 直接做——**按 sub_category 拆分** `categorized.json`（不按 parent_category——避免跨家族合并），**仅保留 3+ 函数的 sub_category**（< 3 不值得分析），输出 → `dedup/duplicates/<sub_category>.json`
5. **找重复阶段（高质量模型逐类）**：同 §2.5.7 第 5 步——**简化 schema** + **主代理 try 两种解析格式**（A: JSON 数组字符串; B: 分隔符纯文本）+ **用 categorized.json 回填 file/line**
6. **报告生成**：在 05_deepcheck.md 末尾追加 `## 语义重复检测报告（§9.4 完整全量）` 段，按 HIGH/MEDIUM/LOW 三段展示：

   ```markdown
   ## 语义重复检测报告（§9.4 完整全量）

   **函数总数**：{N} | **扫描类别数**：{K}/25 | **生成时间**：{ISO timestamp}
   **复用**：dedup_categorized.json {复用/重跑}

   ### HIGH 置信度重复（建议立即合并）
   | Intent | Category | 推荐保留 | 应删除函数 |
   |--------|----------|----------|-----------|
   | ...    | ...      | ...      | ...       |

   ### MEDIUM 置信度重复（建议人工审查）
   | Intent | Category | 推荐保留 | 差异点 |
   |--------|----------|----------|--------|
   | ...    | ...      | ...      | ...    |

   ### LOW 置信度（可能相关，时间允许时复核）
   | Intent | Category | 函数对 |
   |--------|----------|--------|
   | ...    | ...      | ...    |

   **中间产物**：`{ICODE_OUT_DIR}/<ticket>/dedup/{catalog,categorized,duplicates/*.json}`
   ```

7. **产物文件附加**：每条 HIGH/MEDIUM 重复函数对同时作为 issue 计入**dedup 内部 `has_issues` 计数器**（**不与 Reverse/Fixed/Free 的 `has_issues` 共享**——dedup 是独立扫描，不触发整体循环），`evidence_pointer` 指向 `dedup/duplicates/<category>.json:<line>`，`suggestion` 写"合并为 `<survivor>` + 删除其他实现"，`verification_status` 直接标 `confirmed`（已用高质量模型推理，无需再走 §5 A6 独立 3 质疑者对抗——单视角推理质量足够（详细理由同 §2.5.7））

**降级路径**：

- cheap-research 不可用 → 整个 §9.4 跳过，记 `[降级-cheap-research 不可用]`
- 函数数 < 50 → 输出 `▶ §9.4 跳过：函数数 {N} < 50，工程规模太小无需 dedup`，整个 §9.4 结束
- 函数数 > 500 → 分批（每批 100），合并结果
- extract 返回 `schema_validation_failed` → 重试 1 次（自动改 instruction 加"严格按 schema 输出"），仍失败标"分类降级-单类跳过"
- 高质量模型某类返回空数组 → 该类跳过（无重复），不报错

**反偷懒第 21 条合规**：步骤末尾在思考块输出 `cheap-research 调用: extract x {1+1+K}` 或对应降级声明，**无记录 = 违规**。

**与 §5 A6 独立 3 质疑者 spawn 规则的衔接**：dedup 的 issue **不进入** Free 阶段 A6 的 3 质疑者独立 spawn 流程。理由：dedup 用高质量模型单次推理 + cheap-research schema 强约束 + 22 类预定义约束 = 等效"强约束推理"，质量足够；重复 3 次 spawn 成本翻 3 倍但收益边际递减。这是 §9.4 阶段的**显式例外**——A6 规则继续约束 Free 阶段深检 issue。

**与循环控制段的关系**：dedup 是独立扫描，不参与 Reverse/Fixed/Free 的整体循环。dedup 内部修复循环（has_issues=true 时）：修复**重复函数本身**（合并为 survivor，删除其他实现）→ 重新调 ripgrep 抽新 catalog → 重新调高质量模型找剩余重复。修完 → 写 `dedup_report.md` + 追加 05_deepcheck.md 摘要 → 进入原「循环控制」段继续 Reverse/Fixed/Free 的整体判定。**绝不可让 dedup 的 issue 触发 Reverse/Fixed/Free 重跑**——重复函数修复与计划/代码逆推无关。

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


**over-design 复检（反偷懒第 26 条）**：Reverse/Fixed/Free 阶段均核对 plan 修复方案分档 + 实施范围。检查点：①plan 修复方案分 A/B/C 三档？②实施范围 = A 档 + 确认的 B 档（B 需 `confirmed_B_fixes`）？③B/C 混入 A 主方案或超范围实施 = issue。与步骤2 over-design 检查对齐。


## 完成前自检（必须填，未填项标 ❌=不合规）

- □ Reverse/Fixed/Free 三阶段都输出了 `📖 已 Read` 确认行（列出实际 Read 的代码文件）
- □ Free 每个角度 ≥3 检查点（file:line），表格填满
- □ Fixed 每维度有 file:line 证据 + 评分理由 ≥2 句实质
- □ 无"整体通过""无问题"等空泛结论（每条结论有具体证据）
## 决策锚点（步骤5 完成后写）

步骤5 复检完成后，若 `metadata.anchors_enabled != false`，刷新 `.decision_anchors.json`：刷新 `open_risks`（deepcheck 残留风险）。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| playwright | 🟢* | 跑 E2E--前端工程时 |
| vision-bridge | 🟢* | UI 截图复检--用户给图时 |
| **cheap-research** | 🟢* | **降本**：Reverse 阶段 diff_summary（计划vs代码差异）+ summarize（长审查输出压缩）。不接管决策：Fixed/Free 阶段/3 质疑者对抗走主会话 |
| context7 | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
