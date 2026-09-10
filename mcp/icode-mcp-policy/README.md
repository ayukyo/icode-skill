# icode-mcp-policy

免 Key、只读的 MCP 路由策略服务。`policy.json` 是七个 ICODE 本地 MCP 的步骤路由和权限真源；它只回答“某步骤能否调用某工具、条件是什么”，不代理工具内容，也不替主模型做结论。含受管写入的服务可用 `tool_operations` 逐工具收窄 operation，避免把同一服务的读取权限误授给写工具或反向误授。
