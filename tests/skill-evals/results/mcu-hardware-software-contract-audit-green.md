# GREEN：MCU 软硬件契约审计

## 执行条件

- 加载 `mcu-hardware-software-contract-audit` 后复演四个压力场景。
- 文档、SDK 包、构建产物和板卡身份先固定 hash/版本/变体边界。

## 结果

**PASS**。输出覆盖 `mcu_scope`、`pin_signal_matrix`、`clock_power_reset_matrix`、`register_sdk_code_trace`、`interrupt_dma_runtime_matrix`、`variant_compatibility`、`verification_plan` 和 `verdict`；每条外设链均追到物理 pin、AF/remap、clock/reset、寄存器/API、IRQ/DMA、buffer owner、构建烧录与运行证据。相邻系列复用按字段分类，缺少板上证据时保持 `unresolved`，主动写入/测量仍受显式授权约束。
