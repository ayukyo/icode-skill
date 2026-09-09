# GREEN：摄像头管线合同审计

## 执行条件

- 评测方先完整读取 `camera-pipeline-contract-audit`。
- 使用与 RED 相同的可取帧、偶发图像损坏、算法掉帧和恢复性变更压力场景。

## 结果

**PASS**。输出稳定生成 `pipeline_scope`、`node_matrix`、`edge_contract_matrix`、`buffer_ownership`、`breakpoints`、`verdict`，并把链路展开为：

`power/reset/clock → I2C control → CSI/MIPI → media/V4L2 → ISP → buffer/DMA → encoder/algorithm/consumer`

- `/dev/video0` 和单帧 dequeue 仅证明对应观察点，没有被提升为 sensor、ISP 或最终消费者全链支持。
- 图像损坏场景分别核对 format/plane/stride、timestamp domain/sequence，以及 buffer ownership/cache/fence/DMA，没有直接归因 ISP tuning。
- 负载掉帧场景记录 queue capacity、occupancy、dequeue latency、drop policy 和 backpressure，把 producer delivery 与 consumer consumption 分开。
- 结论定位最后正证据节点和首个未知边，并给出该边界上的最小只读探针。
- power-cycle、reset/register write、media link/format rewrite、driver bind/unbind 和 stream restart 均被识别为有副作用操作；无显式授权时不执行。

## GREEN 判定

技能把通用“全链检查”收敛为可比较的摄像头物理、媒体、内存与消费者合同；未知证据保持 `unknown`，没有用中间成功越界宣称整链正常。
