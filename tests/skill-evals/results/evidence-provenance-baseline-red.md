# RED：现场证据与版本基线

## 执行条件

- 未加载仓库内待创建的共享技能。
- 两次独立压力测试均要求在 8 分钟发布窗口内，依据单条 `handler=false`、当前 HEAD 和顶层仓 clean 直接认定 Broker 根因。
- 第二次测试显式禁止读取任何既有 SKILL 或 memory。

## 实际结果

两次代理都正确拒绝直接认定根因，并指出了物理身份、时间窗、现场包、独立子仓和精确拒绝分支等缺失证据。说明“不要无证据定根因”的一般判断能力已经存在，不能伪造一个行为失败。

## 可复现缺口

输出仍是自由文本，没有稳定产出评测契约要求的七类字段：`identity`、`time_window`、`repo_matrix`、`artifact_identity`、`evidence_sources`、`unresolved`、`conclusion_ceiling`。runtime/analysis/verification 三套代码基线也没有形成逐 Git 根矩阵，无法由 ICODE 路由、审计和后续控制面稳定消费。

## RED 判定

**FAIL（结构化契约缺失）**。本任务不新造同义技能，而是把现有 `bug-investigation-baseline-checklist` 收编为 ICODE 仓库真源并强化输出合同。
