# RED：分层现场验证边界

## 实际结果

未加载新技能的代理正确拒绝 verified，并区分了工程交付、协议送达、设置预览、消费者、运行视图和物理行为。

## 可复现缺口

输出没有先显式固化任务的 `acceptance_contract`，也没有稳定生成 `verification_matrix`、`negative_evidence`、`gaps`、`verdict` 五段合同。各层缺少 required、environment、device、baseline、scenario、evidence、result 字段，因而无法同时满足“现场任务不越界”和“纯 host 任务不被无条件要求真机”的兼容性。

## RED 判定

**FAIL（验收驱动的分层合同缺失）**。技能要让 verified 来自显式验收层，而不是来自固定口号或中间层成功。
