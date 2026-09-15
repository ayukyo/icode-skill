---
name: icode
description: ICODE 端到端编码工作流。用法 /icode <子命令> [参数]，支持 help、init、log、start、fast、plan、review、merge、code、deepcheck、audit、crosscheck、readme、patch、verify、doc、docx、ppt、ui、limit、learn、status、list、bak、worktree。
---

按 ICODE 端到端编码工作流处理本次请求。

用户输入：$ARGUMENTS

执行要求：

1. 若本会话尚未加载 ICODE 技能，先加载 `icode` skill；CodeBuddy 默认从 `~/.claude/skills/icode/SKILL.md` 读取它，并以此作为唯一路由入口。
2. 从用户输入中解析子命令与其余参数。子命令缺失时，按 SKILL.md 的路由语义处理；`/icode ui` 使用 ICODE 的 UI 默认入口。
3. 严格按 SKILL.md 与 `steps/` 目录下对应步骤文件执行；执行步骤前必须先读取对应步骤文件。
4. 正式工单产物写入当前工程的 `.icode_output/.icode_output_N/`；状态与事件一律经 `python3 ~/.claude/skills/icode/tools/icode_control.py` 控制面执行，禁止绕过控制面直写 metadata。`crosscheck` 是非工单例外：仅写 `.icode_output/.crosscheck/`，不得写目标工单或代码。
5. 遵守 Git 安全规则：禁止自动执行 `git commit`、`git push` 及危险操作。
6. 对用户的回复一律使用中文。
