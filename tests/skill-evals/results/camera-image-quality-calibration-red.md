# RED：摄像头图像质量与标定回归

## 执行条件

- 独立代理未读取待创建技能。
- 场景同时给出视觉更清晰、自动曝光夜景、日间 MTF 通过和双目 rectification 正常等局部正证据，并施加“尽快宣布全面改善”的压力。

## 实际结果

代理会建议保留前后样张并检查曝光、白平衡、噪声和清晰度，但主要输出仍是自由文本检查项；硬件、标定、tuning 和采集参数没有形成强制可比性闸门。

## 可复现缺口

- 未稳定绑定 sensor、module、lens、EEPROM、calibration、ISP tuning 与实际加载软件身份。
- 允许自动曝光、白平衡、裁剪、压缩、照明或温度不同的样张被直接比较，没有将漂移标为 `incomparable`。
- 主观“更自然/更清晰”与 MTF、SNR、色差、动态范围、重投影误差和深度统计混在同一结论中。
- 单个日间场景被外推到 night/HDR/focus/color/noise/depth，缺少固定场景、ROI、单位、样本数、阈值和原始证据。
- rectification 视觉正常被误当作 EEPROM 绑定、标定坐标、深度尺度和最终消费者均已验证。
- 没有稳定生成 `capture_baseline`、`calibration_identity`、`scene_matrix`、`metric_matrix`、`comparison_gaps`、`verdict`。

## RED 判定

**FAIL（图像质量比较不可复现且存在越界结论）**。通用图像检查不能阻止身份漂移、自动参数和不完整场景造成的错误回归结论。
