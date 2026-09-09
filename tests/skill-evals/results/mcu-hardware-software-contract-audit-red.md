# RED：MCU 软硬件契约审计

## 执行条件

- 未加载 `mcu-hardware-software-contract-audit`。
- 输入同时包含原理图、MCU 手册、相邻系列 Demo、SDK、DMA/中断代码和烧录结果。

## 实际结果

基线代理通常停在“网名一致、宏存在、SDK 初始化成功或烧录成功”，没有证明 package pin、AF/remap、clock/reset、register、IRQ/DMA、buffer owner、实际构建和板上镜像属于同一条链。

## RED 判定

**FAIL（MCU 端到端契约缺失）**。相邻系列同名接口和烧录成功被错误提升为运行正确。
