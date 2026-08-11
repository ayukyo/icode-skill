# tools/dingtalk - 钉钉文档/钉盘拉取（icode 入口的可选外部资料源）

通用、参数化的钉钉文档拉取层，供 icode 入口步骤（`/icode init` / `log` / `plan` / `start`）在**零散输入含钉钉分享链接（alidocs.dingtalk.com / `/i/nodes/{token}`）时**可选调用，
把文档/钉盘里的需求文档、参考资料、规范文件拉到本地，作为流程输入。

> 本目录只做「拉取」，**不做回写**：不向钉钉发任何 POST、不上传、不评论。拉取的资料落到
> `{ICODE_OUT_DIR}/dingtalk_source/`，供人工审阅与下游步骤使用。

## 文件

| 文件 | 职责 |
|------|------|
| `scripts/dingtalk.py` | `auth` 解密 Chrome cookie 生成登录态 jar；`resolve` 链接->id；`ls` 列目录；`download` 下载真实文件 |

## 前置条件（缺了会失败，按提示补）

1. **Chrome 已登录钉钉文档**：先用 Chrome 打开 alidocs.dingtalk.com 并登录过（cookie 在本机）。
2. **Python 库**：`browser_cookie3`（cookie 解密）。缺则 `pip install browser_cookie3`（会自动带 pycryptodomex/lz4）。
3. **桌面会话**：gnome-keyring 已解锁，`DBUS_SESSION_BUS_ADDRESS` 在环境里。headless/无桌面环境跑不了（拿不到 Chrome 主密钥）。
4. **curl**：下载用 curl（Linux 默认有）。

## 命令（调试 / 单独用时）

```bash
SCRIPT=tools/dingtalk/scripts/dingtalk.py

# 1) 解密 Chrome cookie → 登录态 jar（cookie 失效时重跑这一步）
python3 $SCRIPT auth

# 2) 节点链接 → 文件夹的 dentryUuid/spaceId/corpId
python3 $SCRIPT resolve "https://alidocs.dingtalk.com/i/nodes/{token}"

# 3) 列出文件夹子项（拿子文件/子文件夹的 dentryUuid）
python3 $SCRIPT ls {dentryUuid} --space-id {spaceId}

# 4) 下载某文件（真实上传的 xlsx/pdf/docx 等）
python3 $SCRIPT download {文件dentryUuid} -o 保存路径.xlsx --space-id {spaceId}
```

`ls` 支持 `--json` 输出机器可读；`auth` 输出会带 `corp_id_hint`。

## 标准工作流

1. **`auth`** 生成 cookie jar（~/.cache/dingtalk-cookies.txt，权限 600）。若报「任何 profile 都没找到 dingtalk cookie」→ 让用户先用 Chrome 登录钉钉文档。
2. 用户给的是 `/i/nodes/{token}` 链接 → **`resolve`** 拿到 `dentryUuid`(=token)/`spaceId`/`corpId`。
   - 注意：节点页是 SPA；对**文件夹**，token 就是它的 dentryUuid；spaceId 从页面预加载的 `?spaceId=数字&...dentryUuid={token}` 里抠。
3. **`ls`** 列子项 → 得到每个文件/子文件夹的 `dentryUuid`（和类型/扩展名/子项数）。
4. 对要读的文件 **`download`** → 本地文件 → 用常规工具解析：
   - xlsx → `openpyxl`（注意可能被 DLP 封装，见下）
   - pdf → `pdftotext -layout`
   - doc/docx → `python-docx` 或 `antiword`
5. 文件夹递归：对 `ls` 出来的子文件夹重复 `ls`。

## 关键坑（反复验证过，别踩）

- **API 基址是 `/box/api/v2/...`**，不是 `/api/` 也不是 `/i/api/`（后两者直接 404，别浪费时间猜）。
- **CSRF 头名大小写敏感**：必须 `X-XSRF-TOKEN`（脚本已处理，值取自 alidocs 的 XSRF-TOKEN cookie）。写成 `x-csrf-token` 会 403。
- **列表/下载都是 GET**；POST 会 405。
- **节点页鉴权失败会跳 login.dingtalk.com**：脚本检测到就提示重跑 `auth`（cookie 过期了）。
- **原生格式 `.axls`/`.doci`/`.sheet` 等下载不下来**（download 接口只给真实上传的文件返回签名 OSS URL）。已验证两条导出路都不通：① 单文档导出在 weboffice 编辑器后端（非门户 API，无法稳定复用）；② 批量打包 `/box/api/v2/dentry/download/snapshot` 被企业管理员策略禁用。**处理：让用户在钉钉 UI 里对该文档「导出为 pdf/xlsx」，再用本工具拉导出后的真实文件。**
- **cookie 是个人鉴权凭证**：jar 权限 600，不写入工单产物、不进日志文本、不回显。

## 被 icode 入口步骤调用时

入口（init/log/plan/start）阶段0 输入收敛时，若零散输入含 alidocs.dingtalk.com 链接（含 `/i/nodes/` 路径），会自动：

```
python3 tools/dingtalk/scripts/dingtalk.py auth
python3 tools/dingtalk/scripts/dingtalk.py resolve <链接>
python3 tools/dingtalk/scripts/dingtalk.py ls ... --space-id ...
python3 tools/dingtalk/scripts/dingtalk.py download ... -o {ICODE_OUT_DIR}/dingtalk_source/... --space-id ...
```

资料落到 `{ICODE_OUT_DIR}/dingtalk_source/`，作为本次流程的需求/参考资料输入，并在 `.ico_metadata.json`
记 `dingtalk_source` 溯源字段。**零散输入无钉钉引用时整段跳过，各入口行为不变。**

与方式D2（tools/tb）区分：TB 工单日志用 `tools/tb`，钉钉文档/钉盘用 `tools/dingtalk`。
