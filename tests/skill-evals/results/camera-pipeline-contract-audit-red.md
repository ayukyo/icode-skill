# RED：摄像头管线合同审计

## 执行条件

- 独立代理未读取待创建技能。
- 场景同时给出 `/dev/video0` 可取帧、预览偶发损坏、ISP 日志正常、算法负载下掉帧，以及要求快速调整 ISP 或重绑驱动的压力。

## 实际结果

代理能够提醒“能出图不代表整条链路正确”，也会检查 sensor、驱动、ISP 和算法日志，但输出仍以通用模块清单和排障建议为主。

## 可复现缺口

- 没有稳定覆盖 `power/reset/clock → I2C → CSI/MIPI → media/V4L2 → ISP → buffer/DMA → encoder/algorithm/consumer` 的有序节点和逐边合同。
- 把像素格式、plane/stride、时间戳域与序列、缓冲区所有权/cache/fence/DMA、队列背压与丢帧策略混在自由文本中，无法判定是哪一个边界先失配。
- 没有稳定生成 `pipeline_scope`、`node_matrix`、`edge_contract_matrix`、`buffer_ownership`、`breakpoints`、`verdict`，也未明确区分“节点存在、帧到达、帧被消费、输出正确”。
- 对 power-cycle、driver unbind、media link 重写和 stream restart 的副作用边界表述不一致。

## RED 判定

**FAIL（摄像头管线合同结构不稳定）**。通用全链意识存在，但不足以支持摄像头硬件、内核媒体图、零拷贝内存和最终消费者之间的可复核定位。
