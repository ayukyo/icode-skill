# 本地 MCP 配置路径

使用 `local_mcp_common.py` 的本地 MCP 默认配置 `allowed_roots: ["$HOME"]`，只允许访问当前用户目录及其子目录。

- 支持绝对路径、`~` 和环境变量路径。
- `HOME` 非空时，`$HOME` 和 `${HOME}` 保留其配置值，兼容 Linux 等现有环境。
- `HOME` 未设置或为空时，`$HOME`、`${HOME}`、`%HOME%` 使用系统用户目录。Windows 通过 Python `Path.home()` 获取，通常由 `USERPROFILE` 决定；Linux 的空 `HOME` 则直接查询系统用户数据库，避免误将 `/` 作为默认允许根目录。
- 配置文件环境变量（如 `ICODE_WORKSPACE_CONFIG`、`ICODE_EVIDENCE_CONFIG`）使用同一套路径解析。
- 本地索引 `data_dir`、邮件附件 `download_root`、邮件凭据 `credential_file` 和路由 `policy_file` 也复用同一套展开；各字段原有相对路径基准、凭据权限检查与附件符号链接拒绝保持不变。

工程或资料位于用户目录以外时，在对应服务安装目录的 `config.json` 中显式配置实际需要的目录，例如：

```json
{
  "allowed_roots": ["$HOME", "D:/Projects"]
}
```

Linux 可将第二项替换为实际工程目录，例如 `/srv/projects`。Windows JSON 路径建议使用 `/`；使用反斜杠时必须写成 `\\`。示例路径不会由安装器自动加入，更新共享代码也不会改变已有 `allowed_roots` 配置。

路径解析后仍检查允许根的包含关系，`..` 或符号链接解析到允许范围以外的路径会被拒绝。空的允许根数组仍然是无效配置。

无法查询系统用户目录时，返回明确的配置错误，要求指定绝对路径；不会回退到工作目录或磁盘根目录。

修改配置后下一次工具调用会重新读取；更新已安装的共享 Python 代码后，需要重启对应 MCP 服务或重新打开宿主会话。仓库源码修改不会自动更新已有安装。
