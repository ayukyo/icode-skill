# GREEN：嵌入式性能与稳定性

## 结果

**PASS**。加载技能后，代理先绑定设备、运行制品、配置、功耗/温度、频率策略、workload 和 comparator baseline，再把 FPS、延迟、drop、CPU、memory、thermal、I/O、startup 与 recovery 转为带单位、operator、threshold、layer、consumer 和 scenario 的预算。

测量计划明确区分 warmup 与样本窗口，保留 raw samples，并报告 sample count、min/max、mean、median、p95/p99；资源指标与 workload、fault、queue、drop 和 latency 使用同一时间线。十分钟短测只被判为局部结果，24 小时 soak 仍为 `unobserved`。

传感器断连被标记为需要显式授权；在没有授权时只给安全计划并判 `blocked`。恢复是否通过同时检查恢复期限、恢复后预算和 residual drift，重新有输出不再等同恢复成功。最终稳定生成 `performance_budget`、`measurement_matrix`、`resource_timeline`、`soak_faults` 和受证据约束的 `verdict`。
