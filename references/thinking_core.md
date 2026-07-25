# 强制思考前置——核心规则（每步必读）

> 本文件是 icode 所有步骤共享的「强制思考前置」**核心规则**，每步必读。
> 各步骤子项详见 [thinking_detail.md](thinking_detail.md)「各步骤思考子项」段，按需 Read 自身步骤对应小节（各 step 文件本已声明本步骤子项，主要作为速查）。
>
> 历史参考小节（init/plan/log/start 检索命中时）按 verdict 分流标注在 [thinking_detail.md](thinking_detail.md)「历史参考小节」段。

## 规则

每个步骤开始前，必须先 ultrathink 并完成结构化思考——这是不可跳过的硬性前置。思考环节不可整体跳过，但**执行载体分主备两档**：

- **首选**：调用 `sequential-thinking` MCP 工具（`mcp__sequential-thinking__sequentialthinking`），至少 3 步（步骤定义里另有要求除外，如至少 4~5 步），每步对应该步骤声明的子项之一。上下文能看到该 tool_call 记录即为合规证据。
- **降级**：若当前环境未配置该 MCP（`~/.claude.json` 的 `mcpServers` 与项目根 `.mcp.json` 均无 `sequential-thinking` server，或已配置但 ToolSearch 取不到/调用失败），则必须以显式的「结构化思考」文字块替代——在回复中先输出一个 `### 结构化思考` 块，逐项完成该步骤要求的子项（每项一小段，不可省略），再进入产出。该文字块即为合规证据。

> **判定 MCP 是否可用**（关键：MCP 工具为懒加载，**deferred 池 = 已配置可用**而非"暂不可用"，必须分两步判定）：
>
> **第一步·取可用证据**（任一即视为"已配置可用"，**必须**走首选路径，禁止据此走降级）：
>
> - **证据 A（强证据）**：Read `~/.claude.json` 的 `mcpServers` 或项目根 `.mcp.json` 含 `sequential-thinking` server
> - **证据 B（强证据，最易误判处）**：系统提示 deferred tools 列表中列出 `mcp__sequential-thinking__sequentialthinking`
>
> **第二步·首选路径执行**（拿到任一强证据后）：
>
> 1. ToolSearch 取 `mcp__sequential-thinking__sequentialthinking` schema 加载
> 2. 实际调用该工具，至少 3 步，每步对应该步骤声明的子项之一
> 3. 调用成功 → 完成思考；调用返回错误/超时 → 才能进入降级路径
>
> **禁止误判场景**（历史实测的踩坑模式，逐条禁止）：
>
> - ⛔ **看到 deferred 列表里有 `mcp__sequential-thinking__sequentialthinking` 却判定"不可用"** —— deferred 是懒加载就绪态，**正是首选路径的强证据**，绝不可据此走降级
> - ⛔ **未实际调用 ToolSearch 就判定"ToolSearch 无命中"** —— ToolSearch 是独立工具，必须实际调用得到返回（拿到 schema 或确认无该工具）才能下结论
> - ⛔ **未实际调用 `mcp__sequential-thinking__sequentialthinking` 就判定"调用失败"** —— 必须有真实的调用返回错误/超时证据
> - ⛔ **看到顶层工具列表里没有该 MCP 就判定"不可用"** —— 顶层看不到 ≡ deferred 池可见，是懒加载不是缺失
> - ⛔ **凭记忆推断未经 Read 配置文件判定"无 server"** —— 必须实际 Read `~/.claude.json`，未读到配置才能说"无 server"
>
> **降级路径的合法前置**（满足以下**任一组**才能走降级文字块）：
>
> **配置证据组**（必须**同时**成立）：
>
> 1. 已实际 Read `~/.claude.json` 的 `mcpServers` **与** 项目根 `.mcp.json`（若有）→ **都**无 `sequential-thinking` server
> 2. 系统提示 deferred tools 列表**未**列出 `mcp__sequential-thinking__sequentialthinking`
>
> **运行证据组**（满足任一即可）：
>
> 1. 已实际调用 ToolSearch 取 schema → 确认无命中
> 2. 已实际调用 `mcp__sequential-thinking__sequentialthinking` → 确认返回错误/超时
>
> 两组证据**任一组全部满足**才能合法走降级。**任一组都不满足即不可走降级**，必须坚持首选路径。
>
> **两种载体任选其一即可，但思考环节本身不可省略**——未呈现任一形式的思考证据，该步骤产出视为不合规。

## 通用流程（每步执行）

1. 输出 `ultrathink` 触发词（触发更长的内部推理 budget）
2. **显式 Read 本步骤引用的 references 文件**（每步必须重新 Read，同会话已读不豁免——显式Read是深度思考的前置仪式，凭记忆会降级思考质量），Read 后在回复中输出确认行 `📖 已 Read references/xxx.md` 作为合规证据
3. 完成结构化思考（MCP 优先，不可用则降级文字块），至少 3 步，每步对应该步骤声明的子项之一
4. 不得跳过思考直接产出——所有 Write/Edit 必须在思考证据之后

## 层级关系（API 层 / Hook 层 / Prompt 层 概览）

- API 层：`CLAUDE_CODE_EFFORT_LEVEL=max` + `model=opus`（控制推理 effort）
- Hook 层：`UserPromptSubmit` 拦截 `/icode` 命令，缺思考证据时注入提醒
- Prompt 层：SKILL.md「强制思考前置」段 + 本文件 + 各 step 文件声明的子项
