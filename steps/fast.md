# 命令 fast — 精简全流程

**命令**：`/icode fast <需求>`。主会话依次完成 plan → review → merge → code → deepcheck → audit。
**目标**：保留六阶段及固定产物，减少重复阅读和长文改写。实际耗时改善须由基准测量，不预设百分比。

## 必要执行入口

1. 按 [目录与 metadata 共享规则](../references/dir_and_metadata.md) 决定复用/新建和出生事务。复用入口态有歧义时依原合同询问；不重置既有 full。
2. 创建时记录 `mode="fast"`、`max_rounds=1`、`risk_profile={policy_version:2,requested_mode:"fast",effective_mode:"fast",override:false}`，其余 schema/trace 版本按共享模板。缺分类只表示尚待调查，不能先认定 full；出生后先执行 plan。
3. plan 调查现有实现、调用链与验收边界，冻结现有 `scope_contract`，再补齐全部 catalog 风险事实与 classification，调用 `tools/fast_policy.py` 的 `assess_fast_risk(metadata, catalog)`。
4. 进入 review/merge/code/patch 前必须有效分类；控制面实读分类引用文件并核验路径/hash，linter 校验结构和分流。未决语义 `blocked`，未解决重大增量 `replan`，依结果解决决策或回计划，不能把改用 full 当作用户已决定。
5. `route=full` 时经控制面记录 `risk_profile.effective_mode="full"` 后走完整流程；`route=fast` 才使用各主步骤的「fast 必要执行」小节。每步仍依原执行模型 start/check/artifact/finish/transition，不合并阶段，不伪造跳过回执。

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
| [plan](01_plan.md#fast-必要执行) | 短合同；调查后分类；保留需求、范围红线、调用链、实现点、验收、风险和适用 TDD | `01_plan.md` |
| [review](02_review.md#fast-必要执行) | 一轮独立视角；发现/位置/证据/处置，clean 写简短结论；首轮真实 JSON/manifest | `02_review.md` |
| [merge](03_merge.md#fast-必要执行) | 无语义变化时机械复制已批准完整计划，只叠加采纳差异与偏差备忘 | `03_plan_final.md` |
| [code](04_code.md#fast-必要执行) | 实施、适用 RED→GREEN/回归、Code Review Fix；记录测试及偏差 | `04_code_review_fix.md` |
| [deepcheck](05_deepcheck.md#fast-必要执行) | Reverse 独立实际 Read、调用链、专项检查和差异；只减重复正文 | `05_deepcheck.md` |
| [audit](06_audit.md#fast-必要执行) | 独立 Audit Read、追溯矩阵与新裁决，突出债务和结论 | `06_audit.md` |

首轮 `review_round_1.json` / `review_manifest.json`、code/deepcheck/audit worklist 与 coverage 回执继续依 [审查证据合同](../references/inspection_evidence.md)。不移动、删除这六份固定产物，不用摘要替代完整最终计划。fast review 一轮无对抗；有效 full 恢复原多轮与对抗。fast deepcheck 仅 Reverse；有效 full 恢复 Reverse/Fixed/Free。

## 共享规则与条件加载

本表为导航，规则仍由链接目标维护；各主步骤的必要小节与对应条件段构成同一执行合同。

| 条件 | 首次必读/失效重载真源 |
|---|---|
| 所有任务 | [目录/出生/metadata](../references/dir_and_metadata.md)、[工程接入](../references/project_intake.md)、[思考与共享规则复用](../references/thinking_core.md) |
| 语义选择/身份/生命周期/真实环境风险 | [workflow catalog](../mcp/workflow-gate/gates.json)、[证据分层](../references/evidence_and_verification.md)及该主步骤命中的条件段 |
| 严格原始证据/视觉目标/阶段计划 | [串口、证据与阶段验收真源](../references/serial_evidence_stages.md)，保留实际多帧查看和可信阶段确认 |
| review/Reverse/Audit | [审查清单](../references/inspection_worklist.md)、[审查证据](../references/inspection_evidence.md) |
| 外部库、日志、patch、跨仓或并发等 | 各主步骤对应条件段及其引用；条件命中必须完整履行 |

共享规则首次完整 Read，后续按 thinking_core 的有效复用机制引用已有来源，不逐步重复粘贴。规则版本、需求、候选、调用链或验证合同变化时重载。源码实际 Read 和 review/Reverse/Audit 的独立判断不可缓存冒充。

## 思考与 MCP 边界

等级由 [reasoning catalog](../mcp/reasoning-gate/gates.json) 决定，不能因 fast 全部降 L1。新语义、复杂修改、根因分析和重构仍执行 sequential-thinking 至少 3 步；review/Reverse/Audit 各自保留独立判断。当前只复用 merge 既有 L1；尚无足够机器事实证明其他步骤可降级，维持 L2。

机械复制和派生矩阵可由脚本完成，但不等于语义审查完成。cheap-research 的条件与 trace 沿用 [MCP 每步规则](../references/mcp_per_step.md)，不重复调用已经履行且输入未变的 gate。未进入 Fixed/Dedup 时仍写 `skipped_stage_not_reached`，证据为有效 `mode=fast/phase=reverse`；升级 full 不得借用户请求 fast 跳阶段。
