# ICode — 全流程编码工作流（步骤 0 + 1~6，含日志根因分析入口）

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为严格步骤，每步可单独调用，手动切换模型时操作更灵活。

- **入口命令（可选）**：`/icode log` 日志根因分析（领域无关）→ 转修复需求；`/icode init` 需求初稿对话
- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 特性

- **(可选) 需求初稿 → 计划 → 审查 → 定稿 → 编码 → 复检 → 终审**，闭环交付
- 每步可独立调用，使用当前会话模型
- 全流程模式（`/icode start`）自动串联步骤 1→6
- 所有步骤在主会话中执行，无子 Agent 隔离问题
- 产物保存在 `.icode_output/.icode_output_N/` 目录（统一收纳在 `.icode_output/` 父目录下），支持跨会话恢复
- 元信息管理（`.ico_metadata.json`），记录执行状态与代码文件
- 三阶段递进复检（Reverse → Fixed → Free），逆推视角防偷懒复读
- Plan 步骤强制断言验证（Read/Grep 实证，`[已验证]`/`[未验证]` 标记）
- 架构决策记录（ADR）章节，集中管理方案选型与取舍
- Review 支持指定轮数（`/icode review [N]`）和增量审查模式
- Review 意见结构化输出（影响章节/建议/否决风险/证据回指/对抗验证状态）
- Review 引入独立质疑者子代理对抗验证（证据质疑/替代解释/充分性三视角），未对抗或证据不足的 issue 不得确认；断言无法实证验证时诚实降级为 `[未验证-证据不足]`，不伪造共识
- 跨工程历史检索复用：`/icode init`/`/icode log`/`/icode plan`/`/icode start` 启动时自动检索全局索引（`~/.claude/icode_data/index.json`）中相似历史工单，按命令分流注入参考（init 注入需求要点 / log 注入根因结论+证据 / plan 注入 ADR+风险）；三道闸门防上下文撑爆；历史参考只进会话不污染工程产物
- `/icode log` 日志根因分析入口：先基线检查（git diff/状态链路图）再日志侦察，对抗分析（3质疑者）防确认偏误，诚实降级不编根因；产出 `log_analysis.md` + 自动转修复需求 `00_init.md` 衔接步骤1；领域无关（机器人/服务端/嵌入式/Web 均可）
- `/icode init` 多轮对话产出需求初稿 `00_init.md`，每轮增量更新；`/icode start`/`/icode plan`（无参）检测到 init/log 入口态时询问"复用/新建"，选复用则把 `00_init.md` 作为需求输入

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
/icode review                          # 步骤2：专项审查（软上限3轮，仍有问题时自动延长）
/icode review 5                        # 步骤2：指定5轮审查
/icode merge                           # 步骤3：合并定稿
/icode code                            # 步骤4：编码实施
/icode deepcheck                       # 步骤5：循环复检
/icode audit                           # 步骤6：终极终审

# 需求不明确时，先讨论再进入流程
/icode init 录制 point_cloud / lidar_imu 转包      # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6

# 从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/回充异常 "退桩后不旋转"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md
# ... 对抗分析收敛后根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
```

## 命令一览

| 命令 | 功能 | 创建目录 |
| ---- | ---- | -------- |
| `/icode help` | 帮助：输出使用流程示例 | 否 |
| `/icode log [零散信息...]` | 可选入口：日志根因分析→转修复需求 `00_init.md`（领域无关，每次都新建目录） | ✅ 每次都新建 |
| `/icode init [<粗略需求>]` | 可选步骤0：多轮对话产出需求初稿 `00_init.md`（每次调用都新建目录） | ✅ 每次都新建 |
| `/icode start <需求>` | 全流程：创建/复用目录 → 步骤1→6 | ✅ / 复用 |
| `/icode plan <需求>` | 仅步骤1：拟定项目计划 | ✅ / 复用 |
| `/icode review [N]` | 仅步骤2：专项审查计划（N=软上限轮数，默认3；仍有问题时自动延长 +2 轮） | 否 |
| `/icode merge` | 仅步骤3：合并审查意见定稿 | 否 |
| `/icode code` | 仅步骤4：落地编码实施 | 否 |
| `/icode deepcheck` | 仅步骤5：无限轮循环复检 | 否 |
| `/icode audit` | 仅步骤6：终极终审 + 统一修复 + 文档化（产出 README.md） | 否 |

> `/icode start` / `/icode plan` 启动时若最新 `.icode_output/.icode_output_N/` 为入口态（status 为 `init_in_progress` 或 `log_done`，即 init/log 产出了 `00_init.md` 但未进步骤1），**询问用户"复用/新建"**——选复用则把 `00_init.md` 作需求输入（来自 log 则同时读 `log_analysis.md` 作背景）；非入口态带参直接新建。

## 执行方式

所有步骤在主会话中执行，使用当前会话模型。不主动切换，用户可自行 `/model`。

## 目录结构

```text
.icode_output/                # 统一父目录，收纳所有产物
└── .icode_output_N/          # N 自动递增（每次需求新建）
    ├── .ico_metadata.json      # 元信息（状态、代码文件列表）
    ├── log_analysis.md         # 入口 log（可选）：日志根因分析报告
    ├── 00_init.md              # 步骤0（可选）/ 入口 log：需求初稿（每轮对话增量更新）
    ├── 01_plan.md              # 步骤1：项目计划
    ├── 02_review.md            # 步骤2：审查报告
    ├── review_round_*.json     # 步骤2：各轮审查详情（JSON）
    ├── 03_plan_final.md        # 步骤3：定稿计划（末尾预留"实现偏差备忘"段，步骤6回写）
    ├── 05_reverse.json         # 步骤5：逆推规格（单条 JSON）
    ├── 05_review_rounds.json   # 步骤5：复检轮次记录（JSONL）
    ├── 06_audit.md             # 步骤6：终审报告
    ├── 06_fixes.log            # 步骤6：修复日志
    └── README.md               # 步骤6.4 文档化产物：本次变更说明
```

## 工作流程

```text
[入口 log(可选)] 日志根因分析 → 转修复需求 00_init.md
[步骤0(可选)] 需求初稿对话 → 00_init.md
       ↓（start/plan 无参 → 询问复用/新建）
[步骤1] 拟定计划 → [步骤2] 专项审查 → [步骤3] 合并定稿
                                              ↓
[步骤6] 终审修复(含偏差回写) ← [步骤5] 循环复检 ← [步骤4] 编码实施
```

## 许可证

MIT
