---
name: icode
description: ICODE 端到端编码工作流。支持 /icode <子命令> [参数] 或 /icode <自然语言目标与限制>；裸 /icode 显示帮助。覆盖开发、诊断、审查、验证、文档、学习与工单管理。
---

按 ICODE 端到端编码工作流处理本次请求。

用户输入：$ARGUMENTS

执行要求：

1. 若本会话尚未加载 ICODE 技能，先加载 `icode` skill；CodeBuddy 默认从 `~/.claude/skills/icode/SKILL.md` 读取它，并以此作为唯一路由入口。
2. 保留完整用户输入、否定条件和工单上下文。明确子命令按原路由；自然语言或混合描述先读技能中的 `references/natural_language_entry.md` 再选步骤，不能只按关键词猜。空输入或 help 只展示帮助，能力咨询不执行；`/icode ui` 使用 ICODE 的 UI 默认入口。
3. 严格按 SKILL.md 与 `steps/` 目录下对应步骤文件执行；执行步骤前必须先读取对应步骤文件。help 按上述入口参考文档提供帮助，不虚构 help 步骤文件。
4. 正式工单产物写入当前工程的 `.icode_output/.icode_output_N/`；状态与事件一律经 `python3 ~/.claude/skills/icode/tools/icode_control.py` 控制面执行，禁止绕过控制面直写 metadata。`crosscheck` 是非工单例外：仅写 `.icode_output/.crosscheck/`，不得写目标工单或代码。
5. 遵守 Git 安全规则：禁止自动执行 `git commit`、`git push` 及危险操作。
6. 对用户的回复一律使用中文。
