# icode-mcp-health

免 Key、只读的 MCP 健康诊断服务。供 `/icode install`、升级复检和 CI 使用：盘点服务、校验 manifest、通过真实 stdio 握手核对 input/output schema 与 annotations、扫描敏感字段。动态启动严格限制在 `trusted_server_roots`，它不参与日常业务推理，也不读取密钥值。

`diagnose_registration(host_config, entries_dir=None)` 检查明确指定的 JSON `mcpServers` 或 Codex TOML `mcp_servers` 配置与导出条目；所有文件均受 `allowed_roots` 限制，包括逐个导出文件。它不启动配置中的进程、不回传参数或环境变量值、不写配置。`static_ok` 仅表示启动命令与平台形态未发现静态问题，会话工具状态仍为 `unverified`。Python 3.10 使用条件依赖 tomli；缺解析器时明确报告 `toml_parser_unavailable`。CLI、安装摘要及修复边界见 [环境诊断](../../references/environment_diagnostics.md)。
