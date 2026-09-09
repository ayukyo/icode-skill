# GREEN：嵌入式运行身份溯源

## 执行条件

- 加载 `embedded-runtime-provenance` 后，对 RED 中四个场景逐项进行合同演练。
- 不以产品名、单一版本字符串、复制成功、重启成功或文件名作为完整运行身份。

## 结果

**PASS**。技能要求稳定输出六类结构：

- `hardware_identity` 绑定物理设备、板卡与 SoC 修订、启动介质/slot、外设实物和摄像头 sensor/module/lens/EEPROM。
- `software_baseline` 独立记录 bootloader、kernel、DTB/overlay、rootfs、BSP、工具链、启动参数及其目标。
- `component_matrix` 逐项覆盖 kernel image、DTB、每个 `.ko`、rootfs、协处理器/ISP firmware/library、tuning、calibration、model 与应用。
- `deployment_runtime_matrix` 强制区分设备文件和实际选择、加载、映射或驱动绑定对象，并绑定 boot/restart 事件与时间窗。
- `mismatches` 使用硬件变体、错误 slot、DTB/module/ISP/tuning/model 不一致、依赖不兼容和 `deployed_not_loaded` 等可比较分类。
- `verdict` 仅在绑定设备、窗口内每个必需组件均有运行证据时允许 `proven`，否则保持 `mismatch` 或 `unresolved`。

四个场景均不能再以弱证据越级：复制后的模块缺少加载与绑定证据时为 `unresolved`；A/B slot 未确认时不能证明新 rootfs；同名 tuning/model 必须比较身份和消费者；硬件修订或模组身份不明时禁止复用其他设备结论。
