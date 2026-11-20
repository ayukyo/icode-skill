# lite 需求与方案收敛

先调查原始输入、入口、状态或数据流、实际实现、已有测试和工程约束。能从现有材料确定的，不重复询问。不能仅凭名称设计修复；bug 尽量复现，多个根因候选先取证排除。

规划按任务需要展开，不设字数、文件数或问题数上限。至少明确：原始目标、当前事实、范围、不变项、业务决策、具体修改点与异常路径、可观察 AC、验证方法、实际风险和回退。

合同保存在既有 metadata：

- scope_contract.requirements：稳定 R ID、text、source_ref。初始来源为 metadata.requirement；增量来源为 requirement_deltas.N。
- scope_contract.current_facts/in_scope/invariants：调查证据、实施边界、需保留行为。
- scope_contract.plan：approach、change_points、risk_checks、rollback。
- semantic_decisions：保留原 status/selected，增加 resolution={kind:user|existing_constraint|technical,source_ref,reason}。用户选择回指原始输入或有效增量；技术选择说明理由，不能冒充用户要求。
- acceptance_contract.criteria：稳定 AC ID、requirement_ids、expected；每个需求都有可观察验收。
- verification_contract：required=true、required_layers/required_consumers/required_scenarios、required_cells。每个单元含 layer/consumer/scenario/criterion_ids；只有真实外部条件缺失才标 external=true 与具体 pending_reason。

关键业务选择未决时继续调查或向用户给出推荐与影响，不因达到提问次数而降成假设。字段齐全不等于语义正确，主代理必须核对每个原始目标；独立审核再从原文复核。

通过 metadata-update 写合同；原始 requirement 与 mode/version 不可覆盖。需求增量只能 append requirement_deltas，保留 source_ref。追加需求、修改方案或验收前，先终结开放执行并受控回退 lite_prepare_in_progress，撤销下游完成及旧交付投影；精确历史请求可幂等回放，但不授权新语义写入。不能改验收来让已有实现通过。

仅规划请求写 scope_contract.stop_after=prepare，允许形成规划回执并停在 lite_prepare_done；控制面阻止自动进入实现。
