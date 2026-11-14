# 命令 fast — 精简全流程

**命令**：`/icode fast <需求>`。主会话依次完成 plan → review → merge → code → deepcheck → audit。
**目标**：保留六阶段及固定产物，减少重复阅读和长文改写。实际耗时改善须由基准测量，不预设百分比。

## 必要执行入口

1. 按 [目录与 metadata 共享规则](../references/dir_and_metadata.md) 决定复用/新建和出生事务。复用入口态有歧义时依原合同询问；不重置既有 full。
2. 创建时记录 `mode="fast"`、`max_rounds=1`、`risk_profile={policy_version:2,requested_mode:"fast",effective_mode:"fast",override:false}`，其余 schema/trace 版本按共享模板。缺分类只表示尚待调查，不能先认定 full；出生后先执行 plan。
3. plan 调查现有实现、调用链与验收边界，冻结现有 `scope_contract`，再补齐全部 catalog 风险事实与 classification，调用 `tools/fast_policy.py` 的 `assess_fast_risk(metadata, catalog)`。
4. 进入 review/merge/code/patch 前必须有效分类；控制面实读分类引用文件并核验路径/hash，linter 校验结构和分流。未决语义 `blocked`，未解决重大增量 `replan`，依结果解决决策或回计划，不能把改用 full 当作用户已决定。
5. `route=full` 时经控制面记录 `risk_profile.effective_mode="full"` 后走完整流程；`route=fast` 且 policy2 来源校验有效时才使用下述对应阶段合同。每步仍依原执行模型 start/check/artifact/finish/transition，不合并阶段，不伪造跳过回执。步骤开始返回的 `execution_profile` 只是只读指令视图，不是准入或已读证明；不支持、legacy、full 或可选视图失效时读取原主步骤合同。

机器真源为 [workflow catalog](../mcp/workflow-gate/gates.json) 的 `fast_risk_upgrade`，纯评估接口只返回 `route/reasons/required_checks`，不写 metadata，不证明证据已真实执行。词表、必需检查映射与准入步骤只从 catalog 读，不从本文件另造常量。

## 风险分类与有效模式

- **旧策略**：无 `risk_profile.policy_version` 或版本 1，继续原九风险 flag 任一命中升级 full / 显式 override 的语义，不静默重解释旧工单。
- **策略 2**：少量跨文件、已知缓存/观察者/注册表、真实环境验证等普通风险，只增加 catalog 指定专项检查；范围已界定且证据有效时可保持 fast。专项检查未完成必须保留阻断或验证债务，不能因 fast 豁免。
- **必须 full**：新架构、影响不可界定、破坏接口、不可逆迁移、独立对抗需求，按 catalog 稳定 flag 分流。用户要求 full 或本运行已有效 full 时不自动降级。
- **硬门保持**：override 只记录显式风险接受，不豁免未决语义、重大增量、身份影响、真实环境证据、原始证据与阶段确认。需要真实设备时必须绑定设备/环境/制品，缺证据不得 verified。
- **未知不等于 full**：未知版本、错误类型、未调查或缺少可核验证据的 bounded 声明返回 blocked；plan 可继续调查，编码前必须得到有效评估。

`mode` / `requested_mode` 记录用户请求；所有行为、coverage、UI 与 MCP gate 使用同一 `effective_mode(metadata)` 解析结果。非法值显示 `unknown` 并由评估阻断，不能用显示解析代替准入。显式 `/icode review N` 升级时先登记 full，再按 N 轮与对抗执行；升级后 deepcheck 同样走 full。

classification 结构及 canonical scope 摘要、引用根与校验边界见 [risk_profile 字段族](../references/dir_and_metadata.md)。证据建议引用 ticket 内已冻结 `01_plan.md` 或 `03_plan_final.md`；workspace 引用用于确有必要的调查材料，正常源码变化导致引用失效时须真实重新核验，不能改 hash 冒充已读。

## 六阶段必要动作与产物

| 阶段 | 必要动作 | 固定产物 |
|---|---|---|
| [plan](#fast-plan) | 短合同；调查后分类；需求、范围红线、调用链、实现点、验收、风险和适用 TDD | `01_plan.md` |
| [review](#fast-review) | 一轮独立视角与七维；首轮真实 JSON/manifest | `02_review.md` |
| [merge](#fast-merge) | 机械复制已批准完整计划，叠加采纳差异与偏差备忘 | `03_plan_final.md` |
| [code](#fast-code) | 实施、RED→GREEN/回归、四维 Code Review Fix | `04_code_review_fix.md` |
| [deepcheck](#fast-deepcheck) | 独立 Reverse Read、调用链、专项、coverage | `05_deepcheck.md` |
| [audit](#fast-audit) | 独立 Audit Read、追溯矩阵、七维与新裁决 | `06_audit.md` |

首轮 `review_round_1.json` / `review_manifest.json`、code/deepcheck/audit worklist 与 coverage 回执继续依 [审查证据合同](../references/inspection_evidence.md)。不移动、删除这六份固定产物，不用摘要替代完整最终计划。fast review 一轮无对抗；有效 full 恢复原多轮与对抗。fast deepcheck 仅 Reverse；有效 full 恢复 Reverse/Fixed/Free。

<!-- ICODE:FAST:plan:BEGIN -->
<a id="fast-plan"></a>
## plan 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，按 execution_model 启动本阶段 attempt，保留 required_checks、真实输出与 finish/transition 回执。

- **FAST-PLAN-SCOPE**：解析实际工程根与需求来源，实读 init/log 输入、现有实现及 caller/import/test 调用链，检查已有等价功能后确定复用或修改。设计/思考前完成 LIMIT 索引与命中红线实读，在既有 `limit_checkpoint.md` 追加「阶段块：plan前置硬基线」四节（无 LIMIT 也如实记录），引用编号回填 `limit_refs`。保留目录复用、历史检索与 `_inject_cache.json` 留痕、工程结构快照及版本迁移合同。
- **FAST-PLAN-DESIGN**：`01_plan.md` 自包含保留 10 个编号章节：概述、功能需求、架构设计、ADR、详细设计、异常处理、实现步骤、校验项、风险评估、需求质量清单；每节仅写本任务事实。§4.5 保留修复方案与四维设计（根因闭环、逻辑/扩大修改、竞态死锁、日志）及适用 TDD 测试合同：目标行为、命令、生产修改前 RED 预期、GREEN 标准、回归范围。A/B/C 分档、范围红线、现有调用链、实施点、边界异常、验收与风险不能省略；B 须确认、C 不实施。接口误用、端到端路径、状态机与前提假设命中时按原对应段完成推演。
- **FAST-PLAN-GATES**：调查后冻结 `scope_contract`，补全部 catalog 风险事实、classification、范围摘要和真实路径/hash 引用，调用 `assess_fast_risk(metadata,catalog)`。未调查可继续 plan，review/merge/code 前必须有效分类；blocked 解决语义，replan 回计划，full 登记升级并执行完整流程。同步 semantic_decisions、身份 impact_contract 与生命周期 acceptance_contract；不替用户决定未决策略。完成十角度需求质量清单和具体代码断言验证（接口、路径、调用模式、等价实现、首次激活双侧校验），修正失败断言影响的计划。按原 L1/L2 边界运行 limit_refs 自检及 `lint_workflow_contract.py --step plan`，记录真实决策锚点、产物/索引，成功 finish 后合法 transition 到 plan_done。
<!-- ICODE:FAST:plan:END -->

<!-- ICODE:FAST:review:BEGIN -->
<a id="fast-review"></a>
## review 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，分类有效后经控制面进入 review_in_progress，再启动真实 attempt；required_checks、finish/transition 按 execution_model 执行。恢复依据进入前真实状态/attempt，返回重审按 control_plane 的受控路由，禁止先审后补进入事件。

- **FAST-REVIEW-INDEPENDENT**：先只依据原始需求形成独立简要方案，不读取 `01_plan.md`；再实际 Read `01_plan.md` 对比遗漏/偏差/多余，通读关联源码与接口承诺，检索所有调用方和未被计划提及的消费者/等价实现。记录真实通读路径及发现，优先实证未验证断言，审查 TDD 是否验证外部行为、RED 是目标断言失败、首跑通过会停止复核、正常/边界/异常/复现场景齐备。
- **FAST-REVIEW-SEVEN**：一轮逐项覆盖七维：逻辑合理性、流程完整性、场景覆盖度、风险遗漏、落地可行性、现有实现对照、必要性；核对 §4.5 四维设计与 A/B/C 分档、范围升级及 catalog 专项。身份变化独立搜索并核对九维影响与遗漏消费者，生命周期/跨模块枚举/真实环境等命中条件不得省略。发现写稳定 ID、位置、证据、建议、否决风险及处置；不能证实的保留 pending，clean 结论说明真实范围，不复述计划。
- **FAST-REVIEW-RECEIPTS**：实际一轮无对抗，首轮 clean 也必须保存真实 `review_round_1.json`、`review_manifest.json`、三类计数、origin attempt、round digest 与 artifact 回执；Dedup/cheap-research 按适用条件及 trace 合同执行。不得事后伪造 review 已完成。显式 `/icode review N` 先登记 effective full，恢复原多轮、自动延长与独立对抗，后续 deepcheck 同步 full。完成本轮证据后 finish，再合法 transition 到 review_done。
<!-- ICODE:FAST:review:END -->

<!-- ICODE:FAST:merge:BEGIN -->
<a id="fast-merge"></a>
## merge 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，实读 `01_plan.md`、`02_review.md` 与首轮 JSON/manifest，执行 `check-outputs --step review`；缺真实回执回 review，不在本阶段补造。按 execution_model 冻结输入、执行 required_checks 与 finish/transition。

- **FAST-MERGE-COMPLETE**：审查无语义变化时机械复制已批准 `01_plan.md` **全文**到自包含 `03_plan_final.md`，保留 10 章、§4.5 与 TDD 测试合同（目标行为/命令/RED/GREEN/回归）。逐项处理 confirmed、file_review.key_findings、refuted 和 pending：采纳/部分采纳/否决给标记与理由，pending 补证据或作为待验证假设保留，不能默认采纳或丢弃。只修改采纳差异并预留「实现偏差备忘」；不得用摘要替代完整最终计划。review 若改变行为，同步更新测试断言与 metadata.tdd，再冻结。
- **FAST-MERGE-GATES**：核对 scope_escalations 与 requirement_deltas，未确认 B_confirm 不纳入实施，重大增量回 plan；范围改变同步 scope_contract.summary、分类和影响/验收合同。运行原定稿章节/备忘机器硬校验及 `lint_workflow_contract.py --step plan`、`--step code`，失败不得编码。merge 沿用 catalog L1，出现新冲突/语义/复杂修改升 L2；单轮跨轮汇总按真实 skipped_not_eligible trace，不生成无必要摘要。登记定稿真实 artifact，finish 成功后 transition 到 plan_finalized。
<!-- ICODE:FAST:merge:END -->

<!-- ICODE:FAST:code:BEGIN -->
<a id="fast-code"></a>
## code 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，核验有效分类和引用；blocked/replan 不得编码。实读最终计划、已有 patch 和当前源码，保存真实 git 三态/活动 checkout 基线，执行拓扑及 required_checks，按 execution_model start/check/artifact/finish/transition。

- **FAST-CODE-TDD**：行为变更依冻结契约取得真实 RED→GREEN 再回归。生产 Edit 前仅改测试/最小设施、记录 production_hashes，运行最窄目标命令；只有 `expected_assertion` 是有效 RED，编译/导入/环境错误、超时或 flaky 不能冒充。首跑通过停止生产修改并复核必要性。最小生产修改后以相同命令/行为断言 GREEN，不删减/放宽测试；保留 RED/GREEN/regression 原始证据与 metadata.tdd。not_assessed 回计划补合同；exempt 须真实无行为变化理由，device_split/blocked 或用户自担验证保留 verification_pending，fast 不构成豁免。
- **FAST-CODE-IMPLEMENT**：Edit 前逐符号实查 caller/import/test 三链并给 file:line，零命中先扩大调查并如实标记。只实施定稿 A 档与已确认 B/escalation，保护 patch/用户改动，跨文件接口同步所有调用方，沿用工程模式、注释与日志，最小侵入。偏差经 metadata-update 写稳定 code_deviations ID，报告引用同 ID；语义/重大范围变化按原路由重新定稿，不以偏差备忘代替授权。按 LIMIT/README/静态入口确定构建与测试，保留实际退出码、用例范围和来源；零用例/全部跳过为 inconclusive，有失败为 fail，不能只凭退出码0判 pass。验证复用须满足原候选、环境和制品合同。
- **FAST-CODE-REVIEW-FIX**：完整登记 code_files，prepare `code_worklist.json`，本阶段实际 Read 联审文件后优先 `--paths-json` 批量登记 `code_review`（见共享批量规则）。所有工单执行 Code Review Fix 四维：根因闭环（适用 log 的 H/P/V）、逻辑与扩大修改（边界/状态/异常/时序/数值及调用模式）、竞态死锁（锁/原子/内存序/超时）、日志反映（可观测性、原始/归一化双值和敏感信息）。每维逐项给 file:line 与结果；缺设计基线回 plan，失败保持 code_in_progress、不追加步骤4，轻度回代码、重度建议回计划。专项 assessment 独立填全格、理由和真实引用；prepare/read 不等于 pass，源码变更 blocked 重入重审。`04_code_review_fix.md` 仅写改动、四维、测试、偏差与债务，登记 worklist/报告 artifact；真实成功 finish 后 transition 到 code_done，编译或复检失败中止自动串联，测试 fail/inconclusive 继续复检但不升级已验证。
<!-- ICODE:FAST:code:END -->

<!-- ICODE:FAST:deepcheck:BEGIN -->
<a id="fast-deepcheck"></a>
## deepcheck 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，校验当前活动 checkout/拓扑、最终计划、代码及已有 patch，独立执行 required_checks；依进入前真实 attempt/trace 恢复，不能把本次初始化状态当可跳过依据。按 execution_model start/check/artifact/finish/transition。

- **FAST-REVERSE-INDEPENDENT**：prepare `deepcheck_worklist.json`。先独立实际 Read 当前全部关联实现/接口/调用方/测试（删除文件读真实基线），输出已 Read 确认和至少3个具体逆推方向；只依据代码逆推导出接口、类型、分支/边界/异常、数据流/调用链与 unclear，不让计划代替逆推。再实读 `03_plan_final.md`、patch 和 `04_code_review_fix.md` 对比欠实现/偏离，另查工程调用模式、等价实现、首次激活双侧校验及 caller/import/test 三链。已有 patch 纳入计划侧、有补丁不跳过 Reverse；不复用 code/review 的 Read 或结论。
- **FAST-REVERSE-EVIDENCE**：实际读完本 attempt/phase 的一组后优先 `--paths-json` 登记 `reverse`；触发 timestamp、共享变体/消费者等专项时独立完成各 required_items 的 assessment 与真实引用，不从 code 复制裁决。核验 TDD 同命令/断言、修复前生产哈希、合法 RED、GREEN 未放宽与根因覆盖，核对原四维复检未闭环和生命周期矩阵。发现按根因/偏差/范围分流，计划内修复后来源漂移须 blocked 重入、新基线重新审查；未决语义/重大增量回原路由。
- **FAST-REVERSE-COVERAGE**：写 `05_deepcheck.md` 与 `deepcheck_coverage.json`，完整覆盖 code_files 和本阶段真实 hash，登记 coverage/worklist/report artifact。缺 Read、专项、过期 hash 或未观察项不能 success；预算不足写 partial/degraded、unobserved、debt_reason，用 degraded finish 并保留债务。有效 fast 只 Reverse，`deepcheck.fixed_scan` 与 `deepcheck.dedup` 各如实记 skipped_stage_not_reached（有效 mode=fast/phase=reverse），不伪造 Fixed/Free/Dedup；有效 full 恢复原 Reverse/Fixed/Free 与 Dedup/对抗。当前合同完成后 finish，再合法 transition 到 deepcheck_done；Audit 仍需独立 Read。
<!-- ICODE:FAST:deepcheck:END -->

<!-- ICODE:FAST:audit:BEGIN -->
<a id="fast-audit"></a>
## audit 必要合同

先履行[共享前置、硬门与条件路由](#fast-shared-contract)，重新校验当前源码/拓扑、计划及 patch、上游证据和 required_checks；按 execution_model start/check/artifact/finish/transition。audit 是本阶段新裁决，不能以 deepcheck 已 clean 代替终审。

- **FAST-AUDIT-INDEPENDENT**：prepare `audit_worklist.json`，本轮独立实际 Read 全部关联实现/接口/调用方/测试及原始需求、最终计划与 patch，输出真实已读确认和至少3个具体审计角度。每组真实 Read 后优先 `--paths-json` 登记 `audit`，按本阶段独立填写专项 assessment，不复用 Reverse/code 阅读或裁决。先建立计划/原始需求→代码 file:line→测试/约束的追溯矩阵，再审七维：实施完整度、执行精准度、方案偏离度、代码质量、跨文件一致性、残留风险、原始需求收敛（需求/红线/必要性三个视角，搜索等价实现）；代码质量含注释/日志、工程模式及事务提交与展示增强失败分离。每维保留独立评分、file:line 和至少2句实质理由，形成总体评分、问题/严重度、通过/有条件通过/不通过的新裁决，记录更深独立检查角度。按 MCP 真源履行 audit.repo_facts 候选预审、audit.plan_diff 和全工单 `lint_mcp_coverage.py`；摘要/候选不能直接作事实，必须实证复核。
- **FAST-AUDIT-CLOSE**：逐项处理发现，计划内实现缺陷按原合同强制修复/复核；范围外新问题先 escalation/delta 分流、B_confirm 未确认不实施，重大增量回计划，不以终审采纳代替授权。修复改源码则旧 attempt blocked，重入并独立重读重审，必要回归不省略。核对 TDD 因果、生命周期矩阵、真实环境/制品与原始证据、阶段接受、测试及报告冲突，保留 failed/skipped/inconclusive/pending 的结论上限。code_deviations 同 ID 回写最终计划「实现偏差备忘」，保留原正文；风险命中时给部署后验证建议、环境能力/现场登记对账及 worktree 回流提示，不自动提交推送。
- **FAST-AUDIT-RECEIPTS**：保存 `06_audit.md`、独立 `audit_coverage.json` 和 worklist，报告开篇简述改动、实际验证、交付结论、待验项和下一步，后附评分与证据。逐成员当前 hash、全阶段 Read、专项与 artifact 回执不可缺；预算/未观察债务用 partial/degraded 与 debt_reason、degraded finish，不称全部通过。完成原自检、`check-outputs --step audit` 及 `lint_workflow_contract.py --step audit-verified` 验收门，按真实证据确定 `delivery_verdict`；缺有效适用 RED、生命周期矩阵或真实环境证据不得 verified。finish 后才合法 transition 到 completed 并显式携带交付结论，刷新既有索引/概览/决策锚点，不新增性能或步骤工单文件。
<!-- ICODE:FAST:audit:END -->

<a id="fast-shared-contract"></a>
## 共享规则与条件加载

六段 compact 合同与本节及命中引用共同构成 fast 必要合同；稳定 FAST-* ID 表示职责，不代表已完成或 passed。首次必须实际加载本节及共享真源，后续仅按 thinking_core 复用仍完整保留的原文。步骤返回的 source_digest/source_refs 只是版本绑定，不能据此声称已读正文。完整 full/start 保留原多轮、对抗及 Reverse/Fixed/Free 强度；已有单步/不支持 compact 的入口仍可实读原步骤执行。

通用前置不能跳过：解析真实 ticket/活动实现根与现有状态，拒绝 debug/关闭冻结/身份或路径错误；legacy 按控制面只读/迁移合同处理。受控 metadata、索引、事件、产物和状态只用既有 writer，目录/端口/schema/provider 错误仍 fail-closed，可选 profile 失败不能绕过准入。code/Reverse/Audit 的真实源码 Read、判断、专项 assessment 分阶段独立，静态规则复用不代表源码或上一阶段裁决复用。所有阶段保持原 L1/L2/L3 阻断/降级边界、L2 思考和 MCP trace 义务，输出确认、决策锚点及适用检索缓存仍按各真源。

冻结输入/候选/范围/合同或源码漂移：按 execution_model 终结旧 attempt 为 blocked，按 route 重入并实际重新核验，禁止只改 hash；恢复先查真实 trace/open operations，副作用结果未知先核对，不盲重放。每阶段保留 start→边界 check→真实 artifact→finish→合法 transition；长动作成功/失败都写 operation 终态。缺上步真实产物/完成态停止串联，不直写完成态、不伪造 Read、成功或 skipped。

**共享批量登记（同样适用 full/start）**：code_review、reverse、fixed、free、audit 各独立实际 Read 后，优先用现有 inspection `--paths-json` 按当前 attempt/phase 登记；每组1至256成员、最多64KiB，超限分组只合并登记，不合并语义审查。旧 `--path` 兼容。全清单漂移、逐成员精确字节 hash、合法路径、幂等身份及晚期 provider/阶段拒绝保持原检查；一批成功不等于完整覆盖。完整命令、assessment 与失败/恢复边界见[审查清单](../references/inspection_worklist.md#正式工单接线)。

机械复制完整计划、派生矩阵/coverage 结构或规则 hash 不自动赋予 passed、Read 或 verified。既有测试/构建只在原验证 API 接受当前候选、命令/范围、配置、环境和制品适用性时引用，保留 run/provenance；后续 fail/inconclusive、源码或环境变化使旧证据失效。不为换阶段机械重跑不变的有效测试，也不能以“上步绿”跳过本阶段判断。

| 条件 | 首次必读/失效重载真源 |
|---|---|
| 所有任务 | [目录/出生/metadata](../references/dir_and_metadata.md)、[工程接入](../references/project_intake.md)、[思考与共享规则复用](../references/thinking_core.md)、[反偷懒](../references/anti_laziness.md)、[执行模型](../references/execution_model.md)、[控制面](../references/control_plane.md)、[技能路由](../references/skill_routing.md)；原步骤 L1/L2 声明适用，compact 不降低等级 |
| 未决语义/需求增量/范围升级 | [workflow catalog](../mcp/workflow-gate/gates.json)及[metadata 合同](../references/dir_and_metadata.md)的 semantic_decisions、requirement_deltas、scope_escalations；不明条件先调查，不能默认为 N/A |
| 身份变化/生命周期/真实环境风险 | impact_contract 九维、acceptance_contract 四阶段×四消费者与 catalog required_checks；[证据分层](../references/evidence_and_verification.md)及原步骤[计划合同](01_plan.md#plan-gates)、[生命周期复检](05_deepcheck.md#deepcheck-lifecycle)、[终审验收](06_audit.md#audit-completion)；真实设备绑定身份、环境、加载制品，未完成保留债务，override 不豁免 |
| 严格原始证据/视觉目标/阶段计划 | [串口、证据与阶段验收真源](../references/serial_evidence_stages.md)，保留实际多帧查看和可信阶段确认 |
| review/Code Review Fix/Reverse/Audit | [审查清单](../references/inspection_worklist.md)、[审查证据](../references/inspection_evidence.md)，保留首轮 JSON/manifest、worklist、coverage、origin attempt 与真实 artifact；未观察/缺读/专项未完保留债务，不以编译代替 |
| worktree/跨仓/已有 patch | [worktree 隔离与拓扑](../references/worktree_isolation.md)、各原步骤「前置：patch 配合」及 code「worktree 工单业务子仓隔离」；候选、构建与证据来自活动 checkout，保护已有用户/patch 修改 |
| 公共库/API/ABI/外部 SDK、枚举或第三方库 | [证据分层](../references/evidence_and_verification.md)、[技能路由](../references/skill_routing.md)及原[计划接口误用](01_plan.md#plan-interface-review)、[四维设计](01_plan.md#plan-design)、[编码实施](04_code.md#code-implementation)；调用方/消费者、声明与运行时版本、异常与兼容必须闭环 |
| 串口、视觉、硬件、邮件/技术文档、首次激活、日志或并发 | [技能路由](../references/skill_routing.md)、[媒体路由](../references/media_routing.md)、[首次激活](../references/first_activation_path.md)与原步骤命中条件段；真实性、时序/状态/错误、来源和必要现场验证完整执行，不因 compact 或静态通过免除 |

共享规则首次完整 Read，后续按 thinking_core 的有效复用机制引用已有来源，不逐步重复粘贴。规则版本、需求、候选、调用链或验证合同变化时重载。源码实际 Read 和 review/Reverse/Audit 的独立判断不可缓存冒充。

## 思考与 MCP 边界

等级由 [reasoning catalog](../mcp/reasoning-gate/gates.json) 决定，不能因 fast 全部降 L1。新语义、复杂修改、根因分析和重构仍执行 sequential-thinking 至少 3 步；review/Reverse/Audit 各自保留独立判断。当前只复用 merge 既有 L1；尚无足够机器事实证明其他步骤可降级，维持 L2。

机械复制和派生矩阵可由脚本完成，但不等于语义审查完成。cheap-research 的条件与 trace 沿用 [MCP 每步规则](../references/mcp_per_step.md)，不重复调用已经履行且输入未变的 gate。未进入 Fixed/Dedup 时仍写 `skipped_stage_not_reached`，证据为有效 `mode=fast/phase=reverse`；升级 full 不得借用户请求 fast 跳阶段。
