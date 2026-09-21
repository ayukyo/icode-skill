# Claude Code / Codex / CodeBuddy 宿主适配契约

共享 SKILL 描述“要完成的操作”，ICODE 在运行时映射到当前宿主可用工具。方法论只有一份，工具语法不进入共享技能正文。

| 抽象操作 | Claude Code 适配 | Codex 适配 | CodeBuddy 适配 | 无能力时 |
|---|---|---|---|---|
| 读文件/文本检索 | Read、Grep、Glob 或 shell `rg` | 文件读取工具或 shell `rg` | `read_file` / `search_content` / `search_file` 或 shell `rg` | 使用当前宿主最窄只读命令 |
| 结构化思考 | 已注册的 sequential-thinking MCP | 当前可调用的 sequential-thinking MCP | 依据本会话实际暴露的工具；有工具发现接口才调用，无则检查直接工具列表，配置读取走 `~/.codebuddy/mcp.json` | 按项目/步骤允许的降级路径处理；上位规则要求停下时不得擅自替代 |
| 独立审查 | 后台 Agent + 有界等待 | collaboration 子代理 + 有界等待 | 按实际版本的可用 Agent 类型选择；当前 CLI 文档已有 general-purpose/Plan/Explore，不能一律按“仅检索型”处理 | 标记无独立审查环境；主代理复核不冒充独立裁决 |
| 外部文档/媒体 | 当前已注册 MCP；native 按路由上限串行分批 | 当前已注册 MCP 或本地 CLI；native 按路由上限串行分批 | 当前已注册 MCP（同 `{mcpServers}` 结构）；无 MCP 时走文本优先 + 未观测边界 | 声明降级，保留未观测边界 |
| 邮件/线程/附件 | 显式网页邮件链接优先已登录浏览器；导出件走离线 intake；无人值守才用邮箱观察器 | 显式网页邮件链接优先当前已登录浏览器；导出件走离线 intake；无人值守才用邮箱观察器 | 同左（导出件走 `tools/email_intake.py` 离线 intake） | 请求限定范围的 `.eml`/`.msg` 导出；保持线程和附件缺口 |
| 文件修改 | 宿主提供的精确编辑工具 | `apply_patch` | `write_to_file` / `replace_in_file` | 停止修改，不用不安全覆盖命令替代 |

## 自然语言入口适配

三个宿主共用 `SKILL.md` 的发现描述和 [natural_language_entry.md](natural_language_entry.md) 的意图路由。用户点名 ICODE 后由当前主会话模型理解目标、限制和工单上下文；不要求 Hook、新增 MCP 或额外模型 API。已有命令仍有效，CodeBuddy 的自定义命令桥同时接受 `/icode <自然语言>`，不另设一份路由规则。

技能文件已安装不等于当前旧会话已经重新加载。宿主未自动选中技能时，让用户显式选择 ICODE 技能或走已有 `/icode` 入口；升级后需按宿主机制重新加载或新开会话。不能把源文件/安装副本一致、路由情境自检通过，表述为三个宿主的新会话自动触发均已实测。

此入口不扩展 Runtime/UI 的受控动作集合。UI 仍先选工单与动作，自由文本只补充该动作；若意图与已选动作/工单冲突，先纠正选择，不能从 note 绕过 action-policy、revision 或执行根检查。

## CodeBuddy 专项约定

1. **命令入口**：当前 CodeBuddy CLI 文档支持 `/skill-name` 和 `user-invocable`；IDE、旧 CLI 和插件命名空间需分别验证。ICODE 现有 `/icode <子命令>` 自定义命令桥继续保留，不因文档更新自动删除。仓库真源为 `integrations/codebuddy/commands/icode.md`；`scripts/sync-to-global.sh --apply --client codebuddy` 将其原子发布为用户级 `~/.codebuddy/commands/icode.md`。同内容旧文件可通过所有权标记接管，不同内容的未托管命令必须失败关闭；项目级 `<工程>/.codebuddy/commands/icode.md` 仍由项目自行管理。新版本需检查技能/命令同名时的实际优先级。
2. **MCP 配置位置**：CodeBuddy 配置使用 `~/.codebuddy/mcp.json`（**不是** `~/.claude.json`），常见条目为 `{mcpServers:{name:{command,args,env}}}`。相同 JSON 结构不代表跨宿主路径、凭据、信任和可执行文件一定有效；仅经授权的安装器管理，不盲目整段复制用户配置。
3. **工具发现**：以当前会话能力为准。没有 ToolSearch 时查看直接暴露的工具，不能因 ToolSearch 缺失判“工具不存在”；有工具发现接口时使用它。注册配置只是配置证据，不证明本会话可调用。
4. **Hook 层**：当前官方文档有全局 Hook 和受版本/信任约束的 fork Skill Hook；不能笼统写“不支持”。ICODE 不依赖这些新增能力、不自动启用 Hook、不打开 `allowUntrustedFrontmatterHooks`，仍由既有 Prompt、步骤与机器门禁执行约束。
5. **推理预算控制**：`CLAUDE_CODE_EFFORT_LEVEL` / `model=opus` 对 CodeBuddy 无效，不依赖其调节思考深度。
6. **Skill 目录**：CodeBuddy 会扫描 `~/.claude/skills/`（与 Claude Code 共用），也可放项目级 `.codebuddy/skills/`。全局安装只维护共享根中的一份 ICODE，不复制到第三个全局技能目录。
7. **同步边界**：普通 Skill 同步只发布 ICODE、共享 Skill 和命令桥，绝不改 `~/.codebuddy/mcp.json`；MCP 注册/卸载只走 `mcp/install.sh` / `mcp/uninstall.sh`。`--client all` 仅在检测到 `~/.codebuddy/` 时追加 CodeBuddy，保护原有 Claude+Codex 使用方式。

2026-09-21 官方合同核对：[Skills 与 Hook](https://www.codebuddy.ai/docs/cli/skills)、[子代理类型](https://www.codebuddy.ai/docs/cli/sub-agents)。这些是能力存在的文档依据，不代替当前用户安装版本的运行验证。

## WorkBuddy 与其它宿主

WorkBuddy 的代码开发场景有 `.codebuddy/` 兼容路径，但不能把 CodeBuddy CLI 的全部工具、Hook 或配置位置直接外推给它。其专用 MCP 文档列出 `.workbuddy/mcp.json`；实际技能目录、命令优先级、MCP 生效和权限须按产品版本检查，见 [WorkBuddy 接入说明](../docs/workbuddy-support.md)。目前没有 `--client workbuddy` 或 WorkBuddy Runtime runner，不伪造这两个入口。

Cursor、Copilot、Gemini CLI、OpenCode 等宿主按[发现矩阵](../docs/agent-skill-discovery.md)分别验证：目录能读只是第一层，还需完整依赖、命令命名空间、权限、MCP、恢复与停止点。统一复用工作流，不复制宿主专属步骤，不把插件清单解析通过说成全流程兼容。现有 Claude/Codex/CodeBuddy 安装和 Runtime 支持范围不变。

## 共同约束

1. 先探测能力，再选择适配；禁止假定某个 MCP、Agent 或命令一定存在。
2. 相同抽象输入必须产出相同字段语义，不能因宿主不同改变事实/推断判定标准。
3. 子代理只提供候选或独立裁决；主代理负责证据复核、范围控制和最终结论。
4. 宿主降级只降低自动化程度，不降低证据要求；无法验证就保留 `unobserved/inconclusive`。
5. native/bridge/dual 的图片、关键帧、页面和 tile 必须服从 `media_routing.md` 输出的单消息图片上限。超限时串行分批，每批先转成文本结果，再做纯文本聚合；禁止并行媒体调用造成宿主把多批图片重新塞入同一消息。
6. For explicit webmail URLs use the already logged-in browser first：只读取用户提供链接对应的邮件阅读窗，不把收件箱列表、搜索结果、智能回信或其他邮件带入上下文。完整分析范围允许把网页提供的“下载邮件”和指定附件保存到受控证据根；不得点击正文外链或回复、转发、移动、删除、标记、规则等邮箱操作。
7. 浏览器优先复用用户已打开的目标邮件。若自动导航可能产生未读→已读副作用且宿主不能阻断，则要求用户打开目标链接后继续；邮箱观察器路径必须保持 read-only + `BODY.PEEK`。网页链接无需 IMAP 配置，IMAP 仅为无人值守、邮箱范围搜索或用户显式选择的可选适配器。
