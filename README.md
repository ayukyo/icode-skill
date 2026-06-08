# ICode — 六步全流程编码工作流

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为 6 个严格步骤，每步可单独调用，手动切换模型时操作更灵活。

## 特性

- **计划 → 审查 → 定稿 → 编码 → 复检 → 终审**，六步闭环
- 每步可独立调用，使用当前会话模型
- 全流程模式（`/icode new`）自动串联 6 步
- 所有步骤在主会话中执行，无子 Agent 隔离问题
- 产物保存在 `.icode_output_N/` 目录，支持跨会话恢复
- 元信息管理（`.ico_metadata.json`），记录执行状态与代码文件
- 三阶段递进复检（Reverse → Fixed → Free），逆推视角防偷懒复读
- Plan 步骤强制断言验证（Read/Grep 实证，`[已验证]`/`[未验证]` 标记）
- 架构决策记录（ADR）章节，集中管理方案选型与取舍
- Review 支持指定轮数（`/icode review [N]`）和增量审查模式
- Review 意见结构化输出（影响章节/建议/否决风险）

## 安装

将本仓库克隆到 Claude Code skills 目录：

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## 快速开始

```bash
# 一步走得全流程
/icode new 实现MCU雨量传感器I2C驱动

# 或者分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1：拟定计划
/icode review                          # 步骤2：专项审查（默认3轮）
/icode review 5                        # 步骤2：指定5轮审查
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
| `/icode review [N]` | 仅步骤2：专项审查计划（N=轮数，默认3） | 否 |
| `/icode merge` | 仅步骤3：合并审查意见定稿 | 否 |
| `/icode code` | 仅步骤4：落地编码实施 | 否 |
| `/icode deepcheck` | 仅步骤5：无限轮循环复检 | 否 |
| `/icode audit` | 仅步骤6：终极终审 + 统一修复 | 否 |

## 执行方式

所有 6 个步骤在主会话中执行，使用当前会话模型。不主动切换，用户可自行 `/model`。

## 目录结构

```
.icode_output_N/
├── .ico_metadata.json      # 元信息（状态、代码文件列表）
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

```
[步骤1] 拟定计划 → [步骤2] 专项审查 → [步骤3] 合并定稿
                                              ↓
[步骤6] 终审修复 ← [步骤5] 循环复检 ← [步骤4] 编码实施
```

## 版本

当前版本：v1.3.0

详细说明请见 [SKILL.md](SKILL.md)。

## 许可证

MIT
