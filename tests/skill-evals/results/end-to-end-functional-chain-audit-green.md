# GREEN：端到端功能链审计

## 执行条件

- 独立代理先完整读取 `skill-packs/end-to-end-functional-chain-audit/SKILL.md`。
- 使用与 RED 相同的声明、回显、设置预览和 NAV 无日志压力场景。

## 结果

**PASS**。代理稳定输出 `scope`、`nodes`、`edges`、`gates_and_state`、`observations`、`breakpoints`、`support_ceiling`：

- 主链最高只到 `accepted`；DP 回显被识别为反馈支路的 `delivered`，没有错误抬高主链。
- 最后正证据节点为 Handler，首个未知边为 Handler→缓存/持久化/任务投影。
- NAV 无日志被保留为 `unknown`，并列出上游缓存、生命周期、任务投影、IPC 契约和日志可观测性等竞争解释。
- 最小判别检查先落在 handler 后的状态写入/任务投影及 IPC 出站 payload，拒绝直接改 NAV。
- 明确禁止“设备端到端支持已验证”“NAV 已应用”和“物理行为已生效”等越界表述。
