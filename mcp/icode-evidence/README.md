# icode-evidence

免 Key、只读的确定性证据 MCP。它负责文件身份、SHA-256、带行号文本窗口、跨日志时间线和文档语料清单；不总结、不裁决根因。

默认只允许读取 `$HOME`（当前用户目录）。Windows 未设置 `HOME` 时会使用系统用户目录，无需手动设置 `HOME`。通过 `/icode install` 同时注册到 Claude Code，使用 `--client all` 可再注册 Codex。

安装后可编辑 `config.json` 的 `allowed_roots` 收窄范围，或显式加入用户目录之外的资料根目录。共享路径规则见 [配置路径说明](../_lib/README.md)。

