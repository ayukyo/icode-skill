# 步骤 4 — 严格落地实施编码

**命令**: `/icode code`
**产出**: 代码文件
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 前置产物缺失（`03_plan_final.md` 不存在） | 报错退出，提示先跑 `/icode merge` |
| **L1·致命** | 当前工单是 debug 工单（`metadata.debug == true`） | 报错退出，提示：`/icode code` 不接受 debug 工单（debug 工单不入索引、不参与主流程，纯作为正常工单的对照；详情见 [references/debug_mode.md](../references/debug_mode.md)） |
| **L1·致命** | 统一拓扑门禁 verdict=blocked（双活动实现根 / 子仓逃逸 / 未完成迁移 / cwd 不符） | 报错退出，输出冲突路径与各自 dirty/commit 情况，提示先 `/icode worktree --update` 或人工裁决（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |
| **L2·关键** | Code Review Fix 4 维度复检**全部失败**（4 个维度都标 ❌） | 警告 + 记入 metadata + 流程继续（不阻断；user 可事后回代码修复/重设计） |

**L3·重要**（矩阵段定义）：编译失败（3 次仍失败）→ 设 `code_compile_failed=true`，步骤 5 入口警告，**流程继续**。测试失败（3 次仍失败）→ 设 `test_failures=true`，步骤 5 入口警告，**流程继续**（与编译失败同级 L3）。

## 前置校验

> **读决策锚点**（启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取上游关键决策摘要（requirement_digest/key_decisions/design_4dims/deviations/open_risks）作本步骤上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 是否存在，不存在则报错并提示先执行 `/icode merge`。

**用户语义变更检测（O-4，同 [02_review.md](02_review.md) 前置校验）**：读 `metadata.scope_contract`（缺失视为 null＝未冻结，跳过，向后兼容旧工单）；若**用户本次输入**改变冻结契约语义（状态身份或生命周期 / 允许或拒绝条件 / 持久化一致性或回滚承诺 / 验收条件、调用方语义或真实环境验证场景）——先分类写入 `metadata.requirement_deltas`（追加，分类枚举与判定同 02_review 前置），**未分流不得实施对应变更**（`needs_user_confirm` 未确认 / `needs_replan` 未重定稿 → 先回步骤3 分流）。若用户输入仅澄清不改变契约，则无 delta，正常实施。

## 前置：统一拓扑门禁（共享检查器）

> 进入编码前**必须**调用统一拓扑检查器（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)），verdict 语义：`pass` 继续 / `repairable` 仅执行无歧义、可逆、幂等的 metadata 修复后继续 / `blocked` **报错退出**。**禁止**绕开本门禁直接在旧 checkout 修改代码——在非活动 checkout 修改 = 证据与实现脱节，编译/测试通过也不构成当前活动实现的通过证据。子仓隔离硬门（下段）是拓扑门禁「⑥ 子仓拓扑」的实施细则，两者都要过。

## 前置：worktree 工单业务子仓隔离（repo 多仓库工程）

> 若本工单是 worktree 隔离工单（`metadata.active_checkout` 非 null，缺失按 [references/worktree_isolation.md §3.7](../references/worktree_isolation.md) 用 `worktree_path` 推导）且工程为 repo 多仓库（super-repo + 业务子仓，子仓各自独立 git 仓库），**super-repo worktree 不覆盖业务子仓**——子仓有自己的 `.git` 在原工程路径，worktree 内对应相对路径为空。本步骤进入编码前须确定实际修改的业务子仓，并为每个受影响子仓建立隔离 checkout，**禁止直接改原工程路径子仓**（会污染原工程、多需求并行冲突）。规范见 [references/worktree_isolation.md](../references/worktree_isolation.md)「⑤ 业务子仓隔离」。

1. 读 `{ICODE_OUT_DIR}/03_plan_final.md` 的 code_files/§5 符号清单，确定本需求实际修改的业务子仓集（子仓相对 super-repo 路径经 `.repo/manifest.xml` `<project path>` 推导，见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「repo 嵌套子项目路径推导」）
2. 不涉及子仓修改（只改 super-repo）→ **跳过本段**，无需隔离
3. 对每个受影响原子仓，若 `metadata.sub_worktrees` 未含该子仓 → 建子仓隔离 checkout（把 checkout 放进 super-worktree 同名相对路径，保持路径结构与原工程一致；**子仓分支基于子仓当前分支的远程跟踪 `@{u}` 创建 + 自动 upstream**，无 upstream 降级本地 HEAD）：
   ```bash
   SUB_UP=$(git -C "<主仓绝对路径>/<子仓相对路径>" rev-parse --symbolic-full-name @{u} 2>/dev/null)
   SUB_UP_AVAIL=0
   [ -n "$SUB_UP" ] && git -C "<主仓绝对路径>/<子仓相对路径>" rev-parse --verify "$SUB_UP" >/dev/null 2>&1 && SUB_UP_AVAIL=1
   if [ "$SUB_UP_AVAIL" = "1" ]; then
     git -C "<主仓绝对路径>/<子仓相对路径>" worktree add -b "icode/<ticket-slug>-<子仓slug>" "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" "$SUB_UP"
   else
     git -C "<主仓绝对路径>/<子仓相对路径>" worktree add -b "icode/<ticket-slug>-<子仓slug>" "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>"
   fi
   ```
   前置：子仓须有 HEAD（repo 子仓均有）；目标路径须为空（super-worktree 内该相对路径未被写入）
4. 写 `metadata.sub_worktrees` 追加 `{sub_path, worktree_path, branch}`（见 [references/worktree_isolation.md](../references/worktree_isolation.md)「§3 metadata 字段族」）
5. **门禁（硬门）**：受影响子仓未全部隔离即改 = 直接改原工程路径，**不合规**——进入下方「执行步骤」编码前必须确认。历史事故：AI 曾靠模型智能自行加门禁提示而非由 icode 规范保证——本段固化为规范，AI 不得自行裁量

## 前置：patch 配合

> 工单可能已走过 `/icode patch` 追加修改（`{ICODE_OUT_DIR}/08_patch.md` 存在且有 Patch 段，或 `metadata.patch_count > 0`）。本步骤启动时 **Read `08_patch.md`**（不存在则跳过本段，走原流程），按以下规则配合：

1. **在 patch 基础上实施**：本步骤的实施基准 = `03_plan_final.md` 计划 + `08_patch.md` 已落地的修改。Write 已由 patch 改过的文件时**保留 patch 修改**（只叠加本步骤的改动，不整文件覆盖回计划版）
2. **patch 与计划设计冲突**：patch 改动的符号/行为与 `03_plan_final.md` 设计不一致时，**不得擅自把代码改回计划版**——记入 metadata `code_deviations`（`plan_said`=计划说法 / `actual_done`=patch 实际做法 / `reason`）+ 在步骤输出中提示用户"patch 修改与计划冲突，以 patch 为准已记录偏离"
3. **三链预扫范围扩展**：除计划 §5 声明的符号外，`08_patch.md` 最新 Patch N 段涉及的符号同样逐个预扫（caller/import/test 三链）
4. **产物记录**：本步骤对 patch 修改的叠加/偏离处理，写入 `04_code_review_fix.md` 或步骤输出，供步骤5/6 回溯

## 前置：schema 迁移（自动/幂等/原子）

> 自动识别 + 自动迁移：用户无需任何操作，进入步骤 4 时若检测到 `.ico_metadata.json.template_version` 缺失或旧于当前版本，**自动**在已存在的 `03_plan_final.md` 末尾追加新段、补写 metadata、原子落盘；幂等（已含迁移段则跳过），失败兜底静默（不阻塞主流程）。

**自动执行**（强制，写在工具调用前）：

1. Read `.ico_metadata.json` —— 读 `template_version` 字段（缺失视为 `"v0"`）
2. 与当前 `CODE_TEMPLATE_VERSION = "v1.1"` 比对（含本步骤三链预扫段 + blast-radius 三链段两条新增），若 `template_version` 已经 ≥ `"v1.1"` → 跳过本次迁移，直接进入"## 前置校验"
3. 否则执行迁移 **（原子，不破坏既有正文）**：
   a. 解析 `03_plan_final.md`，从 §5 模块详细设计章节抽取"将引入的关键符号"列表（去重 + 过滤纯注释）
   b. 对每个符号跑三条 grep（命令见下方"三链预扫"段）
   c. grep 结果追加到 `03_plan_final.md` 末尾的 `## 三链预扫记录（v1.1 自动迁移）` 段（不存在则新建段；用 **`grep -F '三链预扫记录（v1.1 自动迁移）'`** 检测是否已含，字面量模式，原子写：先写 `.tmp` 再 `mv`；保留原文件前 N 行不动）
   d. 写回 metadata：增字段 `template_version = "v1.1"`、增 `migration_log` 数组追加 `{from, to, at, files}` 条目、`at = date +%Y-%m-%dT%H:%M:%S` 取系统时间（不写死）
   e. 输出 `▶ 自动迁移 → v1.1：补全三链预扫段（迁移到 .ico_metadata.migration_log）`
4. 任一步失败（文件 I/O 错、grep 不可用、03_plan_final 不存在）→ 静默跳过迁移、记 `[迁移跳过-原因]` 到 metadata migration_log，主流程继续（绝不阻塞步骤 4）

**向后兼容**：

- 旧工单（缺 `template_version`）一律视为 `v0`，自动升级到 v1.1
- 已迁移的 metadata 再次进入步骤 4 不会重复追加（按 `grep -F '三链预扫记录（v1.1 自动迁移）'` 字面量去重）
- 用户手改过的 `03_plan_final.md` 原文**不会被覆盖**——只在末尾追加新段
- metadata 字段新增（`template_version` / `migration_log`）一律非破坏性，旧 metadata 缺这字段视为默认（`v0` / `[]`），写回时整对象保留

## 执行步骤

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 自动迁移（如上）—— 仅迁移到 v1.1
3. 读取 `{ICODE_OUT_DIR}/03_plan_final.md` 获取定稿计划
4. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少4步）= 梳理文件清单 → 规划接口 → 预判冲突点 → 确认注释策略

   - 计划中的伪代码和行号引用需要在此步骤展开为完整实现。读取引用的源文件获取完整上下文
5. 输出步骤确认：`▶ 步骤4 编码开始`

### TDD 准入门（RED 硬门，行为变更默认 required；测试驱动非实现后验证）

> **目的**：把"测试通过"升级为"测试先证明能抓住问题（RED）、再证明修复有效（GREEN）、再证明未破坏相邻行为（regression）"。**RED 是准入证据，GREEN 不是交付终点**。测试形态不限于单元测试（unit/静态/契约/集成/特征/主机侧协议配置/设备侧行为均可），但"先取得有效失败证据、再改生产代码"不能因"配置修改/改动少/fast/patch"跳过。

**0. 判定 `tdd.mode`**（Read `metadata.tdd`，缺失视为 `not_assessed`）：`required`/`contract`/`characterization` = 需先 RED；`device_split`/`blocked` = 允许在明确边界内继续准备代码但交付验证保持待完成；`exempt` = 纯文档/注释/无生产行为变化（须 Read `metadata.tdd.reason` 确认豁免理由，缺理由视为违规）。**分类缺失（`not_assessed`）且本工单含行为变更 → L2，先回步骤1/3 补测试契约**；不得静默默认 exempt。

**1. RED 阶段（生产代码 Edit 前的硬门；`required`/`contract`/`characterization` 模式 L1 强制）**：
   1. 记录活动 checkout + 生产文件哈希 + 现有 staged/unstaged/untracked 基线（沿用下方「强制操作·git 状态快照」三态判别）——**不得回滚用户已有修改**；若用户修改已实现目标导致测试通过，报告并重新确认剩余工作
   2. 只新增/修改测试文件及其最小测试基础设施（**禁止此时 Edit 任何生产文件**）
   3. 运行计划中的最窄测试命令（`metadata.tdd.red.cmd` 或计划测试契约「测试命令」），捕获退出码与失败摘要
   4. **分类失败**（写入 `metadata.tdd.red.failure_class`）：
      - `expected_assertion`（目标断言失败，与计划「RED 预期」一致）→ **有效 RED**，`tdd.status=red_verified`，允许进入生产代码 Edit
      - `harness_compile_error` / `harness_import_error` / `environment_error` / `timeout` / `flaky` → **不是有效 RED**：修测试基础设施/消除不稳定性后重跑（**不能靠重复运行挑一次失败当 RED**），不得推进 red_verified
      - `unexpected_failure` → 重新审查测试是否覆盖目标行为
   5. **测试首次即通过**（退出码 0）→ **停止生产代码修改**，重新做必要性检查：可能是行为已存在，也可能是测试未覆盖目标；不得以"测试通过"作为继续写代码的理由，须回步骤1 复核或补强测试
   6. **L1 硬阻断**：`required`/`contract`/`characterization` 模式下，未取得有效 RED（`failure_class=expected_assertion` 且 `tdd.status` 未达 `red_verified`）就准备 Edit 生产代码 → **L1 停止实施**，提示"先取得有效 RED（测试先证明能抓住问题）再改生产代码"；修正测试后可继续

**2. GREEN 阶段（只做最小生产修改后）**：
   1. 严格按计划做最小生产代码修改（不在 GREEN 阶段顺手重构/扩展接口/修改无关测试）
   2. 重跑**完全相同**的 RED 命令（`metadata.tdd.red.cmd`）
   3. 退出码 0 → `tdd.status=green_verified`；若必须修改测试才能通过，说明是修正错误期望还是放宽契约——**放宽断言不得被当作生产修复成功**

**3. Regression 阶段（复用现有验证能力）**：GREEN 后继续执行下方「强制操作·编译验证 + 测试验证」——目标测试 + 受影响模块回归 + 工程构建 + Code Review Fix；`test_cmd`/`test_outcome`/`test_failures` 继续作为**最终回归摘要**，不用于覆盖 RED 历史证据（RED/GREEN 证据在 `metadata.tdd.red/green`）。GREEN 后构建/回归失败沿用现有 L3 降级，但 `delivery_verdict` 不得写已验证。

**4. 落盘**：RED/GREEN/regression 证据写入 `metadata.tdd`（结构见 [SKILL.md](../SKILL.md) metadata 段）：`{mode, red: {cmd, exit_code, failure_class, expected, observed_excerpt, at}, green: {cmd, exit_code, at}, regression: {cmd, exit_code, at}, status}`。**O-6 用户自担验证（用户要求不跑测试）**：可完成代码修改，但 `tdd.status=blocked`（或确属豁免时 `exempt`）+ `delivery_verdict=verification_pending`，交付措辞"已完成代码修改，待实机验证/待用户验证"，不得写"已修复并验证"。

### 编码实施

严格按定稿计划实施编码。

**符号定位（grep 优先）**：编码前对计划 §5 声明的待改符号，用 `grep -rn '<符号>'` 定位待改符号定义、`grep -rn '<符号>('` 找所有调用点（跨仓库/子仓库见 反偷懒第 21 条「跨仓库/子仓库检索」段），结果作为下方「准入三链预扫」的增强输入。检索结果只进思考块，不写入产物文件。

**准入（强制三链预扫，每条按 `文件:行号` 给出至少 1 条命中否则禁止 Edit）**：

> 受影响的"改/新增符号"必须在 Edit 前 **逐个** 输出三条 grep 结果；任一条 0 命中即不合规（先扩大范围，仍 0 命中则按"未找到、不存在"在计划中标注，不能默默跳过）。本预扫每一步强制落地，**禁止**仅凭直觉/经验跳过（旧工程代码稀疏时，0 命中本就是信号）。
>
> 1. **caller 链**：`grep -rn '<symbol>(' src/ include/`（找所有调用方，调用即影响面）
> 2. **import 链**：`grep -rn '<header\|from <module>\|import <pkg>'`（找所有 include/import 来源，改签名/语义会被牵连）
> 3. **test 链**：`grep -rln '<symbol\|<module>.*test'`（找所有可能受影响的测试文件，至少确认 test 不挂在旧路径上）
>
> **与自动迁移的协作**：迁移已为"将在 03_plan_final.md §5 模块详细设计中声明的符号"生成过三链预扫段；本"准入"段对**所有实际 Edit 的符号**逐个实时输出（含迁移段未列、临时发现的新符号）——两者并存不替代，迁移段给基线，准入段给当下实时证据。
>
> **示例**：在 `demo/calc.c` 加 `isqrt` 函数，caller 链 `grep -n 'isqrt(' demo/*.c` 应返回 0（首加）；import 链 `grep -n '#include.*calc.h' demo/*.c` 至少 1；test 链 `grep -rln 'calc.h\|sqrt' demo/` 看是否有现成测试。

**硬性要求**：
1. 先阅读项目中现有的相关代码，了解实际架构和代码风格
2. 严格对齐计划中的功能、规范、边界要求
3. 不删减逻辑、不新增计划外功能
4. 保留现有注释，新增注释遵循项目风格
5. 跨文件修改：修改函数签名/数据结构时，同步更新所有引用方
6. **代码注释增强**：每个导出函数/接口必须有注释说明（功能、参数、返回值、错误码含义）；关键分支和边界条件必须有行内注释解释意图；数据结构成员须说明用途和约束；复杂算法须有流程注释
7. **注释工程化（不写 icode 工作流元数据）**：代码注释是给**未来的工程读者**看的——他们可能完全没用过 icode 工作流。注释里**只写工程语义**（功能/参数/边界/不变量/为什么这么设计），**不要写** icode 工作流元数据，如 `// icode review R3 修复` / `/* ticket: demo-3 */` / `// step 4 落地` / `// 按 02_review.md 修改` 等——这类信息归 git commit message / CHANGELOG / 内部工单系统，不污染代码注释。**反例→正例**：`// 修复 review R3-issue-2 提到的边界 case` → `// 边界：输入长度 0 时不抛异常，返回空结果`（只说"为什么"和"是什么"，不说"由谁审查"）
8. **日志覆盖增强**（不指定具体日志库，遵循项目现有风格）：以下关键路径必须有日志，便于运行时排查——①错误/异常返回点（含错误码、失败原因）；②状态机跳转/关键流程节点切换；③外部交互（协议收发、IO、跨进程调用）的入口与结果；④关键决策分支（为何走此路径）；⑤降级/重试/超时处理。日志级别与格式**遵循项目既有日志风格**（如项目用 spdlog/LoggerManager/ROS_LOG 等，照其约定），不得引入新日志库。纯计算/无副作用的内部函数不强制加日志
9. **主动偏离记录**：编码过程中若发现定稿计划不可行而主动偏离（如计划接口签名无法落地、计划数据结构与现有约束冲突），**不得擅自改回计划**——将偏离记录追加到 `.ico_metadata.json` 的 `code_deviations` 数组（每条含 `plan_said` 计划说法 / `actual_done` 实际做法 / `reason` 原因），供步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段。无偏离则不追加
10. **代码链路习惯一致性（优雅度6条）**——新增代码必须像"工程原生"写的，不得是"功能对但风格突兀"的异物：
   - **复用优先**：新增工具/辅助函数，grep 工程既有——有等价的必须复用，不重造轮子（如工程有 `mul_overflows` 就调它，不自写 `check_overflow`）
   - **风格对齐**：命名风格（前缀/大小写/下划线）、错误处理模式（错误码返回 vs 异常 vs abort）、日志模式（LoggerManager vs printf）、注释格式（`/* */` vs `//`）必须与同文件/同模块既有代码逐一比对一致
   - **调用链模式一致**：工程用 handler 注册模式就别写 switch-case 硬编码；工程用 PropertyCenter 传状态就别引入全局变量；工程用 RAII 就别手动 malloc/free
   - **最小侵入**：只改必要的文件/函数，不顺手重构既有变量名/函数签名/注释格式；git diff 应只有"新增+必要修改"，无无关改动
   - **接口克制**：新增导出函数/类只暴露必要接口，能 static 就不 public，不为"将来可能用到"预留参数（YAGNI）
   - **调用路径选择（架构一致性）**：新增跨模块/跨端点/跨层调用时，grep 同文件/同模块既有同类调用——若工程有主导模式（如统一走路由/事件分发而非直调），必须沿用，不得以"内部触发更简单"为由另选直调；若路由/接收器/事件总线已注册，优先走已建链路而非直调绕过；**同函数内既有同类调用是最强信号**，新增调用必须与之风格一致。编译通过 ≠ 调用模式正确——编译器无法区分"风格异物"，必须 grep 既有模式实证
   - **跨层翻译纯函数化 + 测试覆盖度**：跨层翻译逻辑（外部模块枚举值映射到本模块契约）必须提取为 `constexpr noexcept` 纯函数，**禁止内联在消费点**——便于独立单测且未来新增消费点直接复用；测试必须做到 ① **枚举穷举覆盖**（每个枚举值都有独立断言）、② **条件组合覆盖**（多条件与/或的矩阵）、③ **语义级断言**（每条断言带人类可读标签如 "external NOT_INIT does not terminate"）

用 Write 工具创建/修改每个代码文件。


**修复方案三档实施（反偷懒第 26 条）**：默认只实施 A 档（根因修复）；B 档需 metadata `confirmed_B_fixes: [...]` 记录用户显式确认才实施；C 档不实施（范围外）。A 档跨工程（plan 标注）时本工程无可实施 A 档，不强行造 A 档、不把 B 当 A 实施，只做确认的 B 档 + commit/工单注明"A 档转交 <X>"。Code Review Fix 复检核对：实施范围 = A 档 + 确认的 B 档（**先 Read metadata.fix_tiers 读 plan 分档**，字段缺失则从 `03_plan_final.md` §4.5 文本读），超范围实施 = issue。实施 B 档前必须把用户确认记录写入 `confirmed_B_fixes` 数组。详见 反偷懒第 26 条

**范围升级实施核对（scope_escalations，反偷懒第 33 条）**：Read `metadata.scope_escalations`（字段缺失视为 `[]`），只实施 `classification=A_now` 与已确认 `user_confirm` 的 `B_confirm` 条目对应的方案；`C_follow_up`（范围外）与 `refuted`（丢弃）**不得实施**。审查/复检/终审后新增的 escalation 若未经 merge 定稿纳入 `03_plan_final.md`，编码时不得直接实施（须回到步骤3 定稿或取得用户确认并记录）。Code Review Fix 复检核对实施范围时一并核对（超出已确认 escalation 的修改 = issue）。

**用户语义变更实施核对（requirement_deltas，O-4 语义冻结）**：Read `metadata.requirement_deltas`（字段缺失视为 `[]`），存在未分流条目（`classification` 未定 / `needs_user_confirm` 未确认 / `needs_replan` 未重跑 plan）时**不得实施对应变更**——`clarification_only` 不改变实现；`a_now_with_evidence` 仅在 merge 定稿已纳入计划后实施；`needs_user_confirm` 未获 `user_confirm` 不得实施（先回步骤3 确认）；`needs_replan` 未重定稿不得实施（先回 plan）。**实施范围不得超出 scope_contract 冻结边界 + 已分流 delta/已确认 escalation 的并集**（超范围 = issue）。


## 强制操作（完成后必须执行）

0. **git 状态快照（进工程后第一步，改动静止基线；O-2）**：改动报告前先建立"改动归属"基线，防止把"改动没被跟踪"误判为"改动丢失"：
   - 执行 `git -C <project_path> status --short`，并检查 `.git` 形态（`ls -ld <project_path>/.git`）做**三形态判别**：**目录**=普通 git 仓库 / **symlink**=repo 独立仓（`.git` 指向 repo 管理目录，父仓 `.gitignore` 忽略子仓，父仓根目录 `git diff` 恒为空）→ 标注「**repo 独立仓**」/ **普通文件**（内容以 `gitdir:` 开头）= **git worktree 成员** → 标注「**git worktree**」（`git status` 正常，勿误判为异常/独立仓损坏；`project_id` 归主仓见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「project_id 与 branch 语义」F1）
   - 记录三态：**已暂存（staged）**（本对话之前的改动）、**工作区未暂存（unstaged）**（本对话的改动）、**未跟踪（untracked）**；后续改动报告按此三态描述
   - **禁止**在父仓根目录用 `git diff` 判定子仓改动归属（子仓不在父仓工作树内，父仓 diff 为空 ≠ 子仓改动丢失）
   - 改动报告/交付清单写出 `git -C <子仓> status` 的原始视图
1. **编译验证 + 测试验证**：运行项目对应的编译命令（最多尝试 3 次），确保所有文件无错误、无警告；编译通过后自动探测并跑测试套件（借鉴 aider `auto_test` 机制，icode 增加自动探测）。**用户自担验证豁免（O-6，用户工作模式偏好，走行为分支、不新增 metadata 字段，与 `/icode limit` 项目工程约束职责分离：limit 管项目约束，本条管用户个人工作模式偏好）**：需求文本/对话中出现"我自己编译 / 不要 commit / 我自己验证 / 不宣称已修复 / boot 日志 / daily 日志"等表达时识别为用户红线，按下表豁免：
   - 含"用户自编" → 编译/测试验证降为**可选**：不执行编译命令，产物标注"编译由用户执行"，**不触发** L3 编译/测试警告
   - 含"禁 commit/push" → 任何阶段禁止 `git commit`/`git push`，收尾只输出改动清单
   - 含"禁宣称已修复" → 所有产物结论用词统一降级为"**已完成代码修改，待实机验证**"，禁止"已修复"表述
   - 日志红线（boot/daily）→ 见 [log.md](log.md) 日志分析段 boot/daily 禁混看
   - **编译命令真源优先（条件判断，防用 `--help`/试探性 CLI 替代文档真源）**：按序查——①工程 LIMIT 文档（`~/.claude/icode_data/limits/<project_id>.md`）含「编译命令规范」红线（有则按红线准确命令，含基础库 + 多模块同编等工程专属约束）→ ②工程根 `README.md`（有则按 README）→ ③本步骤探测兜底；真源都不存在才允许试探，且须在产物标注"编译命令为试探得出，未经文档验证"。LIMIT/README 均无编译指令时按③探测（**不强制**读 LIMIT）
   - **编译 3 次仍失败**：输出 `⚠️ 编译失败兜底` 警告，设 `code_in_progress` + `code_compile_failed = true`。代码文件仍写入磁盘，`code_files` 仍记录
   - 步骤 5 入口检测到 `code_compile_failed` 时输出警告，但仍继续
   - **测试命令探测**（编译通过后，自动识别工程测试命令，写入 `metadata.test_cmd`，用户可在 metadata 手动覆盖）：
     | 工程文件 | 探测到的 test_cmd |
     |---|---|
     | `Makefile` 含 `^test:` 目标 | `make test` |
     | `package.json` 的 `scripts.test` 非空 | `npm test` |
     | `pytest.ini` / `setup.cfg` 含 `[tool:pytest]` | `pytest` |
     | `go.mod` | `go test ./...` |
     | `CMakeLists.txt` + `build/` 目录 | `ctest --test-dir build --output-on-failure` |
     | `Cargo.toml` | `cargo test` |
     | `pom.xml` | `mvn test` |
     | 都没有 | `test_cmd=null`，跳过测试验证（不阻塞，设 `test_outcome=skipped`） |
     - **可疑命令防护**：探测到或用户配的 test_cmd 含 `deploy`/`prod`/`rm -rf`/`>` 重定向等危险模式 → **不自动跑**，提示用户确认
   - **测试验证**（`test_cmd` 非空时，编译通过后执行）：跑 `test_cmd`（超时 `metadata.test_timeout` 秒，默认 120，可配）
     - **退出码捕获（防管道误判，实测踩坑）**：跑 `test_cmd` 时**重定向输出到临时文件**（`test_cmd > {ICODE_OUT_DIR}/.test_output.tmp 2>&1`）再读退出码，或用 bash `${PIPESTATUS[0]}`--**禁止直接 `test_cmd | tail` 后取 `$?`**（管道末尾命令退出码会覆盖 test_cmd 的，实测 `make test && false | tail` 时 make 退出码 2 被 tail 的 0 覆盖，导致测试失败误判为通过）
     - **测试通过**（退出码 0）→ 设 `metadata.test_outcome=pass`，进入 Code Review Fix
     - **测试失败**（非 0 退出码或超时）→ 把失败输出（尾部 ≤50 行，防 token 爆）加入上下文 → AI 修复 → 重跑（最多 3 次，复用编译验证的重试机制）
     - **3 次仍失败** → 设 `metadata.test_failures=true` + `metadata.test_outcome=fail`，代码仍写入（**不阻断**，L3 警告，与 `code_compile_failed` 同级），进入 Code Review Fix
     - **test_cmd=null** → 设 `metadata.test_outcome=skipped`，跳过测试验证，进入 Code Review Fix
   - **1.5 子段·Code Review Fix（4 维度复检，1 的强制子段）**：编译+测试验证后**必须执行**（**所有工单都触发**，不论 init/log 入口）。**作用**：核对实施是否与计划设计的 4 维度一致——同事提示词"修 bug 后做代码 review 修复，确保没有逻辑 bug 和副作用，确保没有竞态死锁问题，确保解决了日志反映的问题"的工程化复检机制
     - **强制思考前置**（不可跳过）：本步骤子项（至少3步）= 读计划设计的 4 维度基线 → 列实施对照点 → 预判复检偏差
     - **对照基线读取**（**任一缺失则视为设计遗漏**，须先回到 `/icode plan` 补设计）：
       - log 工单：必须 Read `03_plan_final.md`「4.5 修复方案设计 + 4 维度设计态固化」段（log 工单必填）+ `log_analysis.md` §7 + `00_init.md` §5
       - init 工单：必须 Read `03_plan_final.md`「4.5 修复方案设计 + 4 维度设计态固化」段（init 工单必填）+ `00_init.md` §7
     - **4 维度复检清单**（每维度独立勾对，每条须给 file:line 证据）：
       - **维度 1 根因闭环（log 工单）**：H/P/V 三件套是否落实 → Read 实读 P 修复点代码核对实际实现是否与设计一致 → V 是否可观测（日志/返回值/状态变化）→ 反向验证（H 错则 P 是否仍有效）
       - **维度 2 逻辑+扩大修改**：实施 vs 计划设计的逻辑 5 类（边界/状态/异常/时序/数值）覆盖度 → git diff 最小侵入核对（每行变更回指根因/需求点）→ 优雅度6条（复用/风格/调用链/最小侵入/接口克制/调用路径）→ 三链预扫（如有新增符号）。**布尔表达式短路自检**：对本次修改的每个含 `&&` / `||` 的复合布尔表达式——检查 `&&` 前置条件中已出现的变量是否在 `||` 后续分支中重复（前置已保证为真 → `||` 该项恒真短路，后续逻辑被完全跳过）；检查 `||` 分支任一项是否被前置 `&&` 完全覆盖（整个 `||` 退化）。发现冗余必须化简并记录
       - **维度 3 竞态死锁**：实施 vs 计划设计的 10 条清单覆盖度（不涉及的标 N/A）→ 锁/原子/内存序/超时是否按设计落实 → 是否引入新竞态死锁风险
       - **维度 4 日志反映**：V 可观测性是否落实（关键路径日志是否写到位）→ 根因-日志-修复对齐 → 日志级别/风格一致 → 无敏感信息泄露。**双值日志**：若修复涉及状态归一化 / 映射，归一化后的关键路径日志是否同时保留原始值（如 `status={} published_status={}`），防止归一化后丢失上游语义信息导致二次定位困难
     - **复检产出**：写入 `{ICODE_OUT_DIR}/04_code_review_fix.md`（4 维度勾对表 + 未通过维度清单）
     - **复检失败处理**（**轻/重度分流**，不强制阻断）：
       - **轻度失败**（设计与实施不一致，但设计本身正确）：标 `code_review_fix_with_issues=true` + 未通过清单 → 提示用户"实施偏离计划设计，回到代码修复 → 重跑本子段"
       - **重度失败**（设计本身有问题，如维度 3 漏锁/维度 1 H/P 链断）：同上但额外提示"建议回到 `/icode plan` 重新设计——4 维度设计态本身有缺陷"
       - 任一失败 → `status` 保持 `code_in_progress`，`completed_steps` **不**追加 `"4"`
     - **复检通过**：`code_review_fix_with_issues=false` + `status = code_done` + `completed_steps` 追加 `"4"`
2. **更新元信息**：
   - 将 `code_files` 更新为所有新增/修改的**相对项目根目录**的路径列表
   - **状态流转**（按 1.5 复检结果判定）：
     - 1.5 复检通过 → `status = code_done`，`completed_steps` 追加 `"4"`（**`code_review_fix_with_issues = false`**）
     - 1.5 复检失败（轻/重度） → `status` 保持 `code_in_progress`，`completed_steps` **不**追加 `"4"`（**`code_review_fix_with_issues = true`**）
     - 编译失败（1.5 未执行） → `status = code_in_progress`，`code_compile_failed = true`，`completed_steps` **不**追加 `"4"`
   - **写入测试字段**：`test_cmd`（探测/配置的测试命令，null 表示无测试套件）、`test_outcome`（`pass`/`fail`/`skipped`）、`test_failures`（3 次重试仍失败置 true）。**测试失败不阻断流程**（与编译失败同级 L3），步骤5/6 入口检测 `test_failures=true` 时输出警告
   - **写入 `code_deviations`**：若有主动偏离（见硬性要求第8条），将偏离记录数组写入 metadata `code_deviations`（每条含 plan_said / actual_done / reason），供步骤6 汇总；无偏离则写空数组 `[]`
   - **写入 `code_review_fix_with_issues`**（v1.x 新增，可选，默认 `false`）：4 维度复检未通过标记。`true` 时步骤 5/6 入口输出警告，audit 终审会看到此标记（**不阻断流程**，仅作可见性提示）
3. 全流程模式：编译通过 + 测试通过（或 `test_cmd=null` 跳过）+ 1.5 复检通过则**立即继续执行步骤5**；编译失败或 1.5 复检失败则中止，提示用户修复。**测试失败（`test_failures=true`）不中止**（L3 警告，步骤5 继续复检）
## 决策锚点（步骤4 完成后写）

步骤4 编码+测试验证后，若 `metadata.anchors_enabled != false`，刷新 `.decision_anchors.json`：追加 `deviations`（同步 `code_deviations`）+ 刷新 `open_risks`。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| context7 | 🟢* | 实时查库 API（防训练知识过时）--涉及第三方库时 |
| vision-bridge | 🟢* | 涉及 UI 实现时截图参照--用户给图时 |
| **cheap-research** | 🟢* | **降本**：apply_migration（schema 迁移 ops 生成不执行，主会话审核后手动执行）。不接管决策：关键设计/编码实施/Code Review Fix 走主会话 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
