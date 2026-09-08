# RED：跨节点时间线重建

## 实际结果

未加载新技能的代理正确拒绝按不同节点显示时间直接排序，识别了未知 clock offset、NTP 校时、grep 截断、request_id 丢失和 NAV 无日志的证据边界。

## 可复现缺口

输出没有稳定形成 `scope`、`clock_matrix`、`event_timeline`、`causal_links`、`missing_windows`、`verdict`；事件缺少 source/raw_time/normalized_time/uncertainty/correlation/payload/state/evidence 字段，因果链接也未显式分为 causal、correlated、candidate、refuted。无法比较后续补齐时钟或原始日志后的时间线版本。

## RED 判定

**FAIL（可校正、可增量重建的时间线合同缺失）**。
