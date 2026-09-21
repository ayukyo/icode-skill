# WorkBuddy 接入与上架边界

核查日期：2026-09-21。状态：**官方兼容路径已调研；ICODE 专用安装目标、宿主端到端验证和市场上架未完成**。

## 可以复用什么

WorkBuddy 的[项目配置说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)描述代码开发场景复用 `.codebuddy/`，包括项目技能、命令、规则和 Agent。因此 ICODE 应继续复用单一工作流源码，不为 WorkBuddy 另写一套步骤。

但配置复用不是所有路径都相同：

| 层次 | 已核对事实 | ICODE 接入要求 |
| --- | --- | --- |
| 技能/命令 | 项目配置文档列出 `.codebuddy/skills/<name>/SKILL.md`、`.codebuddy/commands/` | 检查完整资源、共享技能生成与实际加载；不能只复制根 SKILL |
| MCP | [专用 MCP 文档](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)列出用户 `~/.workbuddy/mcp.json` 与项目 `.workbuddy/mcp.json` | 以当前产品版本和界面显示的有效配置为准；不能假定写 CodeBuddy MCP 路径就一定生效 |
| 市场 | 官方提供[技能市场](https://www.workbuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)；SkillHub 有独立发布/审核流程 | 获得真实条目后，还要在目标 WorkBuddy 版本中搜索、安装和运行 |
| Runtime | 当前 ICODE runner 为 Claude/Codex | 不把技能可读取等同新增模型执行后端；本轮不改 UI/runner |

当前 `install.sh` 的 `--client` 只有 `claude`、`codex`、`codebuddy`、`all`，**没有 `--client workbuddy`**。CodeBuddy 安装中的共享技能路径与命令桥还需在 WorkBuddy 实机确认；不要运行不存在的参数，或用重命名宿主绕过校验。

同日补充：[完整包安装验证](host-loading-validation.md)已通过官方 Skills CLI 的
`--agent codebuddy --copy` 路线，将17个技能及344项技能文件安装到独立项目的
`.codebuddy/skills/`，首次与重复安装摘要均一致。该路径可用于后续 WorkBuddy 项目接入试点，
但本轮没有运行 WorkBuddy 本体，也不自动写入其 MCP 配置、命令桥或宿主信任设置。

## 分发容量与许可边界

含 8 套现有 PPT 模板的本地归档约 18 MiB；保留资源与原版本逐字节一致，8 套均通过单页文字替换生成测试。这些仅是本地资源和体积验证，不能当作平台接受、线上归档已更新或发布成功的证据。

2026-09-21 核查时，SkillHub GitHub 导入预检曾提示仓库归档超过 20 MiB；本地上传入口另限 200 个文件、10 MB。这是该次预检的容量边界，不代表约 18 MiB 的本地归档已被平台验证；完整包必须按对应入口重新验收。

此外，`tools/ppt/templates/` 的第三方模板/预览受 [NOTICE](../tools/ppt/NOTICE)约束，不能把整包声明为统一 MIT。模板数量缩减不改变其原有许可；个人学习与非商业研究无需以购买商业授权为前置。“无可见水印”不是许可类别，免费发布也不能宣称整包不受第三方限制。需继续核对平台是否接受该素材范围和声明，不能上传缺依赖的占位技能。

现有步骤会在缺少内置模板时失败，“原创版式”尚无现成生产实现。无模板分发需先补入口与原创资源，且进一步检查文件数，不能仅删文件后宣称兼容。

## 最低验收

1. 市场包来源固定、许可证和第三方声明完整；文件数/大小通过对应入口预检，发布元数据版本与根技能一致。
2. 隔离目录验证完整步骤、工具、schema、gate 与共享技能；不得依赖开发者机器已有的私有路径。
3. 在实际 WorkBuddy 中确认技能被发现，help、只规划、恢复工单和不隐式编译/部署的 verify 行为正确。
4. MCP 已配置和缺失两种情况都检查；缺失时明确降级，不假装门禁已调用。
5. 重复安装/已有 CodeBuddy 共存不覆盖用户设置；Claude Code、Codex、CodeBuddy 既有测试保持通过。
6. 获得提交回执、审核状态和真实条目 URL 后再宣布上架；WorkBuddy 市场可见与 SkillHub 审核分别记录。

当前没有执行 WorkBuddy 宿主实机测试，也没有发布成功回执。更多渠道的公开技术边界见[发现说明](agent-skill-discovery.md)。
