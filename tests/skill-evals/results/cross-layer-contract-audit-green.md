# GREEN：跨层契约路由

以下是既有三个路由场景的结果记录，保留原文；其中 PASS 不适用于本次新增公共库 API/ABI 案例。

## 结果

**PASS**。代理按合同分别输出 boundary、contract_matrix、classification、skill_routes、gaps、verdict，并明确路由：

- A → `cross-layer-status-enum-collision`，结论 `supported`。
- B → `closed-source-upstream-flag-fake-gate`，现有 wrapper 修复 `refuted`。
- C → `property-field-fullchain-alignment`，算法有效消费 `unresolved`。
- 闭源版本差异保持条件路由，没有无证据触发。

每个 route 都包含 trigger、input、expected output 和 fallback；最终拒绝统一代码修复，同时保留三个 repair owner 与独立验证。

## 公共库扩展：合成预期输出

状态：新增案例仅完成审查规格设计和人工文档核对，未运行 with-skill/baseline 模型评测，未产生模型通过率、性能比较或真实库兼容实测结果。以下是评测时应核对的预期输出。

| 合成案例 | 预期输出和证据边界 |
|---|---|
| `public-signature-wrapper-export` | 从客户可见精确签名出发，区分首选接口、废弃兼容包装和内部实现；将声明、导出、外部编译/链接、运行分别列入 `contract_matrix`。 |
| `external-consumer-link-gap` | 核实安装包头文件和产物身份；记录编译通过与链接失败，指出缺失导出，保留 runtime 未观察并限定可交付结论。 |
| `layout-ownership-error-unit-drift` | 按实际暴露边界列出布局、enum 表示、packing、调用约定、所有权、异常/错误、单位和默认值差异；独立 enum/lifecycle 问题复用既有路由。 |
| `preserved-old-consumer-binary` | 在真实基线、compiler/architecture 和加载身份固定后，用保留旧二进制执行受影响入口及清理；缺少必要条件时维持 unresolved，结论只覆盖实际承诺和已观察范围。 |
| `missing-baseline-or-library-tools` | 在 `gaps` 点名缺失基线、header/artifact 配对、工具和 runtime；保留源码检查，未观察层不通过，给出最小补证动作。 |
| `equivalent-library-language-scope` | 使用实际语言的公开 API 元数据、包与外部消费者；说明原生检查不适用，按该语言的兼容承诺区分 source、binary 和 runtime。 |
| `ordinary-application-document-scope` | `boundary` 明确库专项不适用，继续必要的通用契约对照；不引入 ABI 工具、库消费者或无关矩阵。 |

所有适用案例仍使用 `boundary`、`contract_matrix`、`classification`、`skill_routes`、`gaps`、`verdict` 六个顶层输出。source compatibility、binary ABI compatibility 与 runtime behavior 分别给出范围和证据；公共库检查在现有技能内执行，不递归路由自身，也不复制其它技能全文。
