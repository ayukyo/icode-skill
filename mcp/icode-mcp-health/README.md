# icode-mcp-health

免 Key、只读的 MCP 健康诊断服务。供 `/icode install`、升级复检和 CI 使用：盘点服务、校验 manifest、通过真实 stdio 握手核对 input/output schema 与 annotations、扫描敏感字段。动态启动严格限制在 `trusted_server_roots`，它不参与日常业务推理，也不读取密钥值。
