# Claude Code / Codex / CodeBuddy 宿主适配契约

共享 SKILL 描述“要完成的操作”，ICODE 在运行时映射到当前宿主可用工具。方法论只有一份，工具语法不进入共享技能正文。

| 抽象操作 | Claude Code 适配 | Codex 适配 | CodeBuddy 适配 | 无能力时 |
|---|---|---|---|---|
| 读文件/文本检索 | Read、Grep、Glob 或 shell `rg` | 文件读取工具或 shell `rg` | `read_file` / `search_content` / `search_file` 或 shell `rg` | 使用当前宿主最窄只读命令 |
| 结构化思考 | 已注册的 sequential-thinking MCP | 当前可调用的 sequential-thinking MCP | 已注册 sequential-thinking 时同左；**未提供 `ToolSearch` 能力**，配置读取走 `~/.codebuddy/mcp.json` | 输出显式结构化思考块 |
| 独立审查 | 后台 Agent + 有界等待 | collaboration 子代理 + 有界等待 | **仅提供检索型子代理（`code-explorer`），无可裁决型 general-purpose** → 对抗验证须主代理代行并显式标注 | 标记无独立审查环境，不由主代理伪装 |
| 外部文档/媒体 | 当前已注册 MCP；native 按路由上限串行分批 | 当前已注册 MCP 或本地 CLI；native 按路由上限串行分批 | 当前已注册 MCP（同 `{mcpServers}` 结构）；无 MCP 时走文本优先 + 未观测边界 | 声明降级，保留未观测边界 |
| 邮件/线程/附件 | 显式网页邮件链接优先已登录浏览器；导出件走离线 intake；无人值守才用邮箱观察器 | 显式网页邮件链接优先当前已登录浏览器；导出件走离线 intake；无人值守才用邮箱观察器 | 同左（导出件走 `tools/email_intake.py` 离线 intake） | 请求限定范围的 `.eml`/`.msg` 导出；保持线程和附件缺口 |
| 文件修改 | 宿主提供的精确编辑工具 | `apply_patch` | `write_to_file` / `replace_in_file` | 停止修改，不用不安全覆盖命令替代 |

## CodeBuddy 专项约定

1. **命令入口**：CodeBuddy 不把 Skill 自动注册为斜杠命令。`/icode <子命令>` 依赖**自定义斜杠指令**（用户级 `~/.codebuddy/commands/icode.md`，或项目级 `<工程>/.codebuddy/commands/icode.md`）；指令正文负责加载 `icode` skill 并把 `$ARGUMENTS` 作为子命令与参数传入。
2. **MCP 配置位置**：宿主配置读 `~/.codebuddy/mcp.json`（**不是** `~/.claude.json`）；其 `mcpServers` 与 Claude Code 结构同源（`{mcpServers:{name:{command,args,env}}}`），可整段迁移。
3. **无 ToolSearch**：MCP 可用性判定只能靠「工具列表直接可见」这一条路径，不得因 ToolSearch 缺失直接判“工具不存在”。
4. **无 Hook 层**：`UserPromptSubmit` 等 Hook 不可用，强制思考前置仅由 Prompt 层 + 步骤文件保证。
5. **推理预算控制**：`CLAUDE_CODE_EFFORT_LEVEL` / `model=opus` 对 CodeBuddy 无效，不依赖其调节思考深度。
6. **Skill 目录**：CodeBuddy 会扫描 `~/.claude/skills/`（与 Claude Code 共用），也可放项目级 `.codebuddy/skills/`。

## 共同约束

1. 先探测能力，再选择适配；禁止假定某个 MCP、Agent 或命令一定存在。
2. 相同抽象输入必须产出相同字段语义，不能因宿主不同改变事实/推断判定标准。
3. 子代理只提供候选或独立裁决；主代理负责证据复核、范围控制和最终结论。
4. 宿主降级只降低自动化程度，不降低证据要求；无法验证就保留 `unobserved/inconclusive`。
5. native/bridge/dual 的图片、关键帧、页面和 tile 必须服从 `media_routing.md` 输出的单消息图片上限。超限时串行分批，每批先转成文本结果，再做纯文本聚合；禁止并行媒体调用造成宿主把多批图片重新塞入同一消息。
6. For explicit webmail URLs use the already logged-in browser first：只读取用户提供链接对应的邮件阅读窗，不把收件箱列表、搜索结果、智能回信或其他邮件带入上下文。完整分析范围允许把网页提供的“下载邮件”和指定附件保存到受控证据根；不得点击正文外链或回复、转发、移动、删除、标记、规则等邮箱操作。
7. 浏览器优先复用用户已打开的目标邮件。若自动导航可能产生未读→已读副作用且宿主不能阻断，则要求用户打开目标链接后继续；邮箱观察器路径必须保持 read-only + `BODY.PEEK`。网页链接无需 IMAP 配置，IMAP 仅为无人值守、邮箱范围搜索或用户显式选择的可选适配器。
