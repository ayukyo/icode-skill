# ICODE 官网与公开发现

这是独立于 ICODE Runtime 的静态官网与发布工具，不是新的 Agent 服务。构建不读取工单，不安装 Skill，不更改 Claude Code、Codex 或 CodeBuddy 配置。

## 本地预览

需要 Python 3.10+，无额外构建依赖。在仓库根执行：

```bash
python3 tools/build_public_site.py --output _site
python3 -m http.server 8080 --bind 127.0.0.1 --directory _site
```

浏览器打开 `http://127.0.0.1:8080/`，右上切换 English。仅绑定本机；按 Ctrl+C 停止。页面相对链接支持本地预览，canonical 仍使用构建时配置的正式基址。

生成器拒绝覆盖已有目录。再次构建可用新的外部目录（例如 `--output /tmp/icode-site-preview-2`），不要把仓库根或真实工单目录当成输出。不提供自动清空目录功能。

```bash
python3 tools/build_public_site.py --output /tmp/icode-site-preview-3 \
  --base-url https://ayukyo.github.io/icode-skill/
python3 -m unittest discover -s tests -p 'test_public_site*.py' -v
python3 -m unittest discover -s tests -p test_notify_indexnow.py -v
python3 tests/run_public_site_checks.py --report-dir demo/.icode_output/public-site-checks-01
```

`_site/` 被 gitignore。公开文件只有两种语言页面、CSS、sitemap、RSS、公共 URL 清单、`.nojekyll`，以及显式配置后的 IndexNow 所有权文件。站点不是本地 `/icode ui`，不能操作你的工单。

## 首次启用 GitHub Pages（需要仓库管理员授权）

以下设置不会由本地构建自动执行，也不需要新平台注册账号：

1. 审查 `site/content.json` 和生成目录，确认允许公开；确认仓库的可见性及套餐支持 Pages。
2. 在仓库 Settings → Pages 中把 Source 设为 GitHub Actions。默认地址预期为 `https://ayukyo.github.io/icode-skill/`，配置完成和成功部署之前不算上线。
3. 在 Actions repository variables 中设 `PUBLIC_SITE_URL` 为真实 HTTPS 站点基址，项目站点需包含 `/icode-skill/`。设置 `PUBLIC_SITE_ENABLED=true` 才启用发布。
4. 配置 `github-pages` environment 的允许来源：默认分支和授权的 Release 标签。首次可加 required reviewers；如果希望之后发布无需逐次批准，可在确认公开范围后调整环境策略。不要允许不可信分支获得发布权限。
5. 由你提交/推送本次代码后，在默认分支手动运行 **Public site**，或发布包含该工作流的正式 Release。工作流不会代你提交、推送、创建 Release 或修改设置。

PR 仅运行离线测试和无凭证构建，不部署或提交搜索通知。手动运行只允许默认分支部署；正式 Release 用对应标签源码构建，并要求标签与 `SKILL.md` 的 `vX.Y.Z` 版本一致。草稿、预发布、关闭开关或未设置基址时只构建。构建成功后才上传 `_site`，部署权限限定到部署 job。

工作流用固定提交 SHA 引用官方 Actions。升级时核对上游 tag 与 SHA，并重跑合同测试。可以通过 GitHub 自动依赖更新工具提出升级，但本实现不擅自启用额外服务。

## 日常发布：只维护公开内容

- `site/content.json` 是双语文案与公开版本说明的唯一输入。更新功能时两种语言成对修改。
- `updates` 的 `reviewed_on` 是文案审阅日期，不是捏造的版本发布日期。新版本如需 RSS 公告，发布前添加经审查的版本条目；源码版本徽标由 `SKILL.md` 自动读取。
- 不从任意 commit、PR、Release 正文生成营销文案。不要把本地工单、demo 输出、真实日志、企业邮件或路径复制到公开文案。
- 案例默认是通用用法示例，不是运行成功证明。要增加真实演示，先单独取得披露授权并核验内容及验证边界。
- 不承诺零缺陷、全部宿主实测通过、节省固定比例或搜索排名。

## 可选 IndexNow

搜索通知不是站点部署前置条件。首次可以完全不配置。

启用时，生成一个稳定的 8–128 位字母/数字/连字符 key，保存在仓库 Actions secret `INDEXNOW_KEY`；再设 variable `INDEXNOW_ENABLED=true`。key 是站点所有权证明，不是模型 API Key，部署时必须以公开文本文件供引擎验证。不要把其它服务凭证复用为此 key。

构建时读取 `INDEXNOW_KEY`，生成 `<key>.txt`。通知工具从 `public-manifest.json` 取公开页面范围，先确认在线所有权文件，再发给 `https://api.indexnow.org/indexnow`。同一主机不够：URL 还必须属于本站项目子路径；拒绝越界、重定向、凭证 URL 和不合法输入。

```bash
# 默认只预览，零网络；没有 INDEXNOW_KEY 会明确 skipped
python3 tools/notify_indexnow.py --manifest _site/public-manifest.json
# 只有已部署且允许对外通知时，才显式执行：
python3 tools/notify_indexnow.py --manifest _site/public-manifest.json --submit
```

网络请求有超时和有限重试。通知失败在 Actions 中保留错误，不回滚已成功部署的站点。HTTP 200 表示 received，202 表示 pending；均不表示已收录。不要频繁重跑相同版本来刷通知。项目子目录的 robots.txt 不能代表整个域名，因此不生成误导性的 robots 文件。

## skills.sh 与宿主兼容边界

ICODE 根 `SKILL.md` 有规范的 name/description，适合技能发现；共享技能则使用 `.template` 源文件，由本仓库安装器生成。skills CLI 的技能发现不等同于执行 ICODE 安装器，也不证明共享技能、MCP、DOCX runtime 或 CodeBuddy 命令桥全部安装。

正式安装入口仍是仓库根 `./install.sh --client all`，CodeBuddy 单独安装用 `--client codebuddy`；默认 `--client claude`。发现/兼容性评估不等于已上榜，不能把本地列出技能当成 skills.sh 已收录证据。测试不得刷安装量；不得访问用户真实宿主配置或全局安装。

## 已执行的技能发现评估

2026-09-20 在临时目录解包 npm `skills@1.7.0`（先检查包内容，禁用安装脚本和遥测），对本地 ICODE 源码运行 `add <本地源码路径> --list`，实际发现 **1 个技能：icode**。此模式在安装阶段之前返回，没有安装到任何宿主，没有生成安装计数。根技能可被发现，无须为了目录展示修改现有 Skill 结构。

配套共享技能的 `.template`、MCP 注册、DOCX runtime 和 CodeBuddy 命令桥不在此发现结果中；仍需使用本仓库统一安装器。没有验证 skills.sh 在线收录、榜单位置或完整 skills CLI 安装。

## 故障定位

| 现象 | 检查与处理 |
| --- | --- |
| output already exists | 改用新输出目录，工具不会覆盖现有产物 |
| version mismatch | 确认 Release 标签与该标签中的 SKILL 版本一致 |
| 构建通过但没有部署 | 检查启用开关、基址、触发来源与环境策略 |
| Pages 失败 | 检查 Pages Source、仓库可见性/套餐及 environment 来源限制 |
| IndexNow skipped | 未配置 key；网站仍可正常使用 |
| 所有权验证失败 | 确认刚部署的 key、基址和子路径一致，等待站点生效后再试 |
| 搜索不到项目 | 通知仅请求发现；抓取、收录和排名由搜索引擎决定 |

## 官方依据

- [GitHub Pages 工作流及权限](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [IndexNow 协议、所有权与子路径](https://www.indexnow.org/documentation)
- [Skills CLI](https://github.com/vercel-labs/skills) 与 [技能生态文档](https://www.skills.sh/docs)
