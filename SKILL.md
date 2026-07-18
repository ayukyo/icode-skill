---
name: icode
description: 端到端编码工作流（步骤 0~6，含可选需求初稿步骤与日志根因分析入口），支持分步手动调用：/icode help (帮助), /icode init [<粗略需求>] (需求初稿), /icode log [零散信息...] (日志根因分析→转修复需求), /icode start <需求> (全流程), /icode fast <需求> (精简全流程), /icode plan <需求> (计划), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审), /icode doc [自然语言] (工程级知识库生成), /icode readme (交付报告), /icode status (工单状态)
---

**版本**: v2.0.0

# ICode 全流程编码工作流（步骤 0 + 1~6）

端到端编码工作流，将需求到交付拆解为严格步骤，每步可单独调用，方便你自行切换模型。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`（含链路图：修改前/后链路 + 改动点，每轮动态更新），独立步骤、不自动串联到步骤1
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 调用命令

所有输出保存在 `.icode_output/.icode_output_N/`（N 自动递增）目录下——所有产物统一收纳在 `.icode_output/` 父目录内，避免工程根目录堆积大量 `.icode_output_*` 目录：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode log [零散信息...]` | **可选入口（日志根因分析）**：把"设备/服务日志+模糊症状"转为有对抗验证的根因报告，自动转修复需求 `00_init.md` 衔接步骤1。先基线检查（git diff/链路图）再日志侦察，对抗分析防确认偏误。**领域无关，每次调用都新建目录**（详见 [steps/log.md](steps/log.md)） | ✅ 每次都新建 |
| `/icode init [<粗略需求>]` | **可选步骤0**：多轮对话产出 `00_init.md`（需求初稿，含链路图：before/after + 改动点，每轮动态更新）。**每次调用都新建目录，不复用、不续聊**（详见 [steps/00_init.md](steps/00_init.md)） | ✅ 每次都新建 |
| `/icode start <需求>` | **全流程（full 模式）**：创建/复用目录 → 步骤1→6 串联。步骤2 review 默认 3 轮 + 对抗验证，步骤5 deepcheck 三阶段循环（**复用规则见下**） | ✅ 创建新目录 / 复用 |
| `/icode fast <需求>` | **精简全流程（fast 模式）**：plan → review(1轮无对抗) → merge → code → deepcheck(Reverse 单阶段) → audit。耗时约为全流程 65%，产物结构与 full 对齐（详见 [steps/fast.md](steps/fast.md)）。入口打印警告、用户自负其责 | ✅ 创建新目录 / 复用 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划（**复用规则见下**） | ✅ 创建新目录 / 复用 |

> **步骤0 init 状态转换时机**（避免状态机歧义）：
> - `init` 调用：建新目录，立即写 `status=init_in_progress` + `completed_steps=["0"]` 落盘
> - 多轮对话期间：状态保持 `init_in_progress`，`00_init.md` 每轮增量更新
> - 用户决定进入步骤1（调 `start`/`plan`）：**start/plan 调用时立即**把 status 从 `init_in_progress` 切换为 `plan_done`，`completed_steps` 追加 `"1"`（**步骤1计划已就绪等价于 plan_done 终态**，因为 start 调用前已读 00_init.md 作步骤1输入）
> - **不得在 init 阶段把 status 切换为 plan_done**——只有 start/plan 显式复用时才切换

> **log 入口状态转换时机**（方式D log→start 工单）：
> - `log` 调用：建新目录，写 `status=log_done` + `completed_steps=["log"]` 落盘
> - 用户决定进入步骤1（调 `start`/`plan`）：**start/plan 调用时立即**把 status 从 `log_done` 切换为 `plan_done`，`completed_steps` 追加 `"1"`（与 init→start 复用规则一致）
> - **不得在 log 阶段把 status 切换为 plan_done**——只有 start/plan 显式复用时才切换
| `/icode review [N]` | **仅步骤2**：多轮循环审查 + 独立质疑者对抗验证（N=软上限轮数，默认3；如最后一轮仍有新问题自动延长 +2 轮，最多扩展至 `max(10, N×2)`）。`mode=="fast"` 时强制 1 轮无对抗 | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：三阶段递进复检（Reverse → Fixed → Free）。`mode=="fast"` 时只跑 Reverse 阶段 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复（产出 `{ICODE_OUT_DIR}/06_audit.md`） | 用最新目录 |
| `/icode readme` | **可选步骤7**：生成交付报告（面向人的自包含总结，动态文件名，智能识别功能/查BUG模板）。步骤6完成后手动触发 | 用最新目录 |
| `/icode doc [自然语言]` | **工程级知识库生成（独立步骤）**：扫描工程代码特征，生成/维护 `~/.claude/icode_data/project_docs/<project_id>/<branch>/` 下的工程知识库章节（架构/IPC/术语表/代码事实审计，**按分支分目录**，切分支跑 doc 不互相覆盖），**同时检测工程依赖的独立模块**（git submodule / `repo` 管理 / CMake FetchContent / monorepo / vendor / 用户配置，6 级优先级）并生成 `~/.claude/icode_data/module_docs/{key}/` 模块共享文档（**按仓库+分支 key 跨工程共享**，同一上游仓库同分支只一份），供 init/log/plan/start/fast 段零自动跨仓库检索注入。**去参数化**——目标工程与动作（全量/增量/新增）由自然语言识别。**v1 单级布局自动迁移**：检测到旧 `<project_id>/` 平铺布局时自动迁移到 `<project_id>/<branch>/`（保留所有字段 + 备份 `_meta.json.v1_migrated_from`，详见 doc.md 步骤 5）。**不创建工单目录、不写工单 metadata、不参与步骤1~6推进**（详见 [steps/doc.md](steps/doc.md)） | 否（写全局 `project_docs/` 和 `module_docs/`） |
| `/icode status` | **状态查询/verdict 标注**：默认只读查当前工单状态（含 `mode`/`verdict` 字段 + 全局索引工单数）；`--verdict <ticket_id> <verified|disproved|superseded> "<reason>" [--correct "<正确方向>"] [--source <machine_test|review|user|auto_signal>]` 手动标注工单方向结论（双写 metadata+index，幂等覆盖刷新 `verdict_at`）；`--scan-verdict` 批量扫描 unknown 完成态工单的 00_init 末轮/06_audit 证伪信号并提示标注（详见 [steps/status.md](steps/status.md)） | 否（默认只读；`--verdict`/`--scan-verdict` 写 metadata+全局索引，不写工程内源码文件） |

> **`/icode start` / `/icode plan` / `/icode fast` 的目录复用规则**：启动时检查最新 `.icode_output/.icode_output_N/` 目录：
> - **入口态有歧义 → 一律问用户**（无论是否带参）：最新目录 status 为 `init_in_progress` 或 `log_done`（即 init/log 产出了 `00_init.md` 但还没进步骤1，且无 `01_plan.md`）时，**必须问用户**："检测到最近有未完成的初稿/根因 `<摘要>`，是 ① 在此基础上继续（复用目录）/ ② 开全新需求（新建目录）？"——用户选①则复用（命令行参数作为需求补充输入，`00_init.md` 为主体），选②则新建
> - **为何带参也问**：带参可能是"补充旧需求"也可能是"新需求"，区分不了，故一律问。误复用（新需求被吞进旧 init、难拆分恢复）的代价高于误新建（旧 `00_init.md` 仍在磁盘、可恢复），故取保守可靠的"一律问"
> - **不得擅自复用**（会丢失新需求）也**不得擅自新建**（会丢失 init/log 上下文）
> - **非入口态带参 → 直接新建**：最新目录已进入步骤1+（有 `01_plan.md` 等，REUSE=0），`/icode start <需求>` / `/icode plan <需求>` / `/icode fast <需求>` 带参一律新建目录。
> - **无参且无入口态可复用 → 报错**：提示先 init/log 或带参新建。
> 详见下文「目录管理」段落（bash 脚本 `REUSE=2` 分支即"一律问"行为，散文与脚本逐行对齐）。

> **历史检索复用**：`/icode init`、`/icode plan`、`/icode start`、`/icode fast`、`/icode log` 启动时会自动检索全局索引中相似历史工单并按命令分流注入参考（init→需求要点 / plan/start/fast→ADR+风险 / log→根因结论+证据），详见下文「历史检索复用」段落。`/icode review`/`merge`/`code`/`deepcheck`/`audit` 不触发检索。

### 帮助说明（`/icode help`）

在对话中输出使用流程示例和命令一览（不创建目录和文件）。

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤）
/icode start 实现MCU雨量传感器I2C驱动

# 方式B：分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1
/icode review                          # 步骤2（软上限3轮，仍有问题时自动延长）
/icode review 5                        # 步骤2（若上轮已 review_done 则重新审查5轮、覆盖历史；若 review_in_progress 中断态则续跑且改用5轮上限）
/icode merge                           # 步骤3
/icode code                            # 步骤4
/icode deepcheck                       # 步骤5
/icode audit                           # 步骤6

# 方式C：先讨论需求再进入流程（推荐用于需求不明确的场景）
/icode init 录制传感器数据转包              # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，会询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1

# 方式D：从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/服务异常 "启动后无响应"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md
# ... 对抗分析收敛后，根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，会询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1
/icode readme                          # 可选：步骤6完成后手动触发，生成交付报告（面向人的自包含总结）
/icode status                          # 可选：随时查当前工单状态（只读，不创建目录）
```

```bash
# 方式E：精简全流程（fast 模式，单文件/小改动场景）
/icode fast 给工具模块增加 clamp 函数                  # 一键串联：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit
# 入口警告自动打印：
# ⚠️ /icode fast 模式：
#    - 步骤2 review 固定 1 轮无对抗验证
#    - 步骤5 deepcheck 只跑 Reverse 阶段（跳过 Fixed/Free）
#    - 依赖 plan+1 轮 review+Reverse 单阶段+audit 四道关卡
#    - 复杂需求（跨模块/新架构/安全敏感）建议改用 /icode start 全流程
# 产物：01_plan.md, 02_review.md, 03_plan_final.md, 05_deepcheck.md, 06_audit.md（与 full 模式结构对齐）
```

```bash
# 方式F：工程级知识库生成（doc 步骤，独立于 1~6 流程，任意时刻可跑）
/icode doc                              # 无描述→全局扫描：列各工程知识库 stale 状态 + 建议动作
/icode doc myproject                    # 检查该工程更新（增量优先：git diff 命中章节才重生成）
/icode doc 重新生成 myproject           # 全量重生成（触发确认门：检测手动编辑，警告后才覆盖）
/icode doc myproject 加 feature_xxx     # 新增章节（十位桶自动编号）
# 产物：~/.claude/icode_data/project_docs/<project_id>/<branch>/*.md（按分支分目录，章节自带身份证：前 50 行四块）
# 不创建工单目录、不写工单 metadata、不参与步骤1~6推进
# 生成后，后续 /icode init|log|plan|start|fast 启动时段零自动检索注入相关章节（无需手动告知参考文档）
```

## 通用规则

### 目录管理

**创建新目录**（用于 `init`，以及 `start` / `plan` / `fast` 在不满足复用条件时）：
```bash
mkdir -p .icode_output   # 统一父目录，所有产物收纳于此
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR=".icode_output/.icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

**复用 / 创建新目录决策**（用于 `start` / `plan` / `fast`）：
```bash
mkdir -p .icode_output
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
REUSE=0
if [ -n "$LAST" ]; then
  CAND=".icode_output/.icode_output_${LAST}"
  # 判定最新目录是否为"入口态"：有 .ico_metadata.json + 00_init.md，且无 01_plan.md
  if [ -f "$CAND/.ico_metadata.json" ] && [ -f "$CAND/00_init.md" ] && [ ! -f "$CAND/01_plan.md" ]; then
    STATUS=$(grep -oP '"status"\s*:\s*"\K[^"]+' "$CAND/.ico_metadata.json")
    # 入口态：init_in_progress 或 log_done
    case "$STATUS" in
      init_in_progress|log_done) REUSE=2 ;;  # 2 = 有歧义，需问用户
    esac
  fi
fi
# REUSE=2：有歧义，问用户"复用 / 新建"；REUSE=0：非入口态，带参新建 / 无参报错
if [ "$REUSE" = "0" ]; then
  NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
  ICODE_OUT_DIR=".icode_output/.icode_output_${NEXT}"
  mkdir -p "$ICODE_OUT_DIR"
fi
```

> **复用决策三档**：`REUSE=2`（入口态有歧义）→ 必须问用户"复用 / 新建"，按答复定；`REUSE=0`（非入口态）→ 带参新建、无参报错。复用时将 `00_init.md` 作为步骤1主要需求输入（命令行参数作补充）。

**检测最新目录**（用于 `review`/`merge`/`code`/`deepcheck`/`audit`）：
```bash
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  echo "错误：没有找到 .icode_output/.icode_output_N 目录，请先运行 /icode start <需求> 或 /icode init"
  exit 1
fi
ICODE_OUT_DIR=".icode_output/.icode_output_${LAST}"
```

### 前置文件校验

| 步骤 | 必须存在的文件 |
|------|---------------|
| review | `{ICODE_OUT_DIR}/01_plan.md` |
| merge | `{ICODE_OUT_DIR}/01_plan.md` + `{ICODE_OUT_DIR}/02_review.md` |
| code | `{ICODE_OUT_DIR}/03_plan_final.md` |
| deepcheck | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |
| audit | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |

> **通用依赖**：所有步骤均依赖 `{ICODE_OUT_DIR}/.ico_metadata.json`（读取 status/completed_steps/续跑字段等），上表仅列出各步骤**额外**要求的产物文件。`init`/`plan`/`start` 因会创建 metadata，无前置校验。

缺失则报错并提示需要先执行哪一步。

### 元信息文件（`.ico_metadata.json`）

```json
{
  "requirement": "需求描述",
  "created_at": "创建时间",
  "status": "当前步骤状态",
  "completed_steps": ["1", "2"],
  "code_files": ["path/to/file"],
  "total_rounds": 1,
  "clean_rounds": 0,
  "max_rounds": 3,
  "absolute_cap": 10,
  "extended_rounds": 0,
  "unresolved_issues_at_cap": false,
  "pending_verification": [],
  "code_compile_failed": false,
  "deepcheck_total_rounds": 0,
  "deepcheck_clean_rounds": 0,
  "deepcheck_phase": "reverse",
  "requirement_summary": "",
  "requirement_points": [],
  "keywords": [],
  "indexed": false,
  "ticket_id": "",
  "code_deviations": []
}
```

每步执行后必须更新 `status` 和 `completed_steps`。步骤4编码后必须记录 `code_files`。

**`code_files` 路径基准**：所有路径**相对于项目根目录**（即用户运行 `/icode` 命令的目录），不含前导 `./`。例：`src/foo.c`、`include/bar.h`。使用 Read 工具时须将相对路径拼接为绝对路径。

**可选字段**（按需写入，缺失视为默认值）：
- `total_rounds` / `clean_rounds` / `max_rounds`：步骤 2 续跑用（`max_rounds` 由 `/icode review [N]` 参数决定，默认 3；运行中如最后一轮仍有新问题会**自动延长 +2 轮**）
- `absolute_cap`：步骤 2 硬上限，`= max(10, max_rounds初始值 × 2)`，防止无限循环
- `extended_rounds`：步骤 2 自动延长次数（每次延长 +2 轮，触发条件：达到 `max_rounds` 但仍有新问题）
- `unresolved_issues_at_cap`：步骤 2 触达 `absolute_cap` 仍有新问题时置 `true`，提示用户回到步骤1修计划
- `pending_verification`：步骤 2 对抗验证中 `needs_more_evidence` 的 issue 清单（**完整 issue 对象数组**，含 `id`/`affected_sections`/`suggestion`/`rejection_risk`/`evidence_pointer`/`verification_status`，供步骤3定稿时直接复核无需回查 JSON），随轮次动态维护（新增追加、已证实/证伪移除），供步骤3定稿时重点复核
- `code_compile_failed`：步骤 4 编译失败标记（`true` 时步骤 5 入口输出警告）
- `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`：步骤 5 续跑用（`deepcheck_phase` 值：`reverse` / `fixed` / `free`），步骤5完成时最终记录
- `requirement_summary`：一句话需求摘要（≤100 token），跨工程历史检索的主依据。步骤0首轮基于粗略需求生成，步骤0每轮对话后更新，步骤1完成计划后基于完整计划刷新
- `requirement_points`：需求要点清单（≤8 条字符串，每条 ≤30 token），`/icode init` 检索命中时注入用。由步骤0从 `00_init.md`「3.新增需求点」自动提炼，用户无感
- `keywords`：技术关键词（≤8 个），辅助检索匹配
- `indexed`：是否已写入全局索引（防重复写入）
- `ticket_id`：本工单在全局索引中的唯一键（`{工程名}-{N}`，冲突时带 hash 后缀）。步骤0写索引时持久化到 metadata；**跳过步骤0直接 `/icode plan`/`/icode start` 的常规新建目录情况**，在步骤1首次写索引时生成并回填 metadata。供后续步骤检索时排除当前工单
- `code_deviations`：步骤4 编码时主动偏离定稿计划的记录数组（每条含 `plan_said`/`actual_done`/`reason`），供步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段；无偏离写空数组 `[]`
- `mode`（新增，可选，默认 `"full"`）：工单模式。`"full"` = `/icode start` 全流程（步骤2 默认 3 轮 + 对抗，步骤5 三阶段循环）；`"fast"` = `/icode fast` 精简全流程（步骤2 固定 1 轮无对抗，步骤5 只跑 Reverse）。**字段缺失视为 `"full"`（向后兼容旧 metadata）**。详见 [steps/fast.md](steps/fast.md)
- `max_rounds`（新增，可选，默认 3）：步骤2 review 软上限轮数。`mode="full"` 时由 `/icode review N` 参数决定（默认 3）；`mode="fast"` 时**自动串联下强制为 1**，但**单步命令（`/icode review N`）在 fast 工单上调用时 N 优先级最高**——用户用参数 N 显式表达 fast→full 升级意图时，按 N 轮跑（详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「步骤2/5 读 mode 字段的契约」段）。**字段缺失视为 3**

> **`verdict` 字段族（方向结论，v2 新增）**--与 `status` 流程态正交：`status` 表示"流程走到哪步"，`verdict` 表示"方案方向对不对"。用于历史检索注入分流，防止已被证伪/取代的工单误导新需求（详见下文「历史检索复用·注入分流」段）：
> - `verdict`（可选，默认 `"unknown"`）：枚举 `"unknown"`（未判定，旧工单默认）/`"verified"`（已验证有效，实机通过/终审高分/已上线无回退）/`"disproved"`（核心方案被证伪/已回退，如某"暂停数据流"方案实机发现语义是"重置状态"而非"冻结"，从根上不可行）/`"superseded"`（被替代方案取代，指向 `superseded_by`）。**字段缺失视为 `"unknown"`（向后兼容旧工单，走原注入逻辑 + 对抗质疑兜底）**
> - `verdict_reason`（可选，≤150 token）：为何这个 verdict。`disproved` 时填证伪原因
> - `correct_direction`（可选，≤150 token）：正确方向。`disproved`/`superseded` 时填，是反转注入避坑的核心载体
> - `verdict_source`（可选）：结论来源，枚举 `machine_test`/`review`/`user`/`auto_signal`，可信度递减
> - `verdict_at`（可选）：结论时间（运行时取系统时间，禁写死，同 `last_used_at` 约定）
> - `superseded_by`（可选）：`"superseded"` 时填替代工单 `ticket_id`
> - `verdict_premise_deps`（可选，数组，默认 `[]`）：证伪前提依赖的外部模块列表，支持"硬复活"。`disproved`/`superseded` 时填（`/icode status --verdict ... --premise-dep <module>:<commit>[:<path>]`，可多次）。每条 `{module, commit, path}`：证伪前提（如"某接口语义是重置非冻结"）依赖这些模块的当时行为；模块 commit 变了->证伪前提可能失效->须重新评估。**空数组/缺失->无硬复活能力，靠主解软复活**（不阻塞）
> - `verdict_review_needed`（可选，bool，默认 `false`）：证伪前提是否需重新评估。置 `true`：`verdict_premise_deps` 非空且任一 dep.commit != 该模块当前 HEAD（`--scan-verdict` 主动扫或检索命中被动检测）。注入分流：`false`->硬反转+证伪前提断言验证；`true`->降级走 unknown 对抗质疑（不硬避坑，防漏过后来又可行的方向）。**字段缺失视为 `false`**（向后兼容旧 disproved，走主解软复活）
> - **录入途径**：`/icode status --verdict` 手动标注（[steps/status.md](steps/status.md)）/ 步骤6 终审回填（[steps/06_audit.md](steps/06_audit.md)）/ 批量识别扫描提示（`/icode status --scan-verdict`，[steps/status.md](steps/status.md)）
> - **幂等保护**：verdict 一旦判定，后续 status 流转不重置；只有"步骤6 终审重新评估"或"用户显式改标"才覆盖（覆盖时刷新 `verdict_at`）

**`status` 字段枚举**（统一词表，所有步骤必须严格遵守，禁止自定义）：

| 步骤 | 状态值 | 含义 |
|------|--------|------|
| log | `log_in_progress` → `log_done` | 日志根因分析中 → 完成（入口命令，与步骤0并列，不参与步骤1~6推进） |
| 0 | `init_in_progress` | 步骤0需求初稿讨论中（多轮对话每轮更新文档，无显式"完成"态） |
| 1 | `plan_done` | 步骤1计划完成 |
| 2 | `review_in_progress` → `review_done` | 步骤2审查中 → 完成 |
| 3 | `plan_finalized` | 步骤3定稿完成 |
| 4 | `code_in_progress` → `code_done` | 步骤4编码中 → 完成 |
| 5 | `deepcheck_in_progress` → `deepcheck_done` | 步骤5复检中 → 完成 |
| 6 | `completed` | 步骤6终审完成（终态） |

**步骤0说明**：步骤0产出 `00_init.md` 后 status 一直保持 `init_in_progress`，直到 `/icode start`/`/icode plan` 复用该目录进入步骤1时才被切换为 `plan_done`。`completed_steps` 含 `"0"` 表示走过步骤0。

**`in_progress` 状态的两种语义**：

- `init_in_progress`：步骤0**稳态**标记，文档每轮增量更新，等待 `/icode start`/`/icode plan` 复用并切换到 `plan_done`。**不参与崩溃续跑判定**。
- `log_in_progress` / `log_done`：`/icode log` 日志根因分析的**分析中→完成**标记。`log_done` 后用户质疑可切回 `log_in_progress` 只重跑被质疑的根因分支（详见 [steps/log.md](steps/log.md)）。**不参与步骤1~6 推进**（`completed_steps` 含 `"log"` 仅标记走过 log）。
- `review_in_progress` / `deepcheck_in_progress`：步骤 2/5 的**中断续跑标记**，每轮结束时实时落盘 `*_in_progress` + 续跑计数器，崩溃后重启可从断点恢复。步骤2落盘 `total_rounds`/`clean_rounds`/`max_rounds`/`absolute_cap`/`extended_rounds`/`pending_verification`；步骤5落盘 `deepcheck_total_rounds`/`deepcheck_clean_rounds`/`deepcheck_phase`。
- `code_in_progress`：步骤 4 的**执行中标记**（只在步骤4整体开始时落盘 `code_in_progress`，完成后切换为 `code_done`，不带轮次/阶段维度的断点续跑）。

### 执行模式

所有步骤（含可选步骤0）在主会话中执行，使用当前会话模型。**不主动切换模型**，用户如需切换可手动 `/model`。

### 强制思考前置（所有步骤必须遵守）

完整规则**必须先 Read [references/thinking.md](references/thinking.md)**（不得凭概述/记忆执行，否则产出不合规——见反偷懒第15条）。核心要点：每步开始前先 `ultrathink`，首选 `sequential-thinking` MCP 至少3步，MCP 不可用降级 `### 结构化思考` 文字块；每步思考子项见各 step 文件 + thinking.md。

### 反偷懒约束（所有步骤必须遵守，硬性总则）

完整规则**必须先 Read [references/anti_laziness.md](references/anti_laziness.md)**（不得凭概述/记忆执行，否则产出不合规——见反偷懒第15条）。核心要点：16条典型偷懒行为 + 正面合规要求；引用 references 必须每步重新 Read 输出 `📖 已 Read` 确认行；思考块每子项≥2句实质内容；用户授权例外须显式记录。

### 全流程串联规则

`/icode start` 执行步骤1后，如果会话断开，恢复时必须读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。不可跳过未完成的步骤。

**续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 和 `"log"` 仅作为"已走过步骤0/log入口"的标记，**不影响**推进逻辑。例：`["0"]`/`["log"]` → 下一步是步骤1；`["0","1"]`/`["log","1"]` → 下一步是步骤2。

步骤 2/5 的 `*_in_progress` 状态 + 轮次计数器支持**断点续跑**（步骤0的 `init_in_progress` 不参与，详见上节"两种语义"）；步骤 4 的 `code_in_progress`（编译失败时保留）支持**整体续跑**——重跑步骤4时**在已写入的代码基础上继续修复**（编译失败时代码文件和 `code_files` 已保留落盘，不丢弃、不从计划重新编码），不带轮次断点。

### 历史检索复用（跨工程/跨工单借鉴）

> **检索复用两源**（init/log/plan/start/fast 启动时并行检索，候选合并排序注入，最相关者胜）：
>
> - **源1·历史工单**（本段）：跨工单借鉴相似需求的 ADR/风险/根因/要点，详见下文
> - **源2·工程文档（段零）**：当前工程 `~/.claude/icode_data/project_docs/<project_id>/` 知识库（`/icode doc` 生成），段零只读章节前 50 行粗筛、命中按 `[小节锚点]` 定点读小节。**过时章节降级注入**（stale 章节不注正文只注摘要+警告，与历史工单 stale 跳过注入同等防误导）+ **注入文档须 Read/Grep 实证不盲信**（文档是快照可能过时，不作代码事实依据）。**v2 模板质量信号**（v2.0.0 新增）：章节 `_meta.json.template_version` 与 [doc_template.md](references/doc_template.md) 顶部 `SCHEMA_VERSION` 比对，**v2 章节注入优先级 > v1 章节**（v1 章节降级注入摘要+升级提示），保证下游尽量拿到高质量上下文。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」「stale 章节降级注入」「不盲信约束」+「质量信号」+「双视角使用说明」段 + [references/doc_template.md](references/doc_template.md)
> - **防重复注入**（两源共用）：`{ICODE_OUT_DIR}/_inject_cache.json` 按 `(source, ref_id, slice)` 三元组去重，历史源 `hit_count` 同目录内同 ticket 只续期一次。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制」段

**痛点**：每次 `/icode` 都是冷启动，过往相似需求的计划/决策/踩坑无法被新需求复用。本机制在不破坏工程隔离、不撑爆上下文的前提下，让新需求能主动检索历史相似工单并定点注入参考。

> **verdict 防误导（v2 新增）**：历史工单的核心方案可能已被实机证伪/取代，直接注入其 ADR 会把新需求带向错误方向。本机制给每个工单标 `verdict`（方向结论，与 `status` 流程态正交），注入按 verdict 分流：`disproved` **反转注入避坑**（不注 ADR，注证伪原因+正确方向）、`superseded` 注替代指针、`unknown`（含所有旧工单，**不依赖标注**）走 A 层强制强化（扩读 `00_init.md` 末轮+对抗质疑+⚠️警告）兜底。录入：`/icode status --verdict` 手动标 / 步骤6 终审回填 / `/icode status --scan-verdict` 批量识别提示。详见「注入形式·按 verdict 分流」+ [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·verdict 分流注入」+「续期·verdict 分流」段

**全局索引**（不污染任何工程，不放技能目录）：

- 索引文件：`~/.claude/icode_data/index.json`（首次运行自动创建）。**路径说明**：`~` 是当前用户主目录，由 Claude Code 工具层跨平台解析（Linux/macOS/Windows 通用），与技能目录 `~/.claude/skills/icode/` 同源。技能文件**禁止硬编码**任何具体用户路径（如 `/home/xxx`、`C:\Users\xxx`），所有全局路径必须用 `~` 表达，确保技能可移植
- 每条记录：`ticket_id`(`{工程名}-{N}`，工程名冲突时追加 `project_path` 短 hash 后缀保唯一)、`project_path`、`out_dir`、`requirement_summary`(≤100 token)、`requirement_points`(≤8 条)、`keywords`(≤8个)、`has_00_init`/`has_plan`、`status`、`created_at`、`last_used_at`(检索命中时更新，LRU淘汰依据)、`hit_count`(检索命中+1，达20永久保留)、`stale`(默认false，过时校验失败置true，软stale可复活)、`stale_reason`(失败原因`anchor_gone`/`checkout_mismatch`/`path_gone`/`semantic_deviation`/`timeout`)、`stale_checked_commit`(上次评估时HEAD，可复活判据)、`created_commit`(创建时HEAD，commit上下文判据，非git仓库为null)、`created_branch`(`--abbrev-ref HEAD`)、`verdict`/`verdict_reason`/`correct_direction`/`verdict_source`/`verdict_at`/`superseded_by`/`verdict_premise_deps`/`verdict_review_needed`（方向结论字段族，默认 `verdict="unknown"` 其余 null/[]/false，详见上「verdict 字段族」；检索注入按 verdict 分流，`disproved`/`superseded` 不续期 hit_count、不享受永久保留、排序降权，详见「索引淘汰规则」）。**`has_00_init` 语义 = 该工单是否已产出 `00_init.md`（走过 init 或 log，log 也会产出 00_init.md），与"是否走过步骤0 init"解耦**——log 未走过 init 但产出 00_init.md 故也为 true
- **只存指针和摘要，不存产物正文**。产物仍在各工程 `.icode_output/`，工程隔离不破坏。
- **LRU 淘汰**（防 index.json 无限膨胀）：索引是检索缓存非档案。容量上限 200 条；`hit_count >= 20` 且 `verdict != "disproved"` 永久保留（被复用≥20 次的高价值工单；`disproved` 不永久保留，详见「索引淘汰规则」）；未完成态（init/log/review/deepcheck/code in_progress）默认不淘汰，但**超时降级**（`last_used_at` 超 30 天无更新->置 `stale=true`+`stale_reason=timeout` 解除保护、纳入可淘汰，不新增 status 值；timeout 为硬 stale 不复活）；超上限时在**可淘汰集**淘汰 `last_used_at` 最老的：① `hit_count < 20` 且 `stale=false` 完成态，或 ② `stale=true` 且 `stale_reason=timeout` 的超时降级僵尸（status 仍 in_progress）；**软 stale（非 timeout）保留不淘汰待复活**。另**主动 stale 扫描**：每次写索引顺带校验最旧 K 条锚点，失效置 `stale=true`。检索命中**原子同步**更新 `last_used_at`+`hit_count` 续期。淘汰只删索引条目，产物保留各工程。**排序**：tickets 数组按复合键 `(verdict_priority, hit_count)` 降序、同值按 `last_used_at` 降序（`verdict_priority`: verified>unknown>superseded>disproved，详见「索引淘汰规则」）（高价值近期项在前，段一粗筛扫 keywords 快+淘汰从末尾）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「索引淘汰规则」
- **过时校验**（防注入过时信息）：索引存的是工单当时的摘要，工程迭代后老工单 ADR/需求可能已过时。**两处触发**：①检索命中准备注入前（被动）；②每次写索引触发淘汰后，主动 Grep 校验最旧 K 条代码锚点（主动）。锚点失效→置 `stale=true` 跳过注入（即使 hit_count 高也不注入）。stale 工单保留索引留追溯，不再续期，不再被段一粗筛命中。**project_docs 工程文档同样有过时校验**：段零注入前被动 stale 检测（命中 KEYS 文件位置或正文目录前缀即标 stale，降级注入不注正文）+ `/icode doc` 末尾主动 stale 扫描（全库锚点校验写 `_meta.json.stale_files`，见 [steps/doc.md](steps/doc.md) 步骤8）+ module_docs commit 一致性校验（同分支不同 commit 降级注入+警告）。**历史工单 stale 校验 v2**：commit 上下文比对（`created_commit` vs 当前 HEAD：同提交高置信注入/后代跑锚点/祖先分叉软stale）+ top-N 注入前语义偏离 checklist（抓"锚点在但语义变"）+ stale 可复活（checkout 变化重评，解决临时旧提交误判）+ **Git 操作只读白名单**（禁 checkout/reset/commit 等写操作与网络操作，详见「过时校验·Git 操作安全白名单」）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「检索命中续期 + 过时校验」「索引淘汰规则·主动 stale 扫描」「project_docs 主动 stale 扫描」「段零·工程文档检索·步骤 3 commit 一致性校验」

**写索引时机**（**`keywords` 是段一粗筛的检索索引，所有入口首次写索引时必须填 ≤8 个技术关键词、不得为空**——空 keywords 的工单无法被粗筛命中，等于检索盲区）：
- `/icode log` 产出 `log_analysis.md` + `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`（根因摘要）+`requirement_points`（修复要点）+`keywords`（≤8个，从根因/症状提炼）+`has_00_init=true`+`status=log_done`+`last_used_at=当前`+`hit_count=0`+`stale=false`+`stale_reason=null`+`stale_checked_commit=null`+`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）+`created_branch`，**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤0 首轮写 `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`+空 `requirement_points`+`keywords`（≤8个，从粗略需求提炼）+`has_00_init=true`+`last_used_at=当前`+`hit_count=0`+`stale=false`+`stale_reason=null`+`stale_checked_commit=null`+`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）+`created_branch`，**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤0 每轮对话更新后：刷新 `requirement_summary`；`requirement_points` **仅在首次写索引时生成**（步骤0首轮），步骤0 每轮对话不重复刷新（步骤1 完成 `01_plan.md` 时再统一刷一次）
  - **`requirement_points` 提炼算法**（明确可执行）：
    1. 扫描 `00_init.md` 中 `## 3. 新增需求点` 章节下的 `- [ ]` / `- [x]` 列表项
    2. 每行去掉 checkbox 前缀（`- [ ] ` / `- [x] `），保留核心短语作为一条 `requirement_points`
    3. 若某行超 30 字符，截断到 30 字符 + `...`（避免索引体积爆）
    4. 最多保留 8 条，多余的丢弃
    5. 若「3. 新增需求点」章节缺失/为空，`requirement_points` 保持空数组
    6. 例：`- [x] calc_eval 函数签名` → `"calc_eval 函数签名"`
- 步骤1 写完 `01_plan.md` 后：刷新 `requirement_summary`（基于完整计划）+ `has_plan=true`；**常规新建目录首跑时**（跳过步骤0）在此首次生成 `ticket_id` 并回填 metadata、首次写入索引条目（`has_00_init=false`、`keywords`（≤8个，从计划技术栈提炼，不得为空）、`last_used_at=当前`、`hit_count=0`、`stale=false`、`stale_reason=null`、`stale_checked_commit=null`、`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）、`created_branch`），**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤6 终审完成后：刷新 `status=completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新；**若 `stale=true` 重置 `stale=false`+`stale_reason=null`+`stale_checked_commit=null`**（旧 stale 判据失效，下次检索重评）；**确认 verdict**（默认保持 `unknown` 不阻塞流程；用户标 `verified`/`disproved`/`superseded` 时回填 `verdict`+`verdict_reason`+`correct_direction`+`verdict_source`+`verdict_at`，详见 [steps/06_audit.md](steps/06_audit.md)）

**检索注入流程**（`/icode init`、`/icode log`、`/icode plan`、`/icode start`、`/icode fast` 共用检索，分流注入；段零工程文档候选与本流程候选合并排序后统一注入，不分来源）：

1. **检索阶段·两段式**（强制思考**之前**；`/icode init`/`/icode log` 在建目录后检索，`/icode plan`/`/icode start` 在目录管理+确定需求来源后检索——确保用完整需求做相关性判断）：

   **段一·粗筛（不进 LLM，纯计算，零 token 消耗）**：从当前需求/症状提炼关键词集 `K_new`，**先过滤**当前 `ticket_id`（不自我参考）+ **可复活预扫**（对每条 stale 工单取 `H = git -C {project_path} rev-parse HEAD`（只读）；`stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的临时置 `stale=false` 重入候选重评，见步骤2「可复活 stale」）后剩余的 `stale=true`--段一粗筛前**显式排除**（而非粗筛后再过滤），降低计算量；再与**全量 `tickets` 数组中**剩余 ticket 的 `keywords` 做集合交集（index.json 是完整 JSON，必须 `json.load` 整体解析全量读，禁止只读前 N 行--「前 50 行」仅适用于 `project_docs/*.md` 章节，见 [dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」段），按 **Jaccard 相似度**（`|K_new ∩ K_ticket| / |K_new ∪ K_ticket|`）降序排列。取相似度 > 0 的前 **≤10 条**作为候选集（候选为 0 则直接零命中结束）。**关键词缺失的工单**（`keywords` 为空）在粗筛中无法被命中，故写索引时 `keywords` 不得为空（≤8 个技术词）。

   > **为何先粗筛**：index.json 到 200 条上限时全量进上下文 ≈ 3.5 万 token，纯靠 LLM 现场扫全部 summary 会撑爆 context 且判断质量随条数下降。粗筛把 O(全部) 降到 O(候选集)，实测能圈出 ≤10 条强相关候选。

   **段二·精读（进 LLM）**：只把候选集的 `keywords + requirement_points`（约 50-100 token/条，10 条 ≤1K token）喂给主代理，结合当前需求/症状逐条判相关性并打分（0-10）。按分数排序，选 top-N 命中（N 由梯度规则定，见下）。

2. **过时校验 + 命中续期**（对 top-N 命中工单，注入前逐条；`H = git -C {project_path} rev-parse HEAD`（每候选一次）；详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验」）：
   - **项目路径校验**：`test -d {project_path}` 失败->`stale=true`+`stale_reason=path_gone`，跳过注入
   - **commit 上下文校验**（`created_commit` 非 null）：`H==created_commit`->高置信注入快路径；`git merge-base --is-ancestor {created_commit} {H}`：退出0->正常演进进锚点校验；退出1->`stale=true`+`stale_reason=checkout_mismatch`（软stale，checkout变化可复活）；退出128（commit不可达）->视同null进锚点校验
   - **代码锚点校验**：Grep 该工单工程 `{project_path}` 的 ADR 锚点是否仍存在；失效->`stale=true`+`stale_reason=anchor_gone`，跳过注入
   - **语义偏离校验**（仅对已决定注入的 top-N≤3 条）：Read 该工单工程 `{project_path}` 的锚点代码按偏离 checklist 判 ADR 前提（签名/返回值边界/调用关系）是否仍成立；偏离->`stale=true`+`stale_reason=semantic_deviation`，跳过注入（抓"锚点在但语义变"）

   **命中续期**：全部校验通过的工单，**按 verdict 分流续期**——`verified`/`unknown`（含旧工单）原子同步更新 `last_used_at`=当前时间、`hit_count`+=1 写回（两字段同一次写回，不得只更其一--详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「续期（校验通过才续期）·原子同步」）；`stale_checked_commit=H` 在评估时已更新（与 hit_count 解耦，续期去重不阻断）。`stale=true` 的工单不注入不续期（软stale可复活见下）。

   **可复活 stale**（解决 checkout 假阳性）：段一前对每条 stale 工单取其 `H = git -C {project_path} rev-parse HEAD`；`stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的工单临时置 `stale=false` 重入段一重评（重评仍失败则更新 `stale_checked_commit=H`，同 HEAD 不再重评）。**所有 git 调用只读**，禁止 checkout/reset/commit 等写操作与网络操作（白名单见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·Git 操作安全白名单」）。

   **续期去重**（防 hit_count 虚高）：续期前查 `{ICODE_OUT_DIR}/_inject_cache.json`，若本工单目录已续期过该历史工单（`source=history AND ref_id=该 ticket` 任一记录）则不再 `+1`（新 slice 仍注入）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制·续期去重」段

3. **注入阶段·top-N 动态梯度**（按命令分流，N 由相关性梯度决定；**注入前查 `{ICODE_OUT_DIR}/_inject_cache.json` 去重**——按 `(source, ref_id, slice)` 三元组查，已注入的 slice 跳过。历史源 `(history, ticket, <slice>)`、段零 `(project_doc, 章节文件, section:<file>)`。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制·去重规则」段）：

   - **强相关**（精读分数 ≥8）：全注入，上限 2 条
   - **弱相关**（精读分数 5~7）：在强相关之外再注 ≤1 条最相关的作"边缘参考"
   - **零强相关但有弱相关**：注 ≤2 条最相关
   - **全无相关**（分数 <5 或候选集为 0）：**零注入，不强凑参考**
   - 注入总量上限：强相关 2×1K + 弱相关 1×0.8K ≈ 2.8K token（plan 模式；init/log 按下表体积上限）

   | 命令 | 命中后注入内容 | 来源 | 体积上限 |
   |------|--------------|------|---------|
   | `/icode init` | 命中工单的 `requirement_points`（需求要点清单） | 读 metadata 或 `00_init.md`「3.新增需求点」 | ≤500 token/条 |
   | `/icode plan` / `/icode start` / `/icode fast` | 命中工单的 **ADR 章节 + 风险评估章节** | 定点读 `01_plan.md` 对应章节（**不读全文**） | ≤1K token/条 |
   | `/icode log` | 命中工单的 **根因结论 + 决定性证据** | 定点读 `log_analysis.md`「核心结论 + 决定性证据」章节（**不读全文**） | ≤800 token/条 |

4. **注入形式·按 verdict 分流**（v2 新增，核心防误导机制）：命中工单经段二精读+过时校验后，**先读其 `verdict` 字段按值分流注入**（字段缺失视为 `"unknown"`，向后兼容旧工单）：

   | verdict | 注入内容 | 来源 | 体积上限 | 思考块标注 |
   |---------|---------|------|---------|-----------|
   | `verified` / `unknown`（含旧工单） | 上表对应命令的注入内容（ADR+风险/根因/要点） + **`unknown` 额外扩读 `00_init.md` 末轮对话摘要** | 上表来源 + `00_init.md` 末轮 | 上表上限 + ≤0.3K（末轮） | ✅借鉴 / `unknown` 时 ⚠️「结论未经验证标注，须甄别是否被后续实机推翻」 |
   | `disproved`（`verdict_review_needed=false`） | **不注 ADR**，注 `verdict_reason`（作可验证断言）+ `correct_direction` | metadata/index verdict 字段 | ≤0.7K/条 | ⛔「避坑+证伪前提断言：须 Grep/Read 验证前提是否仍成立，失效则提示 `--verdict` 标复活」 |
   | `disproved`/`superseded`（`verdict_review_needed=true`） | **降级对抗质疑**：不硬反转，走 unknown A 层（扩读末轮+三问）+ 证伪前提+依赖变化提示 | metadata verdict + 末轮 | ≤1.1K/条 | ⚠️「曾证伪但依赖已变化，原证伪可能失效，重新评估是否还成立」 |
   | `superseded` | 替代指针 `superseded_by` + `correct_direction` + 替代工单摘要 | metadata + 替代工单 | ≤0.8K/条 | 🔁「已被替代，参考新方案 {superseded_by}」 |

   - 历史参考作为主代理的**思考输入**，在强制思考文字块里加一节「历史参考」，按上表 verdict 标注 + 注入内容，影响后续产出质量
   - **`unknown` 强制 A 层强化**（旧工单防误导主防线，不依赖标注）：扩读末轮 + 对抗质疑三问（详见 [references/thinking.md](references/thinking.md)「历史参考小节」）
   - `disproved` 的 `correct_direction` 缺失时：降级注 ADR + ⛔ 警告（ADR 仅作避坑对照），提示用户用 `/icode status --verdict` 补标 `correct_direction`
   - **verdict_review_needed 复活降级**（防漏过后来又可行的方向）：`disproved`/`superseded` 工单若 `verdict_premise_deps` 非空且任一依赖 commit 已变化，则 `verdict_review_needed=true`，**不硬反转**，降级走 unknown A 层对抗质疑 + 证伪前提+依赖变化提示，让新需求重新评估证伪前提是否仍成立；前提失效则该方向或可重新考虑，提示 `/icode status --verdict` 标复活（unknown/verified）。检测：`--scan-verdict` 主动扫 + 检索命中被动检测（详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·verdict 分流注入」+ [steps/status.md](steps/status.md)）
   - **零命中不注入，不强凑参考**

**防撑爆四道闸门**：
- **索引体积**：全局索引单条只存摘要+要点+关键词，整体 <2K token
- **粗筛控量**：两段式检索只把 ≤10 条候选集 keywords+requirement_points 喂 LLM（非全量），控 token
- **注入数**：top-N 动态梯度，强相关≤2 + 弱相关≤1，全相关时上限 3 条（与段零工程文档候选合并后总量≤3 条一致，见 [dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」段步骤 4）
- **注入体积**：init 注入要点 ≤500 token/条；plan 注入 ADR+风险 ≤1K token/条；log 注入根因+证据 ≤800 token/条；超大工单需深读时派子代理消化成摘要返回（隔离上下文）

**工程污染防护**（重要）：
- 历史参考**只进会话上下文，不写进产物文件**（`00_init.md` 完全不写历史引用；`01_plan.md` 不堆砌历史引用）
- **唯一最小留痕**：若某条 ADR 实质借鉴了历史工单决策，在该 ADR「理由」末尾加一句 `(参考相似工单 {ticket_id} 的同类决策)`——这是决策溯源而非污染，且可选
- 全局索引在 `~/.claude/icode_data/`，工程内无感知；产物路径不动

**边界处理**：
- 全局索引不存在 → 首次运行自动创建空索引，检索跳过
- 命中工单产物读不到（工程被删/移动）→ 跳过该条不报错，索引条目惰性保留
- `00_init.md` 无「3.新增需求点」→ `requirement_points` 为空，`/icode init` 命中时不注入该条
- `log_analysis.md` 无「核心结论 + 决定性证据」章节（如工单未走过 log）→ `/icode log` 命中时不注入该条

### 注意事项

- **Git 安全**：禁止执行任何 Git 危险操作（`git reset --hard`、`git push --force` 等），**也禁止 `git commit` 和 `git push`**
- **`.icode_output/` 父目录及其下的 `.icode_output_N/` 目录无需用户确认**：该目录下创建/写入/修改 `.md`/`.json`/`.log` 文件均为安全操作
- **工程污染防护**：`.icode_output/` 是 icode 产物目录，建议在工程 `.gitignore` 中加入 `.icode_output/`，避免产物误提交；icode 本身**不自动修改工程的 `.gitignore`**（工程配置由用户掌控）。历史检索的全局索引位于 `~/.claude/icode_data/`，不在任何工程内，无污染风险；`/icode doc` 的工程文档库位于 `~/.claude/icode_data/project_docs/`，同样不在任何工程内、不写工程内任何文件（用户工程内已有 `doc/workflows/` 等历史文档时，忽略不读取不迁移不删除，从零生成到全局）
- **跨会话恢复**：运行 `ls -d .icode_output/.icode_output_*` 确认目录后，直接调用对应步骤即可
- **中断恢复**：重新执行某步骤可覆盖该步骤输出

## 各步骤详细规则

各步骤的详细 prompt、维度要求、执行流程请读取对应文件：

| 步骤 | 命令 | 详细文件 |
|------|------|----------|
| log | `log` | [steps/log.md](steps/log.md) |
| 0 | `init` | [steps/00_init.md](steps/00_init.md) |
| 1 | `plan` / `start` | [steps/01_plan.md](steps/01_plan.md) |
| 1~6 | `fast` | [steps/fast.md](steps/fast.md)（编排）+ 各步骤文件（带 fast 降级分支） |
| 2 | `review` | [steps/02_review.md](steps/02_review.md) |
| 3 | `merge` | [steps/03_merge.md](steps/03_merge.md) |
| 4 | `code` | [steps/04_code.md](steps/04_code.md) |
| 5 | `deepcheck` | [steps/05_deepcheck.md](steps/05_deepcheck.md) |
| 6 | `audit` | [steps/06_audit.md](steps/06_audit.md) |
| 7 | `readme` | [steps/07_readme.md](steps/07_readme.md) |
| doc | `doc` | [steps/doc.md](steps/doc.md) |
| - | `status` | [steps/status.md](steps/status.md) |

**执行步骤时，必须先读取对应的 `steps/XX_*.md` 文件，按其中的详细指令执行。**

## 共享规则文件（references/）

各 step 文件不再重复定义跨步骤共享的规则，统一引用 `references/` 下的共享文件。执行某步骤时若该步骤引用了共享文件，**必须先用 Read 工具实读该文件完整内容**（不得凭 SKILL.md 概述或记忆执行，否则产出不合规——见反偷懒第15条）：

| 共享文件 | 内容 | 引用方 |
|---------|------|--------|
| [references/thinking.md](references/thinking.md) | 强制思考前置（ultrathink/MCP/降级文字块/各步子项） | 所有 step |
| [references/anti_laziness.md](references/anti_laziness.md) | 反偷懒约束（16条偷懒行为+合规要求+references必读+确认行） | 所有 step |
| [references/adversarial.md](references/adversarial.md) | 对抗分析模式（3质疑者/裁决优先级/诚实降级/证据回指） | 02_review / log |
| [references/dir_and_metadata.md](references/dir_and_metadata.md) | 目录管理 + ticket_id 生成 + 全局索引写入（含LRU淘汰） + metadata 模板 + **注入缓存机制（防重复注入，两源共用）** + **project_docs 工程文档库 + 段零检索** | init / log / plan / start / fast / doc |
| [references/doc_template.md](references/doc_template.md) | icode doc 章节模板：前 50 行四块结构（项目元信息/KEYS/简要说明/目录）+ 十位桶编号 + 自适应 grep 关键词表 + 99 章审计策略 + **v2.0.0 双视角必含元素清单（14 项）+ 业务流独立成章 + 英文首次中文备注 + 链路中文说明 + 质量审视检查清单 + 模板版本自举迁移** | doc |
