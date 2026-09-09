# icode-device-observe

免 Key的嵌入式设备只读观测 MCP。仅允许配置中的命名 profile（fixture/SSH/ADB）、固定健康检查、白名单路径日志窗口和 SHA-256 对比。

它没有任意命令、部署、写文件、kill、重启或刷写工具。SSH 使用现有 ssh-agent/known_hosts 与 `BatchMode=yes`，配置中不保存密码或私钥。首次安装的 demo profile 只是占位，不会自动连接设备。

