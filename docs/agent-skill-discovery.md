# 主流 Agent 如何发现 ICODE

## 当前边界（2026-09-21 补充）

CodeBuddy 宿主文档漂移已在 [宿主适配契约](../references/host_adapters.md)修正为按版本和实际暴露能力判断；保留旧命令桥，不自动新增 Hook、权限或 Runtime 后端。[WorkBuddy](workbuddy-support.md)已有官方配置路径依据，尚未完成宿主实机验证。含 8 套 PPT 模板的本地归档约 18 MiB，仅是本地体积结果，不证明平台接受；完整包仍须核对容量、依赖和[第三方素材许可](../tools/ppt/NOTICE)。[目录草稿工具](skill-catalog-submission.md)不自动声明整包 MIT；Smithery 使用无需 API key 的网页 `gitUrl` 草稿，命名空间待核实。以上均不构成线上发布成功证明。

以下保留 2026-09-20 的历史快照，查询结果与“未注册”等表述仅对应该次核查。

核查日期：**2026-09-20**。这是官方文档与公开查询的时间点快照，不是永久支持表，也不是全市场排名。源码核查基线 `6dec382`；本轮没有安装新宿主、提交市场申请或改动用户配置。

## 结论

ICODE 已具备标准技能入口和 Claude Code、Codex、CodeBuddy 安装适配，可以进一步进入跨宿主发现渠道。**不需要为每个搜索工具复制一套 ICODE。** 但被目录识别、被推荐、安装完整、真实运行通过是四件不同的事。

- 本地加载：宿主扫描技能目录，按名称/描述选择，或由用户显式调用。
- 联网查找：目录、CLI 或插件市场检索公开资源；有些宿主只提供本地加载。
- 收录与推荐：目录自己的抓取、审核、遥测和排序机制；没有一个全宿主共享的自动推荐池。
- 完整运行：ICODE 还需要步骤、引用、工具、schemas/gates、生成的共享技能，以及按宿主配置的依赖。只复制 `SKILL.md` 不足以完成安装。

## 宿主机制与当前支持边界

“已有适配”指仓库有安装目标，不表示本轮在该宿主完成了端到端实装。其它宿主均是**兼容候选，尚未正式验证**。

| 宿主 | 技能加载与发现方式 | 联网分发/推荐入口 | ICODE 判断 |
| --- | --- | --- | --- |
| Claude Code | `.claude/skills`，描述匹配或显式调用 | `/plugin` Discover 和配置的 marketplace；官方策展与社区审核渠道不同 | 已有适配；可评估完整插件包，不保证市场收录。[技能](https://code.claude.com/docs/en/skills)、[市场](https://code.claude.com/docs/en/discover-plugins)、[社区提交](https://code.claude.com/docs/en/plugins#submit-your-plugin-to-the-community-marketplace) |
| Codex | 项目/用户 `.agents/skills`，CLI `/skills` 或 `$`；内置 skill-installer 可读精选源和其它仓库 | 桌面插件目录、CLI `/plugins`；当前 IDE 不支持插件，仍可用独立技能 | 已有适配；复用完整安装器，插件另做打包与验证；未证明 ICODE 已在公共插件目录上架。[技能](https://learn.chatgpt.com/docs/build-skills)、[插件](https://learn.chatgpt.com/docs/plugins) |
| CodeBuddy | CLI `.codebuddy/skills`；IDE 文档还列兼容来源；形态和版本需区分 | CLI 插件市场，IDE SkillHub 搜索安装 | 已有适配；当前工程部分“宿主不支持”说明需要按版本复核，不能据此直接删除命令桥。[技能](https://www.codebuddy.ai/docs/cli/skills)、[CLI 市场](https://www.codebuddy.ai/docs/cli/plugin-marketplaces)、[IDE 发布记录](https://www.codebuddy.ai/docs/zh/ide/release-notes/release-notes) |
| Cursor | `.cursor/skills`、`.agents/skills` 等兼容来源；描述匹配或显式选择 | Customize 中的插件市场、团队市场；公共市场有审核 | 可读取不等于完整运行；需要 MCP、资源和入口测试。单个 Skill 的发布不会自动带上它引用的其它 Skill。[技能](https://cursor.com/docs/skills)、[插件](https://cursor.com/docs/plugins) |
| GitHub Copilot | `.github/skills`、`.claude/skills`、`.agents/skills` 等，CLI/VS Code/云端各有边界 | `gh skill search/preview/install`、CLI 插件、VS Code `@agentPlugins` | 官方搜索基于 GitHub Code Search；ICODE 可作为候选，但没有本轮搜索命中或完整运行证明。[CLI 技能](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)、[搜索](https://cli.github.com/manual/gh_skill_search)、[VS Code 插件](https://code.visualstudio.com/docs/agent-customization/agent-plugins) |
| Gemini CLI | `.gemini/skills`、`.agents/skills`；`/skills list`、按需激活、仓库安装 | 扩展 Gallery；扩展可包含技能，但不是全网技能搜索库 | 可复用目录结构；缺专用安装目标、MCP 与完整流程验证。[技能](https://geminicli.com/docs/cli/using-agent-skills/)、[扩展发布](https://geminicli.com/docs/extensions/releasing/) |
| OpenCode | V1 支持 `.opencode/skills` 及 Claude/Agents 兼容目录；按需调用 skill | V2 支持指定 HTTP 技能目录，不等于自动搜索全网 | 候选；V1/V2 配置与调用不能混为一谈。[V1](https://opencode.ai/docs/skills/)、[V2](https://opencode.ai/v2/docs/skills) |
| Cline | `.cline/skills` 等来源，描述选择/显式调用 | 官方技能集合与 Skills CLI；未确认任意第三方技能的原生统一推荐市场 | 候选；需完整目录、依赖及工具能力验证。[技能](https://docs.cline.bot/customization/skills)、[官方集合](https://github.com/cline/skills) |
| Windsurf / Cascade | `.windsurf/skills`、共享兼容目录，自动选择或 `@` | 已确认 MCP Marketplace，未确认独立 Skill 市场 | 候选；MCP 市场不是 Skill 市场。原 Windsurf 文档现转向 Devin Desktop/Cascade，不扩推到所有 Devin 产品。[技能](https://docs.devin.ai/desktop/cascade/skills)、[MCP](https://docs.devin.ai/desktop/cascade/mcp) |
| Antigravity | 当前 `.agents/skills`，兼容旧 `.agent/skills`；IDE/2.0/CLI 全局路径不同 | Google 精选插件；自定义插件有清单要求 | 候选；必须按产品形态和版本适配，未确认公开第三方投稿条件。[技能](https://antigravity.google/docs/skills)、[插件](https://antigravity.google/docs/plugins/) |
| Roo Code | 历史 `.roo/skills` 与模式技能 | 历史生态 | 低优先级：官方仓库公告扩展于 2026-05-15 停止并归档，不将后继产品视为已兼容。[官方仓库](https://github.com/RooCodeInc/Roo-Code) |

### 已发现的版本漂移风险

当前 [host_adapters.md](../references/host_adapters.md) 将 CodeBuddy 描述为“Skill 不自动注册斜杠命令”“无 Hook”。但当前官方技能文档已有 `/skill-name`、`user-invocable` 与 fork 模式技能 Hook；后者还受版本、信任与授权条件约束。下一轮应针对实际安装版本验证目录加载、命令优先级与 Hook 能力后修订合同，而不是在官网改造中直接删除旧命令桥或开启 Hook。[当前官方说明](https://www.codebuddy.ai/docs/cli/skills)

## 跨宿主目录：我们实际能否被找到

| 渠道 | 机制 | 本次结果 / 限制 |
| --- | --- | --- |
| Agent Skills 标准 | 定义 `SKILL.md`、元数据和资源组织，不提供统一搜索排名 | ICODE 有标准入口；标准不是目录收录或完整运行认证。[规范](https://agentskills.io/specification) |
| Vercel skills.sh / find-skills | 网站与 `skills find` 检索；find-skills 是指导搜索的技能，不是每个宿主内建功能；榜单依赖 Skills CLI 安装遥测 | 源仓库可被 CLI `--list` 识别。此次 `icode` + `owner=ayukyo`、limit20 返回空数组，仅表示该查询未命中。不能为了上榜重复安装或冒充真实用户量。[FAQ](https://skills.sh/docs/faq)、[find-skills 源码](https://github.com/vercel-labs/skills/blob/main/skills/find-skills/SKILL.md) |
| SkillsMP | 聚合公开 GitHub 技能，关键词/分类检索；文档提供有限额匿名 API | **已收录 ICODE**：作者查询返回 name=icode、author=ayukyo 和本仓库。描述仍含旧 `--reuse` 参数，不能当作最新版使用说明。[ICODE 条目](https://skillsmp.com/creators/ayukyo/icode-skill/skill)、[API](https://skillsmp.com/docs/api)、[抓取条件](https://skillsmp.com/docs/faq) |
| awesome-claude-skills 第三方目录 | 仓库配置与技能文件扫描 | 根路径过滤问题仍按此前待上游处理记录，不将另一个目录的收录当成它已修复。[上游 issue #52](https://github.com/Chat2AnyLLM/awesome-claude-skills/issues/52) |

GitHub Code Search 补充实测（同日、新元数据推送前）：登录后的公开代码查询 `repo:ayukyo/icode-skill path:SKILL.md icode` 显示 0 files，同时明确提示仓库正在建立索引。此结果是 **indexing_pending**，不是技能文件缺失或格式错误，也不是 `gh skill search` CLI 实测成功。该 CLI 的官方说明确认它使用 Code Search 匹配 `SKILL.md` 名称/描述；本机未安装 gh，未为了检查额外安装或读取凭据。索引与排序由 GitHub 控制，避免反复刷请求。[本次查询](https://github.com/search?q=repo%3Aayukyo%2Ficode-skill%20path%3ASKILL.md%20icode&type=code)、[官方查找命令](https://cli.github.com/manual/gh_skill_search)

### 补充渠道：并非都有无账号自动收录

| 渠道 | 机制与本次观测 | 当前可做与限制 |
| --- | --- | --- |
| Context7 Skills / ctx7 | 确有 Skill 注册表；查询 `icode` 返回 20 条，按 project/url 核对未见本仓库。当前官方 master 已将 `ctx7 skills` 标记弃用，并声明下一主版本停止 | 保持标准入口及完整资源；不增加长期依赖，不把文档库收录当 Skill 收录。[命令源码](https://github.com/upstash/context7/blob/master/packages/cli/src/commands/skill.ts)、[技能说明](https://github.com/upstash/context7/blob/master/skills/context7-cli/references/skills.md) |
| 腾讯 SkillHub | 有独立技能目录；`keyword=icode`、pageSize24 的本次搜索为空。官方教程要求账号/实名认证，CLI 发布需 Token，不能假定 GitHub 公开即自动抓取 | 本仓库保留名称、双语用途、MIT/主页。其发布专用 slug/version/displayName 必须在单独发行材料中准备，不往公共根技能强塞非通用字段；本轮未注册、发布或获审核。[官网](https://skillhub.cn/)、[教程](https://skillhub.cn/tutorials#publish-via-cli)、[发布字段](https://skillhub.cn/ai/release.md) |
| ClawHub / OpenClaw | 有公开注册表；`q=icode`、limit10 本次为空。正式发布需登录/凭证；官方说明发布技能采用 MIT-0、无需署名 | 其许可条件与仓库现有 MIT 不同，不擅自重新许可、发布或用空包装上架；需权利人另行确认。本轮只确认渠道，不声称 OpenClaw 实装兼容。[发布](https://github.com/openclaw/clawhub/blob/main/docs/publishing.md)、[CLI 与许可说明](https://github.com/openclaw/clawhub/blob/main/docs/cli.md) |
| Smithery Skills | **有独立 Skills 目录**，不是只有 MCP 市场。`q=icode`、pageSize100 本次返回 100 条，按 namespace/gitUrl 核对未见本仓库 | 提交为带 Bearer API key 的 `PUT /skills/{namespace}/{slug}`，提交公开 gitUrl；本轮不注册或写入。标准 GitHub 技能可作为后续候选，不代表已收录。[目录](https://smithery.ai/skills)、[提交 API](https://smithery.ai/docs/api-reference/skills/create-or-update-a-skill) |
| Glama | 本次确认其 MCP 服务器/连接器/工具目录，未获得独立 Skill 注册表的官方证据 | 不把第三方 `search_skills` 工具等同平台技能市场，也不为收录额外制造 MCP 服务。[官网](https://glama.ai/)、[第三方工具条目](https://glama.ai/mcp/connectors/io.911fund.skills/directory/tools/search_skills) |

这些均是有限关键词结果，不是平台全库无条目的证明。复查入口：

- [腾讯 SkillHub：icode](https://api.skillhub.cn/api/skills?page=1&pageSize=24&keyword=icode)
- [ClawHub：icode](https://clawhub.ai/api/v1/search?q=icode&limit=10)
- [Smithery：icode](https://api.smithery.ai/skills?q=icode&pageSize=100)
- [Context7：icode](https://context7.com/api/v2/skills?query=icode)

按用户追加的多渠道接入要求，以上四个目录也纳入显式只读探针，保留其公开端点、schema 漂移和弃用边界，不引入平台 CLI 运行依赖。SkillHub 与 Smithery 的专用材料由离线工具生成草稿，见[目录接入与提交准备](skill-catalog-submission.md)。收录所需账号、实名、凭证、许可同意和人工审核不能靠仓库改动替代；不能为了“大部分能搜到”伪造完成状态。

公开查询证据（匿名只读，无安装或遥测事件）：

- SkillsMP `GET /api/v1/skills/search?q=ayukyo&limit=50`：HTTP 200，返回 `id=ayukyo-icode-skill-skill-md` 与正确 GitHub 来源；不能证明所有功能关键词都能匹配，也不能证明排名。
- skills.sh `GET /api/search?q=icode&owner=ayukyo&limit=20`：HTTP 200，`skills=[]`、`count=0`；正式 API 与此 CLI 查询入口的认证规则可能不同，不将匿名访问能力推广到所有接口。

SkillsMP FAQ 当前描述公开仓库、有效技能元数据及相关 topic 的自动抓取流程，手动提交仍为计划功能；因此已有条目不需要重复注册或投稿。内容刷新由目录控制，本站 push 或 IndexNow 不会强制刷新 Skill 目录。skills.sh 的安装遥测路径也不应嵌入 ICODE 安装器做隐式上报。[SkillsMP FAQ](https://skillsmp.com/docs/faq)、[skills.sh FAQ](https://skills.sh/docs/faq)

## 本轮已实施的可发现性基础

在上述只读调研后，按“缺口直接优化”的授权补齐以下入口；没有改变正式宿主范围：

| 改进 | 直接作用 | 不代表什么 |
| --- | --- | --- |
| 根 `SKILL.md` 双语 description | 在加载正文前暴露真实用途、Claude/Codex/CodeBuddy 名称和点名/续接边界 | 不让普通请求自动转入 ICODE；不保证宿主模型选中 |
| `agents/openai.yaml` | Codex 名称、短说明与显式 `$icode` 默认提示 | 不改变依赖、权限或调用政策 |
| 官网 `llms.txt` 与双语 `index.md` | 从单一文案生成精简索引及完整可读页面；HTML 提供标准链接关系 | 不是各 Agent 必须读取的标准，不保证被引用 |
| 无脚本 `SoftwareSourceCode` microdata | 明确可见名称、源码地址、版本、用途、MIT 许可证 | 不伪造评分、安装量或新宿主认证 |
| [三宿主分发原型](skill-distribution.md) | 从单一源码生成完整工作流与共享技能包、薄插件清单、SHA-256 摘要 | 尚未插件实装、上架或加入用户默认市场 |
| `tools/check_skill_discovery.py` | 覆盖 SkillsMP、skills.sh、Context7、SkillHub、ClawHub、Smithery；默认离线，显式联网后得到可复查的有限查询 JSON | 不提交、不安装、不刷遥测；同名未核实来源不算收录，错误不当成未命中 |
| `tools/prepare_skill_listing.py` | 生成 SkillHub 专用元数据及 Smithery 提交请求的可审查草稿，版本跟随根技能 | 不读凭据、不联网、不写入、不重新许可，不等于完整平台包或发布成功 |

2026-09-20 追加关键词抽查：两个目录分别查询 `icode`、`AI coding workflow`、`code review`、`工单`，各取最多 50 条，均未在返回结果中命中本仓库。独立作者词 `ayukyo` 在 SkillsMP 返回 5 条、本技能位于该响应第 1 项；skills.sh 返回 0 条。**已收录与泛关键词容易被推荐仍有明显差距**，不能用作者词命中掩盖这一点。上述查询发生在本轮新元数据推送前；后续应给予平台正常刷新时间，不反复刷请求。

`llms.txt` 使用[路径级阅读索引提案](https://llmstxt.org/)，源码标记使用 [SoftwareSourceCode](https://schema.org/SoftwareSourceCode)。它们提高内容可读取性，不是排名技巧；[Google 官方说明](https://developers.google.com/search/docs/appearance/ai-features)明确 AI 搜索不要求新增专属文件或标记，索引和展示仍由搜索系统决定。

## 后续市场接入边界

1. **先做好被发现后的可靠安装。** 保留单一 ICODE 真源、清楚的中英文用途与停止边界；增加版本化的“发现 / 安装 / 运行”兼容矩阵。优先复核已有 CodeBuddy 契约漂移，保护原 Claude/Codex 安装。不要先堆十几个未验证的宿主图标。
2. **再做完整插件发行原型。** 优先覆盖已有宿主的分发路径，再选 Cursor/Copilot 或 Gemini/OpenCode 做隔离试点。复用安装清单，明确共享技能、MCP、DOCX runtime、命令入口、插件命名空间和旧安装去重；不把只复制根 SKILL 当完整安装。插件公共上架仍可能需要账号、提交和审核。
3. **最后按需增加“只读查找 Skill”能力。** 如果希望 ICODE 帮用户寻找其它技能，可复用 find-skills 或受控目录查询；推荐须包含来源、更新时间、宿主/依赖与风险，安装另行确认。该功能与“让别人搜到 ICODE”不同，不是本轮宣传需求的前置条件。

新宿主的最低验收应覆盖：隔离用户目录的 install/dry-run/重复安装、完整资源与共享技能、缺 MCP 的显式降级、实际 help/plan/verify 等流程、失败与停止点、旧宿主回归、Runtime 支持范围。当前 Runtime runner 仅支持 Claude/Codex，不能随技能目录兼容性一起扩张为全部宿主支持。

本轮官网仍只展示现有三宿主；只增强技能发现描述，不修改正文执行语义、安装器、Runtime、工单记录或真实全局设置。新宿主实装、插件公开发布或市场申请仍需相应环境与授权；此报告不构成它们已完成的承诺。
