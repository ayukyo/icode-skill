# GREEN：跨层契约路由

## 结果

**PASS**。代理按合同分别输出 boundary、contract_matrix、classification、skill_routes、gaps、verdict，并明确路由：

- A → `cross-layer-status-enum-collision`，结论 `supported`。
- B → `closed-source-upstream-flag-fake-gate`，现有 wrapper 修复 `refuted`。
- C → `property-field-fullchain-alignment`，算法有效消费 `unresolved`。
- 闭源版本差异保持条件路由，没有无证据触发。

每个 route 都包含 trigger、input、expected output 和 fallback；最终拒绝统一代码修复，同时保留三个 repair owner 与独立验证。
