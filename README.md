# ICode — 全流程编码工作流（步骤 0 + 1~6）

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为严格步骤，每步可单独调用，手动切换模型时操作更灵活。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 特性

- **(可选) 需求初稿 → 计划 → 审查 → 定稿 → 编码 → 复检 → 终审**，闭环交付
- 每步可独立调用，使用当前会话模型
- 全流程模式（`/icode start`）自动串联步骤 1→6
- 所有步骤在主会话中执行，无子 Agent 隔离问题
- 产物保存在 `.icode_output_N/` 目录，支持跨会话恢复
- 元信息管理（`.ico_metadata.json`），记录执行状态与代码文件
- 三阶段递进复检（Reverse → Fixed → Free），逆推视角防偷懒复读
- Plan 步骤强制断言验证（Read/Grep 实证，`[已验证]`/`[未验证]` 标记）
- 架构决策记录（ADR）章节，集中管理方案选型与取舍
- Review 支持指定轮数（`/icode review [N]`）和增量审查模式
- Review 意见结构化输出（影响章节/建议/否决风险）
- **（v1.4.0 新增）** `/icode init` 多轮对话产出需求初稿 `00_init.md`，每轮增量更新；`/icode start`/`/icode plan` 自动检测并复用该目录作为需求输入

## 安装

将本仓库克隆到 Claude Code skills 目录：

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## 快速开始

```bash
# 一步走完全流程
/icode start 实现MCU雨量传感器I2C驱动

# 或者分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1：拟定计划
/icode review                          # 步骤2：专项审查（默认3轮）
/icode review 5                        # 步骤2：指定5轮审查
/icode merge                           # 步骤3：合并定稿
/icode code                            # 步骤4：编码实施
/icode deepcheck                       # 步骤5：循环复检
/icode audit                           # 步骤6：终极终审

# 需求不明确时，先讨论再进入流程
/icode init 录制 point_cloud / lidar_imu 转包      # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 复用同一目录，把 00_init.md 作为需求输入，进入步骤1→6
```

## 命令一览

| 命令 | 功能 | 创建目录 |
| ---- | ---- | -------- |
| `/icode help` | 帮助：输出使用流程示例 | 否 |
| `/icode init [<粗略需求>]` | 可选步骤0：多轮对话产出需求初稿 `00_init.md`（每次调用都新建目录） | ✅ 每次都新建 |
| `/icode start <需求>` | 全流程：创建/复用目录 → 步骤1→6 | ✅ / 复用 |
| `/icode plan <需求>` | 仅步骤1：拟定项目计划 | ✅ / 复用 |
| `/icode review [N]` | 仅步骤2：专项审查计划（N=轮数，默认3） | 否 |
| `/icode merge` | 仅步骤3：合并审查意见定稿 | 否 |
| `/icode code` | 仅步骤4：落地编码实施 | 否 |
| `/icode deepcheck` | 仅步骤5：无限轮循环复检 | 否 |
| `/icode audit` | 仅步骤6：终极终审 + 统一修复 | 否 |

> `/icode start` / `/icode plan` 启动时若发现最新 `.icode_output_N/` 只有 `00_init.md`（步骤0产物），自动**复用该目录**并以 `00_init.md` 为需求输入；否则按常规创建新目录。

## 执行方式

所有步骤在主会话中执行，使用当前会话模型。不主动切换，用户可自行 `/model`。

## 目录结构

```text
.icode_output_N/
├── .ico_metadata.json      # 元信息（状态、代码文件列表）
├── 00_init.md              # 步骤0（可选）：需求初稿（每轮对话增量更新）
├── 01_plan.md              # 步骤1：项目计划
├── 02_review.md            # 步骤2：审查报告
├── review_round_*.json     # 步骤2：各轮审查详情（JSON）
├── 03_plan_final.md        # 步骤3：定稿计划
├── 05_reverse.json         # 步骤5：逆推规格（单条 JSON）
├── 05_review_rounds.json   # 步骤5：复检轮次记录（JSONL）
├── 06_audit.md             # 步骤6：终审报告
└── 06_fixes.log            # 步骤6：修复日志
```

## 工作流程

```text
[步骤0(可选)] 需求初稿对话
       ↓
[步骤1] 拟定计划 → [步骤2] 专项审查 → [步骤3] 合并定稿
                                              ↓
[步骤6] 终审修复 ← [步骤5] 循环复检 ← [步骤4] 编码实施
```

## 版本

当前版本：v1.4.0

### What's New（v1.4.0）

- **新增可选步骤 0：`/icode init`** —— 在需求不明确时，先与 AI 多轮对话讨论，每轮自动增量更新 `00_init.md`，文档始终保持完整结构（背景 / 现状 / 新增需求 / 影响面 / 待决策项）
- **`/icode init` 即"新开"**：每次调用都创建一个全新目录，不复用、不续聊。想继续上次讨论 → 直接对话即可，AI 会自主识别并增量更新文档
- **支持两种入参形态**：`/icode init <粗略需求>` 起一稿后进入对话；`/icode init` 空模板进入对话
- **`/icode start`/`/icode plan` 智能识别**：当最新目录仅含 `00_init.md` 时，自动复用并以其为需求输入（命令行参数仅作补充上下文）
- **步骤0独立、不自动串联**：等用户讨论充分后显式运行 `/icode start`/`/icode plan` 才进入步骤1

详细说明请见 [SKILL.md](SKILL.md)。

## 许可证

MIT
