# GREEN：硬件规格契约审计

## 执行条件

- 加载 `hardware-spec-contract-audit` 后复演四个压力场景。
- 所有文档先经过可信接入并保留 hash 与精确位置。

## 结果

**PASS**。输出稳定拆为 `source_register`、`normalized_fact_matrix`、`conflict_register`、`traceability_matrix` 和 `verdict`。每条事实绑定 source role、product/variant、revision/date、lifecycle_status、raw/normalized value、unit、modality 和 location；差异被区分为真实矛盾、作用域/变体差异、生命周期漂移、单位/舍入或未决。任何来源都不能凭文档名获得全局权威，缺少测试或现场证据时追溯链保持 gap。
