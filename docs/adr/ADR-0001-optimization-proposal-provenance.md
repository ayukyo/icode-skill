# ADR-0001：外部优化提案的历史来源登记（ADR）

- 状态：已接受（2026-09-07 评审随 `ICODE_TWO_WEEK_USAGE_OPTIMIZATION_PROPOSAL.md` 全量优化落地）
- 相关：`ICODE_TWO_WEEK_USAGE_OPTIMIZATION_PROPOSAL.md` §6.11 文档漂移治理

## 背景

仓库内存在对以下**外部历史输入文档**的引用，但这些文档文件并未随仓库发布（属当时的外部审计/优化提案，非运行合同）：

- `WORKFLOW_OPTIMIZATION_PROPOSAL.md`（workflow gate 实施来源）
- `ICODE_SEQUENTIAL_THINKING_OPTIMIZATION.md`（reasoning gate 分级说明）
- `ICODE_CHEAP_RESEARCH_EXECUTION_GATE_OPTIMIZATION.md`（cheap-research 执行门）

悬空引用会让读者（含 AI 与测试）以为文件应存在，造成断链。

## 决策

按「设计提案若是历史来源 → 改为可追踪 ADR；若是运行合同 → 随仓库发布」原则：

- 上述三份提案的**规范性内容**（触发条件/阻断步骤/必填单元/阈值常量/分级判定）已固化为**机器真源**，运行时应只读真源，不再引用外部提案文件：
  - workflow gate → `mcp/workflow-gate/gates.json` + `tools/lint_workflow_contract.py`
  - reasoning gate → `mcp/reasoning-gate/gates.json` + `tools/lint_thinking_gate.py`
  - cheap-research gate → `mcp/cheap-research/gates.json` + `tools/lint_mcp_coverage.py`
- 文档中对这三份提案的引用一律改写为真源路径（本 ADR 即历史出处登记，不再全文保留提案）。
- 本优化提案 `ICODE_TWO_WEEK_USAGE_OPTIMIZATION_PROPOSAL.md` 是**随仓发布的运行合同**，引用保留。

## 后果

- 全仓无对缺失 `.md` 的悬空引用（以 20 轮自检的断链扫描为准）。
- 后续新机制验收：若涉及外部输入文档，一律按本 ADR 模式登记出处 + 指向机器真源。
