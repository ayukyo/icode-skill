# 证据与验证默认习惯

本文件是 log / plan / deepcheck / audit / verify 共用真源。各步骤只保留触发点，不复制规则正文。

## 1. 现场事实优先

- 当前源码、历史提交、现场运行包、修复验证包是不同对象；先建立关系，再用代码解释日志。
- 设备用 SN 或物理 MAC 绑定；IP、hostname、虚拟 MAC 仅作检索线索。
- 固定日期、时区、精确时间窗、原始证据和转换过程。过滤结果不能冒充完整日志。
- 多 Git 根逐仓记录 runtime / analysis / verification commit、dirty、manifest pin、build root 和产物身份；父仓 clean 不代表子仓一致。
- 现场事实优先于旧结论和“应该如此”的口述；文档与当前 HEAD 只能提供候选解释，不能自动成为现场事实。

未知根因、旧日志、多仓或设备证据场景按 [skill_routing.md](skill_routing.md) 加载 `bug-investigation-baseline-checklist` 或 `multi-repo-artifact-provenance`。

## 2. 证据分类与主代理复核

- 始终区分 `fact`、`inference`、`refuted`、`unobserved`，每条都写 source 和 boundary。
- 决定性事实或反驳用 `record-claim` 写入控制面；fact/refuted 必须附 evidence。
- 子代理和便宜模型可找候选、压缩或质疑，但主代理必须重新核对决定性证据，才能发布根因、修复充分性或 verified 结论。
- 冲突证据不取平均：回到原始来源、身份、时间窗和运行版本，保留尚未解决的竞争解释。

## 3. 无日志反查上游

下游无日志先标 `unobserved`，从最后正证据节点反查上游 cache、相等去重、owner/latch、状态 gate、任务投影、路由注册、异步发送和版本契约。只有上游已证明送达、下游观测点已启用且窗口完整时，才把无日志提升为下游异常证据。

声明、可达、受理、送达、持久化、消费、用户可见和物理行为逐层分开；送达不等于消费，设置预览不等于运行视图，运行视图不等于物理行为。

## 4. 诊断、实现、验证分层

- 诊断：回答“现有证据支持什么根因或断点”，不把候选修复冒充已实现。
- 实现：回答“哪些代码和合同已改变”，不把编译或静态检查冒充现场效果。
- 验证：按用户验收合同逐层记录 environment、baseline、consumer、scenario、evidence 和 outcome。
- 部署/交付/消费/UI/physical 是独立层。App 关闭状态、有效消费者和物理行为只有被明确要求时才成为 required，不给纯 host 任务强加真机门禁。
- `verification_contract.required=true` 时，用 `record-verification --layer --consumer --scenario --baseline` 填满必需矩阵；任一必需单元缺失或最新结果非 pass，不得 `delivery_verdict=verified`。

## 5. 步骤触发

| 步骤 | 必做动作 |
|---|---|
| log | 先固定设备、时间、runtime/analysis 基线和原始证据，再提出根因。 |
| plan | 把证据缺口、完整功能链、状态生命周期和验收层写成合同。 |
| deepcheck | 从消费者和失败路径逆推，检查无日志上游 gate、跨轮残留及未观测边界。 |
| audit | 主代理复核决定性证据，核对 claim ledger 与实际代码/验证记录。 |
| verify | 绑定验证 baseline，逐 layer/consumer/scenario 记录，不自动升级交付结论。 |

## 6. 最小对外表达

结论同时给出：已证事实、推断、未观测边界、运行/分析/验证版本关系、允许结论、禁止越界表述、下一项最小判别动作。不要用“已修复”覆盖“已实现未验证”，也不要用局部 subset success 覆盖完整现场验收。
