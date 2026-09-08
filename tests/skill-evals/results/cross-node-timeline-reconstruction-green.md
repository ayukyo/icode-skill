# GREEN：跨节点时间线重建

## 结果

**PASS**。代理按合同输出 scope、clock_matrix、event_timeline、causal_links、missing_windows、verdict：

- 保留 App/Broker/MC 的 raw time，没有伪造 normalized time。
- request_id、完整 JSON、设备身份和 NTP 证据缺失时，跨节点链接全部保持 `candidate`。
- NAV 无日志被标为 observation gap，不是故障证据。
- 明确禁止“Broker 物理上先于 App”和“三个事件属于同一请求”。
- 下一步优先恢复未过滤原始日志、关联字段与时钟调整证据。
