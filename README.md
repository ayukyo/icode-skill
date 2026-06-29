# ICode — 全流程编码工作流（步骤 0 + 1~6，含日志根因分析入口）

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为严格步骤，每步可单独调用，手动切换模型时操作更灵活。

- **入口命令（可选）**：`/icode log` 日志根因分析（领域无关）→ 转修复需求；`/icode init` 需求初稿对话
- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 特性

- **闭环交付**：(可选) 需求初稿 → 计划 → 审查 → 定稿 → 编码 → 复检 → 终审，每步可独立调用，主会话执行、不切换模型
- **双模式**：`/icode start` 全流程（多轮审查 + 对抗验证）/ `/icode fast` 精简（1 轮无对抗，约 65% 耗时），自动串联步骤 1→6
- **防偷懒质量门**：三阶段复检（Reverse/Fixed/Free）、Plan 断言实证验证、ADR 决策记录、对抗验证（独立质疑者——证据不足不确认、诚实降级不伪造共识）
- **跨工程历史检索**：init/log/plan/start 启动时自动检索相似历史工单按命令分流注入，只进会话、不污染工程产物
- **两个可选入口**：`/icode log` 日志根因分析（先基线检查再对抗分析，领域无关）→ 转修复需求；`/icode init` 多轮需求初稿对话 → `00_init.md`
- **产物与状态管理**：统一收纳在 `.icode_output/.icode_output_N/`，`.ico_metadata.json` 记录状态/代码文件，支持跨会话恢复与断点续跑

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

# 精简全流程（fast 模式：单文件/小改动场景，耗时约为全流程 65%）
/icode fast 给 calc.c 增加 isqrt 函数   # plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit

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
| `/icode fast <需求>` | 精简全流程：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit（耗时约为全流程 65%） | ✅ / 复用 |
| `/icode plan <需求>` | 仅步骤1：拟定项目计划 | ✅ / 复用 |
| `/icode review [N]` | 仅步骤2：专项审查计划（N=软上限轮数，默认3；仍有问题时自动延长 +2 轮） | 否 |
| `/icode merge` | 仅步骤3：合并审查意见定稿 | 否 |
| `/icode code` | 仅步骤4：落地编码实施 | 否 |
| `/icode deepcheck` | 仅步骤5：三阶段递进复检（Reverse → Fixed → Free，fast 模式仅 Reverse） | 否 |
| `/icode audit` | 仅步骤6：终极终审 + 统一修复（产出 06_audit.md） | 否 |
| `/icode readme` | 可选步骤7：生成交付报告（面向人的自包含总结，动态文件名，智能识别功能/查BUG模板） | 否 |
| `/icode status` | 只读：查当前工单状态（不创建目录/不写文件） | 否 |

> `/icode start` / `/icode plan` / `/icode fast` 启动时若最新 `.icode_output/.icode_output_N/` 为入口态（status 为 `init_in_progress` 或 `log_done`，即 init/log 产出了 `00_init.md` 但未进步骤1），**询问用户"复用/新建"**——选复用则把 `00_init.md` 作需求输入（来自 log 则同时读 `log_analysis.md` 作背景）；非入口态带参直接新建。

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
    ├── 05_deepcheck.md          # 步骤5：三阶段复检（Reverse/Fixed/Free 合并）
    ├── 06_audit.md             # 步骤6：终审报告（含修复日志段）
    └── {动态文件名}.md        # 步骤7（可选）：交付报告（/icode readme 生成，面向人的自包含总结）
```

## 工作流程

```text
[入口 log(可选)] 日志根因分析 → 转修复需求 00_init.md
[步骤0(可选)] 需求初稿对话 → 00_init.md
       ↓（start/plan/fast 无参 → 询问复用/新建）
[步骤1] 拟定计划 → [步骤2] 专项审查 → [步骤3] 合并定稿
                                              ↓
[步骤6] 终审修复(含偏差回写) ← [步骤5] 循环复检 ← [步骤4] 编码实施

fast 模式（/icode fast）：步骤2 固定 1 轮无对抗，步骤5 只跑 Reverse；产物与 full 模式结构对齐
```

## 许可证

MIT

## DEMO（用于测试 icode 流程）

`demo/` 是一个最小 C 计算器工程（`calc.h`/`calc.c`/`main.c`/`Makefile`），**专门用来端到端测试 icode 工作流**——五种调用方式（A 全流程 / B 分步 / C init→start / D log→start / E fast 精简）都可在此工程里真实跑通：步骤1 计划、步骤4 编码、步骤5 复检、步骤6 编译验证都有真实代码可操作。

```bash
cd demo && make && ./calc_demo   # 确认基线可编译可运行
```

测试示例需求：
- **方式A**： `cd demo && /icode start 给计算器增加取模和幂运算功能，并补全整数溢出检查`
- **方式B（分步）**： `cd demo && /icode plan 给计算器增加 isqrt 函数` 然后逐步 `/icode review` `/icode merge` `/icode code` `/icode deepcheck` `/icode audit`
- **方式C（先 init 后 start）**： `cd demo && /icode init 计算器加新功能`（多轮对话澄清需求）→ `/icode start`
- **方式D（先 log 后 start）**： `cd demo && /icode log <log路径> "症状描述"` → 产出根因 + 修复需求 → `/icode start`
- **方式E（fast 精简）**： `cd demo && /icode fast 给 calc.c 增加 isqrt 函数`（耗时约 65%）
