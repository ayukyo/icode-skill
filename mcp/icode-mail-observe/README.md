# icode-mail-observe

ICODE 的 optional unattended mailbox adapter（可选无人值守邮件适配器）。它只在邮箱范围搜索、无浏览器环境或用户显式选择 IMAP 时列出 allowlist 内目录、读取邮件/RFC 线程并受控保存附件；用户提供显式网页邮件链接时，默认复用已经登录的浏览器，不需要安装或配置本 MCP。

## 安全边界

- 邮箱始终以 `readonly=True` 打开，正文始终使用 `BODY.PEEK[]`，不会造成已读状态变化。
- 不提供 SMTP、STORE、MOVE、COPY、EXPUNGE、APPEND、原始命令、回复或删除工具。
- 邮件正文、表格、图片、链接、文件名和附件均视为不可信输入；不会获取远程图片、打开链接或执行活动内容。
- `save_attachment` 是唯一写操作，只能在 `download_root` 下写一个指定 `part_id`，拒绝路径穿越、覆盖不同内容和可执行文件魔数。

## 可选 IMAP 配置

先安装，再编辑安装目标中的 `~/.claude/skills/icode/mcp/icode-mail-observe/config.json`，填写主机、用户名、目录 allowlist 和下载根目录；源码目录的运行配置不会覆盖已安装配置。认证信息按以下顺序读取，永不写入配置示例或工具输出：

1. 环境变量 `ICODE_MAIL_OBSERVE_SECRET`
2. `credential_file` 指向的普通文件（Linux/macOS 权限必须为 `0600` 或更严格；Windows 应使用仅当前用户可读的 ACL）

可用 `ICODE_MAIL_OBSERVE_CONFIG` 指定其他配置路径。安装和凭据文件示例：

```bash
./mcp/install.sh --client all icode-mail-observe
mkdir -p ~/.config/icode
chmod 700 ~/.config/icode
# 用编辑器创建 ~/.config/icode/mail-observe.secret，然后：
chmod 600 ~/.config/icode/mail-observe.secret
```

普通网页邮件分析只需先登录浏览器并提供精确链接；ICODE 通过网页的“下载邮件”和附件下载入口取得限定证据，再由 `tools/email_intake.py` 离线解析。没有可用浏览器时，也可直接提供已导出的 `.eml`：

```bash
python3 tools/email_intake.py inspect \
  --root /path/to/mail-export \
  --path /path/to/mail-export/message.eml \
  --output-root /path/to/mail-export/analysis \
  --output /path/to/mail-export/analysis/mail_manifest.json
```

Outlook `.msg` 需要在执行离线 intake 的 Python 环境中由用户选择安装 `extract-msg>=0.56.1,<0.57`；它不是 MIT 工程的默认强制依赖，缺失时工具会返回 `requires_optional_dependency`，也可改导出标准 `.eml`。
