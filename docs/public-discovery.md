# ICODE 官网与公开发现

这是独立于 ICODE Runtime 的静态官网与发布工具，不是新的 Agent 服务。构建不读取工单，不安装 Skill，不更改 Claude Code、Codex 或 CodeBuddy 配置。

公开介绍中的 AI coding workflow、ticket-based development、多模型代码审查、证据化验证、断点续接和本地 UI 描述的是用途，不扩大执行触发范围。仅在用户点名 ICODE 或续接已绑定的 ICODE 工单时使用；不接管未指定 ICODE 的普通请求。多模型复评需要用户先在宿主中自行切换 Agent/模型，再运行 `/icode crosscheck`，不自动换模型。

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

`_site/` 被 gitignore。公开文件白名单为 10 项：两种语言 HTML 页面和对应 `index.md`、`llms.txt`、CSS、sitemap、RSS、公共 URL 清单、`.nojekyll`；显式配置 IndexNow 后另加 1 个所有权文件。输入仍只有 `site/content.json`、`site/style.css`、`SKILL.md` 三项，不遍历工单或其它资料。站点不是本地 `/icode ui`，不能操作你的工单。

## 面向搜索工具与 Agent 的阅读入口

`llms.txt` 按本站项目子路径提供简短双语索引，链接单一技能真源、完整安装文档、宿主边界和双语 Markdown。HTML 用 `rel="describedby"` 指向它，用 `rel="alternate" type="text/markdown"` 指向同目录 `index.md`。Markdown 从同一 `content.json` 生成，完整保留六步、场景、示例及验证限制，不单独维护第二套文案。页面使用无脚本的 Schema.org `SoftwareSourceCode` microdata 标识名称、仓库、版本、用途和许可证，全部对应可见内容；没有虚构评分、安装量或兼容标签。

这是可读取性优化，不是私有排名接口。[llms.txt 是开放提案](https://llmstxt.org/)，不能假定所有 Agent 都采用；[Google 官方说明](https://developers.google.com/search/docs/appearance/ai-features)也明确 AI 搜索没有额外必需的 AI 文本文件或专属结构化数据，符合条件不保证收录。继续以有用的可见文本、正常链接、sitemap 与真实功能说明为基础。[SoftwareSourceCode 属性](https://schema.org/SoftwareSourceCode)

可重复的目录检查（不读凭证、不安装、不制造遥测）：

```bash
python3 tools/check_skill_discovery.py                    # 零网络，仅显示查询计划
python3 tools/check_skill_discovery.py --online           # 6 渠道，各查询 icode
python3 tools/check_skill_discovery.py --online --channel skillsmp --query icode
python3 tools/check_skill_discovery.py --online --channel smithery --query "code review"
```

该工具支持 `skillsmp`、`skills.sh`、`context7`、`skillhub`、`clawhub`、`smithery` 的公开只读搜索。网站公开端点不等于稳定 API 保证；Context7 的 skills CLI 已有弃用提示，不把它作为 ICODE 运行依赖。默认仅一个名称词；可指定单渠道与最多 5 个公开词，总 HTTP 请求预算不超过 10（含允许的重定向），超限需分渠道检查。预算在发送前扣除，耗尽为 `budget_exhausted`；JSON 中 `http_budget` 记录额度和使用数。每响应最多 2 MB、单次网络超时 12 秒，无自动重试；不把个人信息当查询词。`matched` 仅证明该次有限结果含可靠身份；`candidate_unverified` 表示同名但身份未核实，不算已收录；官网链接、未识别 URL 写法或仅有平台同名账号也不能直接排除为未命中。`not_in_results` 不证明全站未收录。认证、限流、网络失败、返回结构变化分别记录，不能当作未命中。输出到 stdout，不自动更新宣传材料或触发线上发布。

`python3 tools/prepare_skill_listing.py` 可离线生成 SkillHub/Smithery 的专用提交资料草稿；不带凭据、不提交、不修改根技能，剩余账号/许可/包完整性要求见[接入说明](skill-catalog-submission.md)。

[三宿主分发原型](skill-distribution.md)只生成完整资源包，尚非已发布市场插件；源安装器依然是正式安装路线。

## 首次启用 GitHub Pages（需要仓库管理员授权）

以下设置不会由本地构建自动执行，也不需要新平台注册账号：

1. 审查 `site/content.json` 和生成目录，确认允许公开；确认仓库的可见性及套餐支持 Pages。
2. 在仓库 Settings → Pages 中把 Source 设为 GitHub Actions。默认地址预期为 `https://ayukyo.github.io/icode-skill/`，配置完成和成功部署之前不算上线。
3. 在 Actions repository variables 中设 `PUBLIC_SITE_URL` 为真实 HTTPS 站点基址，项目站点需包含 `/icode-skill/`。设置 `PUBLIC_SITE_ENABLED=true` 才启用发布。
4. 配置 `github-pages` environment 的允许来源：默认分支和授权的 Release 标签。首次可加 required reviewers；如果希望之后发布无需逐次批准，可在确认公开范围后调整环境策略。不要允许不可信分支获得发布权限。
5. 启用后，每次 push 到 `main` 自动构建更新官网。也可在默认分支手动运行 **Public site**，或发布包含该工作流的正式 Release。工作流不会代你提交、推送、创建 Release 或修改设置；仅在本地 commit 不会触发线上发布。

其他分支与 PR 不发布；PR 仅运行离线测试和无凭证构建，不部署或提交搜索通知。自动发布限于 `main` 的 push，手动运行只允许默认分支部署；正式 Release 保留标签与该源码 `SKILL.md` 的 `vX.Y.Z` 版本一致性检查。草稿、预发布、关闭开关或未设置基址时不发布。构建成功后才上传 `_site`，部署权限限定到部署 job。

源码继续绑定事件的 `github.sha`，构建与部署使用同一提交，不在 checkout 时动态替换为 main。发布前必须核对该 SHA 等于当前默认分支 HEAD，只有默认分支最新提交可以发布。若旧 Release、旧 run 重跑的 SHA 已不等于该 HEAD，或构建期间默认分支已推进，则属于过期源，会明确拒绝发布，避免回退官网；应改用新 main 提交对应的运行，或在当前默认分支重新发起手动运行，不能反复重跑过期 run。正式 Release 必须同时满足标签版本一致与源码为当前默认分支 HEAD 这两项条件。

工作流用固定提交 SHA 引用官方 Actions。升级时核对上游 tag 与 SHA，并重跑合同测试。可以通过 GitHub 自动依赖更新工具提出升级，但本实现不擅自启用额外服务。

发布任务使用串行队列（`cancel-in-progress: false` + `queue: max`），不让旧 run 重跑取消正在运行或等待中的新提交；排到后再检查源码是否过期。GitHub 最多保留 100 个排队任务，超限会取消新增任务，异常集中推送时应查看 Actions 队列。此行为依据 [GitHub 并发与多任务排队规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。

以上保护适用于包含新版工作流的运行。旧历史 run/标签仍可能使用其当时的工作流定义，修改 main 不会追溯修改它们；不要重跑升级前的发布，应从当前 main 新建运行。新版发布组 `public-site-deployment-v2` 与旧取消式并发组隔离，但不能替旧工作流补上源码校验。

## 日常发布：只维护公开内容

- `site/content.json` 是双语文案与公开版本说明的唯一输入。更新功能时两种语言成对修改。
- 浅色官网首屏以固定 SVG 图示呈现六步，CSS 动画仅表达顺序、不模拟真实运行；原生复选框可播放/暂停，系统减少动态效果偏好下静态显示。功能图卡和原生 `details/summary` 将完整说明按需展开，不依赖脚本。`stages`、`paths`、`scenes`、`delivery` 的 id/顺序和字段集合由生成器严格校验；不要把 HTML、SVG 或任意链接混入文案字段。六步标签与说明应一同修改；步骤文档链接在生成器中固定映射。Markdown 保留完整说明，不跟随首页折叠隐藏内容。
- `updates` 的 `reviewed_on` 是文案审阅日期，不是捏造的版本发布日期。新版本如需 RSS 公告，push 到 main 前添加经审查的版本条目；源码版本徽标由 `SKILL.md` 自动读取。
- 不从任意 commit、PR、Release 正文生成营销文案。不要把本地工单、demo 输出、真实日志、企业邮件或路径复制到公开文案。
- 案例默认是通用用法示例，不是运行成功证明。要增加真实演示，先单独取得披露授权并核验内容及验证边界。
- 不承诺零缺陷、全部宿主实测通过、节省固定比例或搜索排名。

## Packages、Release 与 Pages

[GitHub Packages](https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages) 用于分发 npm 等软件包及 Docker/OCI 容器制品，不是 Skill 目录；Release 记录版本与发行附件，Pages 托管本项目的静态官网，三者用途不同。

本项目当前不启用 Packages：根目录没有独立 npm 包或容器包，安装仍依赖源码、宿主适配与官方 `install.sh`。不为展示 Packages 入口制造空包，也不添加包发布权限；未来确有可独立分发的 Runtime 容器时，再评估通过 GHCR 发布。

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

工作流整体绿色不等于 IndexNow 通知成功：通知步骤保留 `continue-on-error`，因此应检查原始 `.outcome`，不能用经过容错处理的 `.conclusion` 判断。验收新的 **Public site** run 时，分别查看 summary 中的 `Pages deployment: <outcome>`、`IndexNow step outcome: <outcome>` 与 notifier 原始受控 JSON。JSON 的 `status`、`stage`、`http_status`（如有）用于区分所有权验证与通知提交；`skipped`、`dry-run`、`failed` 都不能报成已通知，`received` / `pending` 也不能报成已收录。旧 summary 若只有通用边界说明，应回看通知步骤输出。

### 2026-09-20 启用排查快照

本轮已保存 repository variable `INDEXNOW_ENABLED=true` 与 Actions secret `INDEXNOW_KEY`；这只证明配置已保存，不证明通知已成功。本记录不包含真实所有权 key。

旧 [Public site run 35511779722](https://github.com/ayukyo/icode-skill/actions/runs/35511779722) 的打包产物包含所有权文件，但公开访问该文件返回 404，通知结果为 `failed`。同一 SHA 重新部署时可能复用了旧产物，与 [actions/deploy-pages issue #383](https://github.com/actions/deploy-pages/issues/383) 描述的现象疑似相关；目前仅作排查线索，不能视为已确认根因。

后续应在新提交 push 到 `main` 后检查自动触发的 **Public site**，核对该次源码 SHA、打包产物和实际部署，再检查公开所有权文件与新 summary 的通知 JSON / outcome；无需重复手动触发同一次发布。新 run 的结果另记在本地运行报告；这里保留带日期的故障快照与验收方法，不将旧失败或未来成功写成永久状态。未获得新运行证据前，不能宣称已通知或已收录。

## skills.sh 与宿主兼容边界

主流宿主的本地技能加载、插件市场、跨宿主目录和 ICODE 支持等级，见 [2026-09-20 Agent Skill 发现调研](agent-skill-discovery.md)。该次只读查询确认 SkillsMP 已有 ICODE 条目，但描述仍旧；skills.sh 指定查询未命中。不要把目录收录、关键词推荐、安装完整与运行验收混为一谈。

ICODE 根 `SKILL.md` 有规范的 name/description，适合技能发现；共享技能则使用 `.template` 源文件，由本仓库安装器生成。skills CLI 的技能发现不等同于执行 ICODE 安装器，也不证明共享技能、MCP、DOCX runtime 或 CodeBuddy 命令桥全部安装。

只读发现命令（关闭遥测）：

```bash
DISABLE_TELEMETRY=1 npx skills add ayukyo/icode-skill --list
```

`--list` 在安装阶段之前返回，不安装到宿主。npx 可能下载 CLI，发现过程也可能下载仓库到临时目录或缓存；这些下载不等于安装完成。复核下面的版本快照时，可用 `DISABLE_TELEMETRY=1 npx skills@1.7.0 add ayukyo/icode-skill --list` 固定 CLI 版本。

正式安装入口仍是仓库根 `./install.sh --client all`，CodeBuddy 单独安装用 `--client codebuddy`；默认 `--client claude`。发现/兼容性评估不等于已上榜，不能把本地列出技能当成 skills.sh 已收录证据。测试不得刷安装量；不得访问用户真实宿主配置或全局安装。

## 2026-09-20 技能发现评估快照

以下为本轮开始前及本轮的带日期观测，不是在线目录或搜索能力的永久结论：

| 检查入口 | 本轮观测 | 证据边界 |
| --- | --- | --- |
| 临时目录解包 npm `skills@1.7.0`，对本地源码运行 `add <本地源码路径> --list` | 发现 1 个技能：`icode` | 先检查包内容，禁用安装脚本和遥测；未安装到宿主 |
| `skills@1.7.0` 直接对 GitHub 仓库运行 `add ayukyo/icode-skill --list` | 可识别 `icode` | 遥测关闭，只验证仓库解析与列出技能，未执行完整安装 |
| 本轮优化前的 skills.sh 与旧 CLI search 检索 | 品牌词 `icode` 以及 `coding workflow`、`code review`、`multi-model` 等检索未命中 | 仅代表当时查询结果；不是永久未收录，也不与直接 GitHub 发现成功矛盾 |

配套共享技能的 `.template`、MCP 注册、DOCX runtime 和 CodeBuddy 命令桥不在此发现结果中，仍需使用本仓库统一安装器。没有获得 skills.sh 在线收录、榜单位置或完整 skills CLI 安装成功的证据；不通过重复安装制造计数，也不为目录展示修改现有 Skill 结构。

### 第三方目录的根路径过滤问题

2026-09-20 检查 `Chat2AnyLLM/awesome-claude-skills`：目录已列出本仓库，但显示技能数为 0。其配置 `skillsPath='./'` 经 `metadata_catalog.py` 的 `strip('/')` 处理后成为 `'.'`；后续路径过滤排除了仓库根的 `SKILL.md`。这是该目录的根路径处理问题，不能据此认定 ICODE 缺少技能文件。

已提交可复现的 [上游 issue #52](https://github.com/Chat2AnyLLM/awesome-claude-skills/issues/52)，截至本次快照状态为 **Open，待上游修复**。未宣称上游已修复；保持现有根 `SKILL.md` 与 `.template` 结构，不复制或移动仓库内容绕过目录过滤。

## 故障定位

| 现象 | 检查与处理 |
| --- | --- |
| output already exists | 改用新输出目录，工具不会覆盖现有产物 |
| version mismatch | 确认 Release 标签与该标签中的 SKILL 版本一致 |
| stale source / 过期源拒绝发布 | 事件 SHA 已不等于当前默认分支 HEAD；使用新 main 运行或从当前默认分支新建手动运行，不重跑旧 Release/旧 run |
| 构建通过但没有部署 | 检查启用开关、基址、触发来源与环境策略 |
| Pages 失败 | 检查 Pages Source、仓库可见性/套餐及 environment 来源限制 |
| IndexNow skipped | 未配置 key；网站仍可正常使用 |
| 所有权验证失败 / 公开文件 404 | 对比该次打包产物与实际部署，确认 key、基址和子路径一致；若疑似同 SHA 产物复用，核验新 main 提交自动触发的 Public site，不只反复重跑旧 run |
| 工作流绿色但通知未成功 | 检查新 summary 的 Pages deployment、IndexNow step outcome 及受控 JSON；以 `.outcome` 和实际 `status` 为准，不能用 `.conclusion` 推断成功 |
| 第三方目录列出仓库但技能数为 0 | 区分目录解析与真实技能发现；根路径过滤问题见上游 issue #52，待上游修复 |
| 搜索不到项目 | 通知仅请求发现；抓取、收录和排名由搜索引擎决定 |

## 官方依据

- [GitHub Pages 工作流及权限](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [IndexNow 协议、所有权与子路径](https://www.indexnow.org/documentation)
- [Skills CLI](https://github.com/vercel-labs/skills) 与 [技能生态文档](https://www.skills.sh/docs)
