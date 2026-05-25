# ICode — 六步全流程编码工作流

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为 6 个严格步骤，每步可单独调用，方便在步骤间切换模型。

## 特性

- **计划 → 审查 → 定稿 → 编码 → 复检 → 终审**，六步闭环
- 每步可独立调用，步骤间可切换不同模型
- 全流程模式（`/icode new`）自动切换最优模型
- 产物保存在 `.icode_output_N/` 目录，支持跨会话恢复
- 元信息管理（`.ico_metadata.json`），记录执行状态与代码文件
- 双阶段递进复检（Fixed → Free），防止 AI 偷懒复读

## 安装

将本仓库克隆到 Claude Code skills 目录：

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## 快速开始

```bash
# 一步走完全流程（自动切换模型）
/icode new 实现MCU雨量传感器I2C驱动

# 或者分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1：拟定计划
/icode review                          # 步骤2：专项审查
/icode merge                           # 步骤3：合并定稿
/icode code                            # 步骤4：编码实施
/icode deepcheck                       # 步骤5：循环复检
/icode audit                           # 步骤6：终极终审
```

## 命令一览

| 命令 | 功能 | 创建目录 |
|------|------|----------|
| `/icode help` | 帮助：输出使用流程示例 | 否 |
| `/icode new <需求>` | 全流程：创建目录 → 步骤1→6 | ✅ |
| `/icode plan <需求>` | 仅步骤1：拟定项目计划 | ✅ |
| `/icode review` | 仅步骤2：专项审查计划 | 否 |
| `/icode merge` | 仅步骤3：合并审查意见定稿 | 否 |
| `/icode code` | 仅步骤4：落地编码实施 | 否 |
| `/icode deepcheck` | 仅步骤5：无限轮循环复检 | 否 |
| `/icode audit` | 仅步骤6：终极终审 + 统一修复 | 否 |

## 全流程模型分配

`/icode new` 全流程模式下，各步骤使用以下模型：

| 步骤 | 功能 | 模型 | 说明 |
| --- | --- | --- | --- |
| 1 | 拟定计划 | 主会话 | 在主会话中执行，保留需求上下文 |
| 2 | 专项审查 | sonnet | 子 Agent 执行 |
| 3 | 合并定稿 | opus | 子 Agent 执行 |
| 4 | 编码实施 | opus | 子 Agent 执行 |
| 5 | 循环复检 | sonnet | 子 Agent 执行 |
| 6 | 终极终审 | opus | 子 Agent 执行 |

- **步骤1** 在主会话中直接执行，可复用对话中的全部需求讨论
- **步骤2-6** 使用子 Agent 执行，便于切换模型和隔离上下文
- 分步模式下，每步使用当前会话模型，由你自行决定

## 目录结构

```
.icode_output_N/
├── .ico_metadata.json      # 元信息（状态、代码文件列表）
├── 01_plan.md              # 步骤1：项目计划
├── 02_review.md            # 步骤2：审查报告
├── 03_plan_final.md        # 步骤3：定稿计划
├── 05_review_rounds.json   # 步骤5：复检轮次记录（JSONL）
├── 06_audit.md             # 步骤6：终审报告
└── 06_fixes.log            # 步骤6：修复日志
```

## 工作流程

```
[步骤1] 拟定计划 → [步骤2] 专项审查 → [步骤3] 合并定稿
                                              ↓
[步骤6] 终审修复 ← [步骤5] 循环复检 ← [步骤4] 编码实施
```

## 版本

当前版本：v1.2.0

详细说明请见 [SKILL.md](SKILL.md)。

## 许可证

MIT
