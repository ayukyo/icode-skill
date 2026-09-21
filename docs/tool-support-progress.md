# ICODE 工具支持进度

此页是持续维护的当前台账，机制与历史查询见[发现渠道说明](agent-skill-discovery.md)。记录日期是最近核查时间，不是平台保证。唯一源码：[ayukyo/icode-skill](https://github.com/ayukyo/icode-skill)；官网：[ICODE](https://ayukyo.github.io/icode-skill/)。

**可搜索、已收录、安装完整、宿主运行通过、自动更新是独立状态。** 已发布不代表安装可用；根技能资源完整不代表共享技能、MCP、设备或每个模型已通过。待验和受阻不计作支持完成，也不保证推荐排名。

## 当前台账

日期 2026-09-21 的初始核查基线为源码 `1441fc6d91d544f3e097415fb9db278f71783715`；同日远程安装、Smithery 下载与 GitHub 索引追加核查使用 `0b7a3f49ea9187b5d33f20b83313ec09200587f9`。各次结果保留自身来源，不用新提交替换历史证据。

| 稳定 ID | 类型 | 发现 / 上架 | 安装 / 执行 / 评估 | Git 来源关联 | 更新机制 | 核查日期 | 阻塞与下一步 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-code` | 宿主 | 2.1.270真实初始化识别17技能；项目/插件入口均通过 | [禁网加载、重复启动、空项目反例](host-loading-validation.md)通过；模型执行待验 | 单一源码，由安装器分发 | 显式同步；不隐式更新全局目录 | 2026-09-21 | 继续验证实际步骤、共享技能调用、缺MCP降级与共存优先级；发现不等于执行 |
| `codex` | 宿主 | CLI 0.154.0真实app-server识别并启用17技能 | 重复扫描、入口移走/恢复后的刷新通过；桌面与模型执行待验 | 单一源码，由安装器分发 | 显式同步 | 2026-09-21 | 见[加载证据](host-loading-validation.md)；不把CLI加载当桌面、MCP或完整工单认证 |
| `codebuddy` | 宿主 | 已有安装目标及旧命令桥 | 完整17技能项目复制与重复安装通过；宿主加载/执行待验 | 单一源码，由安装器分发 | 显式同步 | 2026-09-21 | 验证命令优先级、信任与Hook条件；Skills CLI安装通过不代表本体运行 |
| `workbuddy` | 宿主 | [官方路径适配说明](workbuddy-support.md)已有 | 复用路径的17技能完整复制已验；WorkBuddy本体加载/执行待验 | 复用 ICODE 来源，不复制维护 | 沿用显式安装；市场更新待验 | 2026-09-21 | 使用已验证的codebuddy项目安装目标试点，不虚构独立参数；SkillHub仍受阻 |
| `find-skills` | 发现/安装 | 官方 CLI 查询 icode 未命中目标；直接来源可识别 | [隔离 GitHub 远程 copy 安装及旧→新刷新](find-skills-compatibility.md)通过，328项资源一致；宿主运行待验 | 锁文件为 github / ayukyo/icode-skill / main，前后hash变化 | update退出1且旧安装未损；显式add --copy完成0b7a3f49→6eb09d4 | 2026-09-21 | 保留有界超时、完整性校验和失败日志；不把显式刷新当自动更新通过，不启用遥测 |
| `skill-creator` | 创建/评估 | 创建/评估工具，不是推广目录 | 格式/证据检查通过；[追加两组36份文本复测](skill-creator-compatibility.md)，末组合同15符合/1问题/2边界，知识错误另列 | 绑定每轮源码提交、diff与输入摘要，保留上下文/评分口径变化 | 源码变更后重跑评估；无市场同步语义 | 2026-09-21 | 已修正工程根冲突、预检/示例绑定并补三态约束；不将局部补测合并全绿，真实工具行为与模型稳定性待验 |
| `skillsmp` | 市场/目录 | [已收录](https://skillsmp.com/creators/ayukyo/icode-skill/skill)，描述仍为旧版 | 最新完整安装待验 | 条目关联正确 GitHub 来源 | 平台抓取；刷新时间由平台控制 | 2026-09-21 | 关注旧参数描述刷新；作者词命中不代表用途词排名 |
| `skills-sh` | 市场/目录 | icode 有限查询未命中；[ICODE Pack已创建](https://skills.sh/p/qyTEI6OU23DzlcNx) | 只读导入通过；平台漏掉16个PPT二进制资源，非完整安装通过 | 只读关联公开 ICODE 仓库，不授予提交/推送 | Pack内来源为ayukyo/icode-skill@main；实际更新待验 | 2026-09-21 | Pack为不列入目录的分享链接，不保证搜索上榜；完整功能从GitHub安装 |
| `smithery` | 市场/目录 | [ICODE 条目已发布](https://smithery.ai/skills/ayukyo/icode) | 受阻：下载ZIP检出1项缺失、25项变化，包括16个二进制文件损坏 | 设置中已关联仓库根 URL，不是仅固定提交 | 文本仍有旧版差异；定时跟随更新未证实 | 2026-09-21 | 已提交[官方反馈 #816](https://github.com/arcadeai-labs/smithery-cli/issues/816)，注明网页下载而非已确认CLI缺陷；修复前从GitHub安装 |
| `agentskill-sh` | 市场/目录 | [条目已收录](https://agentskill.sh/@ayukyo/icode-skill)；导入回执 1 updated | 受阻：[实样检查](find-skills-compatibility.md)对比基线323项资源，311项缺失、1项内容不同 | 条目关联 GitHub 根仓库；contentSha 对应源码 SKILL 摘要短前缀，不是附加指令后载荷的完整摘要 | 提交页声明每日检查；实际刷新完整性待验 | 2026-09-21 | 缺 steps/references/tools 等；根 SKILL 注入 AUTO-REVIEW 静默评价上报指令，未执行；解决前不建议使用该载荷 |
| `skillhub` | 市场/目录 | 导入受阻，尚未发布 | 受阻：刷新仓库后重新导入，归档下载显示 Failed to fetch | 已绑定 GitHub；维护者昵称及简介已补公开源码链接；尚无发布条目 | 未发布，不承诺自动更新 | 2026-09-21 | [官方问题 #2](https://github.com/Tencent/skillhub/issues/2)；最新错误与早先仓库状态误判分别保留，另核实候选包限制 |
| `skillkit-io` | 市场/目录 | 已提交，页面提示待审核添加 | 完整安装待验 | 提交公开 GitHub 根 URL | 审核与自动刷新未证实 | 2026-09-21 | 等待目录条目，核对来源及资源；勿重复提交 |
| `context7-docs` | 文档索引 | [已索引且验证通过](https://context7.com/ayukyo/icode-skill)；新配置刷新94文件、2048片段 | crosscheck主题检索通过，返回源码引用及隔离/fresh规则；技能安装不适用 | 根仓库已关联，已补官网、用途和验证申请资料 | 仓库 context7.json 已被平台校验并刷新成功；不承诺每次 push 即重建 | 2026-09-21 | 保留工具执行证据边界；文档索引不等同 Skills 目录收录 |
| `context7-skills` | 市场/目录 | 有限查询未命中 | 待验；官方 CLI 已标记 skills 命令弃用 | 查询按目标来源核对 | 不新增已弃用 CLI 的长期依赖 | 2026-09-21 | 与文档索引分开跟踪，先确认继任渠道 |
| `clawhub` | 市场/目录 | 查询未命中；未发布 | 受阻：发布许可条件须与第三方素材逐项核对 | 尚无已发布关联 | 不适用，未发布 | 2026-09-21 | 不更改许可或用空包装冒充完整技能 |
| `github-skill-search` | 发现/安装 | Code Search 已命中根 SKILL.md（0b7a3f49）；gh skill search 尚未实测 | 网页代码索引通过；CLI搜索/安装待验 | 查询结果直接指向公开源码与提交 | GitHub 索引已从先前pending变为可查；时效无保证 | 2026-09-21 | 不把网页 Code Search 命中说成CLI技能搜索/安装通过或推荐排名 |
| `cursor` | 宿主 | 标准技能目录接入路线已有 | 官方CLI完整17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | [复现配方](host-loading-validation.md)；继续验证目标版本MCP、步骤停止点 |
| `gemini-cli` | 宿主 | 标准技能目录接入路线已有 | 官方CLI完整17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | 不虚构原install.sh专用目标；验证宿主权限和停止点 |
| `opencode` | 宿主 | V1/V2需分别适配 | 官方CLI完整17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | 选择具体版本做加载与调用验证，不混用两代配置 |
| `copilot` | 宿主 | 官方技能目录机制已有依据 | github-copilot目标17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | CLI/VS Code/云端分开验收；不等同gh搜索或GitHub网页索引 |
| `cline` | 宿主 | 官方技能目录与Skills CLI接入依据已有 | 官方CLI完整17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | 按具体版本验证共享技能调用、MCP与停止点 |
| `windsurf-cascade` | 宿主 | 官方文档已转向Devin Desktop/Cascade，见[宿主机制](agent-skill-discovery.md) | windsurf目标17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | 不扩推到全部Devin产品；技能加载与MCP市场分开验收 |
| `antigravity` | 宿主 | 官方标准技能目录机制已有依据 | 官方CLI完整17技能copy/重复安装通过；本体加载/执行待验 | 复用单一源码生成包 | local来源显式重建/重装 | 2026-09-21 | IDE/CLI/2.0分别验证，不把目录可读当插件上架 |
| `public-site` | 自有入口 | 官网、双语 README、llms.txt 已发布 | 既有构建/部署通过；索引排名不适用 | main push 触发构建，绑定源码提交 | GitHub Actions 自动部署；IndexNow 仅通知 | 2026-09-21 | 提交新改动后检查对应构建与线上版本，不将通知受理当收录 |

## 当前优先级

1. **可用性优先**：Smithery 下载包二进制损坏已反馈；agentskill.sh 实样检出311项缺失、1项内容不同，根 SKILL 注入 `AUTO-REVIEW` 静默评价上报指令（并非 ICODE 源码行为，未执行）。解决前引导从完整 GitHub 来源安装。SkillHub 等官方答复，不能靠继续删模板绕过未知条件。
2. **验证闭环**：find-skills 的GitHub远程完整安装与显式旧→新刷新已测，update新增Git trace确认本次首轮克隆在360秒上限终止、旧安装无损，不与先前子安装失败混因；skill-creator保留所有失败轮、上下文变化和局部补测。末组15项合同符合不代表全部事实正确；模型稳定性、正反触发与真实工具行为仍待验。离线通过、网络受阻和行为缺口分别记录。
3. **后续扩展**：10个官方CLI安装目标的完整包复制已通过，Codex/Claude真实加载已验证；继续补模型执行、共享技能调用/MCP降级以及其他宿主本体证据。复制、加载与执行不是同一层，不直接授予完整认证。
4. **维护现有条目**：SkillsMP 等待旧描述刷新；Smithery 跟踪损坏修复与同步；SkillKit.io 等待审核。Context7 仓库配置读取与问答检索已验证，不再列为未完成。不要重复提交或堆砌关键词。

## 2026-09-21 渠道复核与推广边界

- 六渠道对 `icode` 的有限查询仍未命中目标：SkillsMP、skills.sh、Smithery 各最多50条，Context7旧 Skills 接口20条，SkillHub与ClawHub返回0条。一次查询共6次HTTP请求；这不代表整个目录未收录，更不能推翻已核实的直接条目。
- Context7 验证表单已提交公开官网、GitHub来源、用途和特点，页面返回验证成功。根目录 [context7.json](https://github.com/ayukyo/icode-skill/blob/main/context7.json) 按[官方配置规则](https://context7.com/docs/library-owners)选择工作流文档，排除内部资料和PPT模板；后台已切为仓库管理，刷新后标题与描述已读取新值，日志确认94/94文件处理成功并校验main中的配置。crosscheck主题检索已返回正确来源与fresh/零回写约束。自动刷新由平台条件控制，不能与官网的 main push 自动部署混为一谈。
- Smithery 设置中的 Git URL 已正确关联仓库根，公开元数据为 listed；无需重复发布。实际 ZIP 完整性失败、样本摘要和二进制反例见[安装兼容记录](find-skills-compatibility.md)，已提交上述官方反馈。不要把“已关联”升级为可完整安装或同步成功。
- GitHub [定向 Code Search](https://github.com/search?q=repo%3Aayukyo%2Ficode-skill%20path%3ASKILL.md%20icode&type=code)已返回4个文件，其中包括提交0b7a3f49的根SKILL及其名称、描述。其余是匹配路径的模板，不能算4个已安装技能；本机尚无gh CLI实测。
- skills.sh 已按维护者授权连接 GitHub：应用仅只读代码和元数据，安装范围限定公开的 ayukyo/icode-skill，不开放提交/推送，不含私有仓库。导入识别icode并创建了上述Pack；创建回执明确排除8套PPT的16个preview.png/template.pptx文件（拒绝二进制或单文件超2MB）。原仓库模板未删除，Pack不能替代完整源码安装。当前页面只提供增删技能，未见名称/说明修改入口，限制在本台账明确保留，不以完整包推广。
- [Pack官方说明](https://www.skills.sh/docs/packs)：它是不列入目录、但持链接可访问的分享包，团队归属不代表访问控制；不把建包说成获得推荐。该页的更新说明也不证明本包已经跟随源码变化。[仓库展示配置](https://skills.sh/docs/customize)不会自动创建收录，暂不为单个技能堆分组。
- agentskill.sh 作者页声明每日同步，并提供 push webhook；当前安装载荷完整性问题尚未解决，不先扩大自动分发，也不执行平台附加的自动评价行为。以完整 GitHub 来源作为可靠安装入口。
- 新渠道筛选：`travisvn/awesome-claude-skills` 的贡献要求包含至少10颗星及不接受AI辅助PR，本仓库当前不满足，不提交；`VoltAgent/awesome-agent-skills` 要求实际社区采用证据，暂不把未验证推广成绩充作贡献资格。热门列表不等于无门槛广告位。
- SkillsMP 联系页仅提供社交联系，未见自助刷新入口；SkillKit.io 仍按既有待审核回执跟踪。没有新证据时不重复投递，不创建付费或新权限依赖。

## 证据与维护规则

### 剩余缺口如何关闭

| 缺口类别 | 涉及工具 | 关闭条件 / 当前可用路径 |
| --- | --- | --- |
| 安装载荷不完整或损坏 | Smithery、agentskill.sh、skills.sh Pack | 平台修复后重新对固定源码做全量资源校验；目前从GitHub完整来源安装，不减少模板或放宽检查 |
| 平台导入 / 审核 / 刷新 | SkillHub、SkillKit.io、SkillsMP | 获得成功回执并检查公开条目、来源、实际载荷；没有新信息不重复提交 |
| 发布许可条件不兼容 | ClawHub | 完整包许可满足发布条件后再评估；不删除必要署名或默认重新许可 |
| 宿主实际运行证据 | Claude Code、Codex、CodeBuddy、WorkBuddy及候选宿主 | 各自版本的隔离加载、共享技能/MCP、真实步骤与停止点通过；文本评估、路径适配和复制安装不能替代 |
| 模型行为稳定性 | skill-creator评估 | 保留原失败，分开检查路由、事实正确性和真实工具执行；不凭提示词修订或局部补测宣称全部解决 |
| 搜索与更新 | find-skills、GitHub技能搜索、目录排序 | 安装/升级、搜索命中、用途词排名分别取证；不给自动推荐或实时刷新承诺 |

这些条目是验收条件，不是自动监控任务；本台账不暗中创建定时任务、额外权限或全局宿主配置。

- 优先记录可公开的条目、官方 issue、测试命令、源码提交；不上传凭证、账号联系方式、登录截图或内部诊断记录。
- 同一次成功至少说明：工具/版本、日期、来源提交、检查范围、结果及未验证部分。网络超时记受阻，schema 漂移记探针错误，不写成“搜索无结果”。
- 只有默认分支变化后平台内容实际刷新，才把“自动更新待验”改成“实测通过”；仅填写 Git URL 不够。
- 维护者在增加工具、修复兼容性、发布/退回或重新实测时更新本页。此页不启动定时任务，也不暗中联网。
- 本页行结构由 `tests/test_tool_support_progress.py` 离线守护；最新测试输出与真实外部证据仍需人工核对。

## 新增工具模板

复制同类工具行，填入稳定 ID（小写短横线，后续不要随展示名改变）、类型、发现、执行、来源、更新、核查日期与下一步。类型使用：宿主、发现/安装、创建/评估、市场/目录、文档索引、自有入口。

详细证据可在同页另加小节，包含：官方入口与版本、安装资源范围、许可/权限要求、成功或失败复现命令、下一次验收标准。未知项明确写待验，首次录入不能直接标为支持完成。
