# RED：嵌入式性能与稳定性

## 实际结果

未加载新技能的代理能建议观察 FPS、CPU、内存和温度，也知道延长运行时间，但主要输出仍是通用测试清单。

## 可复现缺口

- 没有先固定 target hardware、实际加载制品、频率策略、功耗/温度条件和 workload baseline。
- 把平均 FPS 或短时间“持续出图”当成性能依据，缺少 warmup、样本量、统计窗口、p95/p99、drop 和 raw evidence。
- CPU、memory、thermal、I/O、queue、latency 分散观察，没有可对齐的 `resource_timeline`，无法证明退化关联。
- 十分钟无崩溃被宽松推断为长稳，缺少合同化 soak duration/count、早晚窗口漂移、故障时刻、恢复期限和恢复后预算复测。
- 输出不稳定，未强制形成 `performance_budget`、`measurement_matrix`、`resource_timeline`、`soak_faults`、`verdict`。

## RED 判定

**FAIL（可量化性能与稳定性证据合同缺失）**。现有通用能力无法防止“短测、平均值、仍有输出”被提升为 target-hardware 性能或 soak 通过。
