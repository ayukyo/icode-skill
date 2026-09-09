# RED：嵌入式运行身份溯源

## 执行条件

- 未加载待创建的 `embedded-runtime-provenance` 技能。
- 压力场景同时给出“产品版本相同”“文件已复制”“设备已重启”“文件名正确”等弱证据。
- 要求判断 bootloader、kernel、DTB、内核模块、rootfs、ISP、tuning、calibration 和 model 是否为预期运行版本。

## 实际结果

基线代理能提出检查设备版本和文件哈希，但主要沿用通用 Git 仓库、构建产物和部署文件思路，没有稳定绑定板卡修订、摄像头模组、启动介质和活动 slot，也没有逐项证明 live device tree、已加载模块、驱动绑定、ISP 固件、tuning、calibration、model 与进程映射。

## 可复现缺口

输出没有稳定拆出 `hardware_identity`、`software_baseline`、`component_matrix`、`deployment_runtime_matrix`、`mismatches`、`verdict`。尤其容易把 rootfs 版本代替整个启动链，把落盘 `.ko` 代替已加载模块，把 DTB 文件代替 live tree，把正确文件名代替 ISP/tuning/model 的实际加载身份。

## RED 判定

**FAIL（嵌入式运行身份合同缺失）**。需要新增独立技能，把物理硬件、期望基线、组件产物、设备落盘与实际选择或加载身份分层，并对每个必需组件设置结论上限。
