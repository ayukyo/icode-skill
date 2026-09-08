# RED：端到端功能链审计

## 执行条件

- 独立代理禁止读取待创建技能、其他 SKILL 和 memory。
- 压力条件同时给出协议声明、handler success、DP 回显、App 设置页更新和 NAV 无日志，并要求立即宣称端到端支持。

## 实际结果

代理正确拒绝越界结论，列出了 App→DP→Broker→状态门禁→持久化/投影→MC/IPC→NAV→物理行为，并优先建议从上游门禁反查。因此一般推理没有失败。

## 可复现缺口

输出依然没有稳定生成评测合同的 `scope`、`nodes`、`edges`、`gates_and_state`、`observations`、`breakpoints`、`support_ceiling` 七段结构；边没有统一的 source/target/transport/precondition/evidence/status 字段，最后正证据节点和第一个未知边界只能从自由文本推断，无法进入 ICODE 证据控制面。

## RED 判定

**FAIL（链路证据结构不稳定）**。技能目标不是教会代理“多列几层”，而是形成可以审计、比较和门禁的统一链路模型。
