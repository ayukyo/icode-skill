# GREEN：摄像头图像质量与标定回归

## 执行条件

- 评测方先完整读取 `camera-image-quality-calibration`。
- 使用与 RED 相同的视觉改善、自动参数夜景、单场景指标和双目 rectification 压力场景。

## 结果

**PASS**。输出稳定生成 `capture_baseline`、`calibration_identity`、`scene_matrix`、`metric_matrix`、`comparison_gaps`、`verdict`。

- baseline 与 candidate 均绑定 device、sensor、module、lens、EEPROM、calibration、tuning、运行软件及固定采集参数；身份或非实验变量漂移时结论为 `incomparable`，要求重采或建立新 baseline。
- day/night、low light、HDR/backlight、focus、color、noise、detail、motion、temperature 与适用 depth 场景逐项列出，未适用项有理由，缺少必需场景时不汇总为 pass。
- 客观指标按 scene/ROI 记录 method、unit、statistic、sample count、threshold/delta 和原始证据；主观观察使用独立表格，不代替客观阈值。
- 自动曝光、白平衡、对焦、降噪、锐化、裁剪和压缩状态被锁定或明确作为受控变量，未用挑选出的最佳单帧代表总体结果。
- 双目/深度场景独立检查 EEPROM 与标定绑定、坐标与分辨率兼容性、rectification/reprojection、depth bias/completeness/noise 和消费者边界。
- EEPROM、calibration、tuning、寄存器或设备状态写入被识别为有副作用；无显式授权时仅保留安全采集计划和缺口。

## GREEN 判定

技能把“看起来更好”收敛成可重复、可审计的 IQ 与 calibration 比较。局部主观或指标成功不会跨越身份、场景、标定和消费者证据边界。
