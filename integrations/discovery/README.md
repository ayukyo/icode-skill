# ICODE 分发接入原型

此目录存放清单模板，**它本身不是可安装插件**。运行仓库的
`python3 -B tools/build_skill_distribution.py --output /新建临时父目录/icode`
生成包含实际资源的插件目录。清单版本从根 `SKILL.md` 提取；不能把本目录单独提交给市场。

生成包同时带有 `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、
`.codebuddy-plugin/plugin.json`，共用 `skills/` 下的一份工作流和共享技能。
源码、相对引用、共享技能模板均来自同一 checkout；`SHA256SUMS` 记录包内文件摘要。
生成包不是已上架产品，也不代表任一宿主已实装通过。

## 使用与依赖边界

- 根工作流位于 `skills/icode/SKILL.md`；共享技能位于其同级目录。
- Claude 插件技能按官方规则使用 `/icode:icode`，不能承诺原 `/icode` 别名。
  Codex、CodeBuddy 的实际入口与共享技能名，以安装后宿主列出的限定名称为准。
  未验证裸名称路由、同名旧安装的优先级或共存；不要自动删除旧安装。
- 自包含指源码与资源自包含。此包没有自动执行的 Hook、MCP 注册配置、依赖下载、凭证或账号。
  Python 工具、DOCX runtime、Node/外部命令及 MCP 仍有独立运行前提。
- 完整安装继续使用**原源码 checkout** 的 `install.sh --client claude|codex|codebuddy`。
  包内保留的 `install.sh` 是资源原样快照，不应直接从插件缓存运行：旧同步脚本存在 Git、
  全局目标及宿主环境约定。`--skip-mcp` 仍会准备 DOCX runtime，并非纯复制。
- 只在已授权的后续宿主试验中配置依赖。不要因“已发现技能”推断 MCP、工作流门禁或真实任务已通过。
- 共享技能可能引用额外的外部技能；包内完整性范围是 `skill-packs/manifest.json` 声明的集合，
  不是安装用户的全部个人技能。外部技能不可用时记录缺口，遵循原路由降级合同。

更详细的官方依据、构建命令、测试范围见源码仓库 `docs/skill-distribution.md`
（生成包内为 `skills/icode/docs/skill-distribution.md`）。
