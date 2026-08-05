# 步骤 4 — 严格落地实施编码

**命令**: `/icode code`
**产出**: 代码文件
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 前置产物缺失（`03_plan_final.md` 不存在） | 报错退出，提示先跑 `/icode merge` |
| **L2·关键** | Code Review Fix 4 维度复检**全部失败**（4 个维度都标 ❌） | 警告 + 记入 metadata + 流程继续（不阻断；user 可事后回代码修复/重设计） |

**L3·重要**（矩阵段定义）：编译失败（3 次仍失败）→ 设 `code_compile_failed=true`，步骤 5 入口警告，**流程继续**。测试失败（3 次仍失败）→ 设 `test_failures=true`，步骤 5 入口警告，**流程继续**（与编译失败同级 L3）。

## 前置校验

> **读决策锚点**（v2.8，启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取上游关键决策摘要（requirement_digest/key_decisions/design_4dims/deviations/open_risks）作本步骤上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 是否存在，不存在则报错并提示先执行 `/icode merge`。

## 前置：patch 配合（v2.13）

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

### 编码实施

严格按定稿计划实施编码。

**符号定位（serena 优先，v2.2 执行步骤内嵌）**：编码前对计划 §5 声明的待改符号，若工程有可索引源码且 serena 可用：ToolSearch 取 `mcp__serena__find_symbol` + `mcp__serena__find_referencing_symbols` schema -> `find_symbol` 定位待改符号 -> `find_referencing_symbols` 找所有调用点（按符号语义非文本匹配，比 grep 精准）-> 结果作为下方「准入三链预扫」的语义增强。目标代码在子仓库/嵌套 git 仓库时（`git -C <dir> rev-parse --show-toplevel` ≠ 当前激活项目根），先 `serena-doctor init/fix` 子仓库根 + `activate_project(<子仓库根>)` 激活目标仓库再查（v2.12，详见 anti_laziness 第 21 条 v2.12 段）；激活失败才降级。serena 不可用/无 LSP -> 降级下方 grep 三链预扫，降级说明只进思考块，不写入产物文件。**未经实际调用 serena 就标降级 = 反偷懒第 21 条违规。**步骤末尾按反偷懒第 21 条 v2.6 自检门输出 `serena 调用: <工具 x N>` 或 `serena 降级: <原因>`，无记录 = 违规****。

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
   - **（v2.4 增补）跨层翻译纯函数化 + 测试覆盖度**：跨层翻译逻辑（外部模块枚举值映射到本模块契约）必须提取为 `constexpr noexcept` 纯函数，**禁止内联在消费点**——便于独立单测且未来新增消费点直接复用；测试必须做到 ① **枚举穷举覆盖**（每个枚举值都有独立断言）、② **条件组合覆盖**（多条件与/或的矩阵）、③ **语义级断言**（每条断言带人类可读标签如 "external NOT_INIT does not terminate"）

用 Write 工具创建/修改每个代码文件。


**修复方案三档实施（v2.7，反偷懒第 26 条）**：默认只实施 A 档（根因修复）；B 档需 metadata `confirmed_B_fixes: [...]` 记录用户显式确认才实施；C 档不实施（范围外）。A 档跨工程（plan 标注）时本工程无可实施 A 档，不强行造 A 档、不把 B 当 A 实施，只做确认的 B 档 + commit/工单注明"A 档转交 <X>"。Code Review Fix 复检核对：实施范围 = A 档 + 确认的 B 档（**先 Read metadata.fix_tiers 读 plan 分档**，字段缺失则从 `03_plan_final.md` §4.5 文本读），超范围实施 = issue。实施 B 档前必须把用户确认记录写入 `confirmed_B_fixes` 数组。详见 anti_laziness 第 26 条


## 强制操作（完成后必须执行）

1. **编译验证 + 测试验证**：运行项目对应的编译命令（最多尝试 3 次），确保所有文件无错误、无警告；编译通过后自动探测并跑测试套件（借鉴 aider `auto_test` 机制，icode 增加自动探测）
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
       - **维度 2 逻辑+扩大修改**：实施 vs 计划设计的逻辑 5 类（边界/状态/异常/时序/数值）覆盖度 → git diff 最小侵入核对（每行变更回指根因/需求点）→ 优雅度6条（复用/风格/调用链/最小侵入/接口克制/调用路径）→ 三链预扫（如有新增符号）
       - **维度 3 竞态死锁**：实施 vs 计划设计的 10 条清单覆盖度（不涉及的标 N/A）→ 锁/原子/内存序/超时是否按设计落实 → 是否引入新竞态死锁风险
       - **维度 4 日志反映**：V 可观测性是否落实（关键路径日志是否写到位）→ 根因-日志-修复对齐 → 日志级别/风格一致 → 无敏感信息泄露。**（v2.4 增补）双值日志**：若修复涉及状态归一化 / 映射，归一化后的关键路径日志是否同时保留原始值（如 `status={} published_status={}`），防止归一化后丢失上游语义信息导致二次定位困难
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
   - **写入测试字段**（v2.8 新增）：`test_cmd`（探测/配置的测试命令，null 表示无测试套件）、`test_outcome`（`pass`/`fail`/`skipped`）、`test_failures`（3 次重试仍失败置 true）。**测试失败不阻断流程**（与编译失败同级 L3），步骤5/6 入口检测 `test_failures=true` 时输出警告
   - **写入 `code_deviations`**：若有主动偏离（见硬性要求第8条），将偏离记录数组写入 metadata `code_deviations`（每条含 plan_said / actual_done / reason），供步骤6 汇总；无偏离则写空数组 `[]`
   - **写入 `code_review_fix_with_issues`**（v1.x 新增，可选，默认 `false`）：4 维度复检未通过标记。`true` 时步骤 5/6 入口输出警告，audit 终审会看到此标记（**不阻断流程**，仅作可见性提示）
3. 全流程模式：编译通过 + 测试通过（或 `test_cmd=null` 跳过）+ 1.5 复检通过则**立即继续执行步骤5**；编译失败或 1.5 复检失败则中止，提示用户修复。**测试失败（`test_failures=true`）不中止**（L3 警告，步骤5 继续复检）
## 决策锚点（步骤4 完成后写，v2.8）

步骤4 编码+测试验证后，若 `metadata.anchors_enabled != false`，刷新 `.decision_anchors.json`：追加 `deviations`（同步 `code_deviations`）+ 刷新 `open_risks`。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

## MCP 推荐（v2.2 强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| serena | 🟢* | 按符号编辑、重命名引用追踪（game-changer）--有可索引源码时（编码实施内嵌） |
| context7 | 🟢* | 实时查库 API（防训练知识过时）--涉及第三方库时 |
| vision-bridge | 🟢* | 涉及 UI 实现时截图参照--用户给图时 |
| **cheap-research** | 🟢* | **降本**：apply_migration（schema 迁移 ops 生成不执行，主会话审核后手动执行）。不接管决策：关键设计/编码实施/Code Review Fix 走主会话 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束（v2.2）**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
