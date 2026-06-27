---
name: icode
description: 端到端编码工作流（步骤 0~6，含可选需求初稿步骤与日志根因分析入口），支持分步手动调用：/icode help (帮助), /icode init [<粗略需求>] (需求初稿), /icode log [零散信息...] (日志根因分析→转修复需求), /icode start <需求> (全流程), /icode plan <需求> (计划), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审)
---

**版本**: v1.8.0

# ICode 全流程编码工作流（步骤 0 + 1~6）

端到端编码工作流，将需求到交付拆解为严格步骤，每步可单独调用，方便你自行切换模型。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`，独立步骤、不自动串联到步骤1
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 调用命令

所有输出保存在 `.icode_output/.icode_output_N/`（N 自动递增）目录下——所有产物统一收纳在 `.icode_output/` 父目录内，避免工程根目录堆积大量 `.icode_output_*` 目录：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode log [零散信息...]` | **可选入口（日志根因分析）**：把"设备/服务日志+模糊症状"转为有对抗验证的根因报告，自动转修复需求 `00_init.md` 衔接步骤1。先基线检查（git diff/链路图）再日志侦察，对抗分析防确认偏误。**领域无关，每次调用都新建目录**（详见 [steps/log.md](steps/log.md)） | ✅ 每次都新建 |
| `/icode init [<粗略需求>]` | **可选步骤0**：多轮对话产出 `00_init.md`（需求初稿）。**每次调用都新建目录，不复用、不续聊**（详见 [steps/00_init.md](steps/00_init.md)） | ✅ 每次都新建 |
| `/icode start <需求>` | **全流程**：创建/复用目录 → 步骤1→6 串联（**复用规则见下**） | ✅ 创建新目录 / 复用 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划（**复用规则见下**） | ✅ 创建新目录 / 复用 |
| `/icode review [N]` | **仅步骤2**：多轮循环审查 + 独立质疑者对抗验证（N=软上限轮数，默认3；如最后一轮仍有新问题自动延长 +2 轮，最多扩展至 `max(10, N×2)`） | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：三阶段递进复检 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复 + 文档化（产出 `{ICODE_OUT_DIR}/README.md`） | 用最新目录 |

> **`/icode start` / `/icode plan` 的目录复用规则**：启动时检查最新 `.icode_output/.icode_output_N/` 目录：
> - **入口态有歧义 → 问（无论带参与否）**：最新目录 status 为 `init_in_progress` 或 `log_done`（即 init/log 产出了 `00_init.md` 但还没进步骤1，且无 `01_plan.md`）时，**必须问用户**："检测到最近有未完成的初稿/根因 `<摘要>`，是 ① 在此基础上继续（复用目录）/ ② 开全新需求（新建目录）？"——用户选①则复用（命令行参数作补充），选②则新建。**入口态下"带参"不能作为新建的充分条件**——带参可能是补充旧需求也可能是新需求，区分不了，故一律问。**不得擅自复用**（会丢失新需求）也**不得擅自新建**（会丢失 init/log 上下文）。
> - **非入口态带参 → 直接新建**：最新目录已进入步骤1+（有 `01_plan.md` 等，REUSE=0），`/icode start <需求>`/`/icode plan <需求>` 带参一律新建目录。
> - **无参且无入口态可复用 → 报错**：提示先 init/log 或带参新建。
> 详见下文「目录管理」段落。

> **历史检索复用**：`/icode init`、`/icode plan`、`/icode start`、`/icode log` 启动时会自动检索全局索引中相似历史工单并按命令分流注入参考（init→需求要点 / plan→ADR+风险 / log→根因结论+证据），详见下文「历史检索复用」段落。`/icode review`/`merge`/`code`/`deepcheck`/`audit` 不触发检索。

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
/icode init 录制 point_cloud / lidar_imu 转包       # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，会询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1

# 方式D：从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/回充异常 "退桩后不旋转"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md
# ... 对抗分析收敛后，根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，会询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1
```

## 通用规则

### 目录管理

**创建新目录**（用于 `init`，以及 `start` / `plan` 在不满足复用条件时）：
```bash
mkdir -p .icode_output   # 统一父目录，所有产物收纳于此
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR=".icode_output/.icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

**复用 / 创建新目录决策**（仅用于 `start` / `plan`）：
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
- `requirement_summary`：一句话需求摘要（≤200 token），跨工程历史检索的主依据。步骤0首轮基于粗略需求生成，步骤0每轮对话后更新，步骤1完成计划后基于完整计划刷新
- `requirement_points`：需求要点清单（≤10 条字符串），`/icode init` 检索命中时注入用。由步骤0从 `00_init.md`「3.新增需求点」自动提炼，用户无感
- `keywords`：技术关键词（≤8 个），辅助检索匹配
- `indexed`：是否已写入全局索引（防重复写入）
- `ticket_id`：本工单在全局索引中的唯一键（`{工程名}-{N}`，冲突时带 hash 后缀）。步骤0写索引时持久化到 metadata；**跳过步骤0直接 `/icode plan`/`/icode start` 的常规新建目录情况**，在步骤1首次写索引时生成并回填 metadata。供后续步骤检索时排除当前工单
- `code_deviations`：步骤4 编码时主动偏离定稿计划的记录数组（每条含 `plan_said`/`actual_done`/`reason`），供步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段；无偏离写空数组 `[]`

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

### 强制思考前置（所有步骤必须遵守，缺一不可）

**每个步骤开始前，必须先 ultrathink 并完成结构化思考**——这是不可跳过的硬性前置。思考环节不可整体跳过，但**执行载体分主备两档**：

- **首选**：调用 `sequential-thinking` MCP 工具（`mcp__sequential-thinking__sequentialthinking`），至少 3 步，每步对应下方子项之一。上下文能看到该 tool_call 记录即为合规证据。
- **降级**：若当前环境未连接 / 不可用该 MCP（工具列表与 deferred tools 池中均无 `sequential-thinking`），则**必须以显式的「结构化思考」文字块替代**——在回复中先输出一个 `### 结构化思考` 块，逐项完成下方要求的子项（每项一小段，不可省略），再进入产出。该文字块即为合规证据。

> 判定 MCP 是否可用：尝试调用 `sequential-thinking`；若工具不存在则按降级路径走。**两种载体任选其一即可，但思考环节本身不可省略**——未呈现任一形式的思考证据，该步骤产出视为不合规。

**通用流程**（每步执行）：
1. **输出 `ultrathink` 触发词**（触发更长的内部推理 budget）
2. **完成结构化思考**（MCP 优先，不可用则降级文字块），至少 3 步，每步对应下面的子项之一：
   - **需求分解**：把原始需求拆成可验证的子项
   - **方案分析**：候选方案 + 取舍
   - **风险评估**：影响面、边界、依赖、回滚
   - 步骤文件可按本步骤特点增补额外子项（详见各 step 文件）
3. **不得跳过思考直接产出**——所有 Write/Edit 必须在思考证据之后

**层级关系**：
- API 层：`CLAUDE_CODE_EFFORT_LEVEL=max` + `model=opus`（控制推理 effort）
- Hook 层：`UserPromptSubmit` 拦截 `/icode` 命令，缺思考证据时注入提醒
- Prompt 层：本节 + 各 step 文件的"强制思考前置"段

具体每步子项见各步骤文件。

### 反偷懒约束（所有步骤必须遵守，硬性总则）

**禁止以"省 token / 省时间 / 简化产出 / 已大致足够"为由偷工减料**。任何步骤的产出必须**完整**完成本步骤定义的所有子项、维度、轮次和文件，**不得合并、不得跳过、不得降级**。

**典型偷懒行为（一经发现，该步骤产出视为不合规）**：

1. **合并产出**：把多个独立步骤的产物揉进同一个文件（如把审查和定稿合到一个文件、把代码和复检合并写）
2. **跳过子项**：步骤定义里要求的子项/维度/章节漏写或写"略"、"同上"、"参考前文"
3. **降低轮次**：步骤 2 审查、步骤 5 复检要求的轮次未达标就提前终止（如 `max_rounds=3` 只跑 1 轮就声称"已无新问题"）
4. **示意/伪代码替代实现**：步骤 4 编码时用 `// TODO`、`// ...`、`// 省略类似实现` 等占位符代替真实代码
5. **省略产物文件**：步骤明确要求的 `.json` / `.md` / `.log` 文件缺失、或内容是空模板
6. **审查/复检走过场**：审查意见全是"无问题"、"通过"等空泛结论，无具体证据
7. **跳过强制思考**：未输出 `ultrathink` 触发词、未做 MCP 思考或降级文字思考块
8. **跳过自检报告**：修改后未输出完整的 7 维自检报告
9. **跳过对抗验证**：步骤 2.5 逐维审查产出的 issue 未经独立质疑者子代理对抗就下 `confirmed` 结论（**步骤 2.4 已用 Read/Grep 实证验证失败的 issue 例外**，已有铁证可直接 `confirmed` 无需对抗）；或把主代理推理过程喂给质疑者当既定事实（破坏独立性）；或对抗未收敛时和稀泥默认通过
10. **伪造共识 / 默认通过**：issue 无 `evidence_pointer` 证据回指就确认；断言无法实证验证时不标 `[未验证-证据不足]` 而默认通过；对抗裁决分裂时不降级而强行 `confirmed`
11. **跳过历史检索 / 污染产物**：`/icode init`/`/icode plan`/`/icode start` 未执行历史检索注入（全局索引存在时）；或把历史参考内容写进 `00_init.md`/堆砌进 `01_plan.md`（污染工程产物）；或命中工单后照抄历史决策而不顾当前需求差异
12. **跳过偏差回写 / 污染计划**：步骤6 终审未把实质偏差汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段（含无偏差时未写"无实质偏差"留痕）；或回写时改了计划正文（破坏对照价值）；或把细节差异堆砌进备忘（抬高回读噪音）；或步骤4 主动偏离计划未记入 `code_deviations`
13. **注释/日志偷工**：步骤4 编码漏写导出函数/关键分支/数据结构注释；关键路径（错误返回/状态跳转/外部交互/决策分支/降级重试）漏加日志导致运行时无法排查；步骤5 复检/步骤6 终审未把注释完备性+日志覆盖纳入检查（漏检即放行）；或引入项目未使用的新日志库
14. **日志根因分析偷工**：`/icode log` 跳过基线检查直接 grep 日志；对抗分析质疑者未独立 spawn 或未独立读日志（主代理自演）；伪造根因共识（证据不足不降级）；根因结论无日志行回指；或照抄历史工单根因不顾当前症状差异
15. **references 软引用未实读**：step 文件写"见 references/xxx.md"但执行时**未用 Read 工具实读该共享文件**，凭 SKILL.md 概述或记忆执行——新会话里 references 内容不在上下文，凭记忆执行必出错。**引用 references 的步骤，必须先 Read 该文件完整内容再执行**，否则该步骤产出视为不合规
16. **思考块过薄**：强制思考降级文字块每子项只有一句话带过（如"与步骤1一致""通过"），未展开实质推演。**每个思考子项必须有 ≥2 句实质内容**（具体设计点/具体检查项/具体风险），步骤2独立方案构思须≥3设计点、步骤5每阶段须列具体检查项、步骤6追溯矩阵须逐功能点定位代码

**合规要求**：

- 步骤定义里写"至少 N 步" / "至少 N 项" / "N 个维度"——**必须 ≥ N**，不得以"N-1 个已经够了"为由减少
- 步骤定义里列出的所有产物文件——**必须全部产出**，不得以"另一个文件已经包含"为由省略
- 审查/复检的轮次——**必须跑完连续 K 轮无新问题或达到 max_rounds**，不得提前终止
- 编码——**必须完整可编译可运行**，不得留 TODO 占位、不得伪代码、不得"仅给出关键部分"
- 文档章节——**每个章节必须有实质内容**，不得写"略"、"同上"、"参考 XX 文件"
- 步骤 2 对抗验证 / 诚实降级——见上方"典型偷懒行为"第 9/10 条的反面要求（正面执行：每条 issue 经独立质疑者对抗或 2.4 实证、未对抗/未收敛一律 `needs_more_evidence`、无 `evidence_pointer` 不得 `confirmed`、断言无法实证验证时标 `[未验证-证据不足]` 列入 `pending_verification`）
- 历史检索复用——`/icode init`/`/icode plan`/`/icode start` 启动时**必须先执行历史检索**（全局索引存在时），命中后按命令分流注入；历史参考**只进会话上下文**，不得写进 `00_init.md`，不得在 `01_plan.md` 堆砌历史引用（唯一例外：实质借鉴的 ADR 可在"理由"末尾加一句溯源）；命中工单的参考**只作启发**，不得照抄
- 实现偏差回写——步骤6 终审**必须汇总**步骤4 `code_deviations` + 步骤5 `05_reverse.json` + 步骤6 终审偏差，回写到 `03_plan_final.md` 末尾步骤3预留的「实现偏差备忘」段；**只填充预留段、不改计划正文**；只标实质不一致（接口/数据结构/功能有无/实现方式/异常策略变更），细节差异不标；无偏差也必须写"无实质偏差"留痕；每条偏差必须能回指计划章节 + 代码行
- 注释与日志完备性——步骤4 编码**必须**满足注释增强（导出函数/接口/关键分支/数据结构/算法）与日志覆盖（错误返回/状态跳转/外部交互/决策分支/降级重试关键路径有日志，遵循项目既有日志风格，不引入新库）；步骤5 复检（Fixed 第5维度 + Free A15）+ 步骤6 终审（代码质量维度）**必须检查**注释完备性与日志覆盖，漏写记为 issue，不得放行
- 日志根因分析——`/icode log` **必须**先做基线检查（git diff/状态链路图/文档参考不盲信）再 grep 日志；对抗分析**必须**独立 spawn 3 质疑者子代理、独立读日志；根因结论**必须**有日志行回指（节点+时间+原文）；证据不足**必须**诚实降级"未定论"；历史根因只作启发不得照抄

**为什么强制**：本工作流的价值在于"完整闭环 + 多轮审查 + 充分思考"，任何偷懒都会破坏闭环、放过隐藏缺陷、最终把质量负担转嫁给后续维护。**省下的 token 远小于因质量下降而需返工的代价**。

**用户授权例外**：仅当用户**明确显式**说"跳过步骤 X" / "只跑 1 轮"等指令时才可降级，且必须在产出文件中明确记录"已按用户指令降级，跳过的内容包括：..."。模型不得自行降级、不得擅自合并。

### 全流程串联规则

`/icode start` 执行步骤1后，如果会话断开，恢复时必须读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。不可跳过未完成的步骤。

**续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 和 `"log"` 仅作为"已走过步骤0/log入口"的标记，**不影响**推进逻辑。例：`["0"]`/`["log"]`/`["log","0"]` → 下一步是步骤1；`["0","1"]`/`["log","1"]` → 下一步是步骤2。

步骤 2/5 的 `*_in_progress` 状态 + 轮次计数器支持**断点续跑**（步骤0的 `init_in_progress` 不参与，详见上节"两种语义"）；步骤 4 的 `code_in_progress`（编译失败时保留）支持**整体续跑**——重跑步骤4时**在已写入的代码基础上继续修复**（编译失败时代码文件和 `code_files` 已保留落盘，不丢弃、不从计划重新编码），不带轮次断点。

### 历史检索复用（跨工程/跨工单借鉴）

**痛点**：每次 `/icode` 都是冷启动，过往相似需求的计划/决策/踩坑无法被新需求复用。本机制在不破坏工程隔离、不撑爆上下文的前提下，让新需求能主动检索历史相似工单并定点注入参考。

**全局索引**（不污染任何工程，不放技能目录）：

- 索引文件：`~/.claude/icode_data/index.json`（首次运行自动创建）。**路径说明**：`~` 是当前用户主目录，由 Claude Code 工具层跨平台解析（Linux/macOS/Windows 通用），与技能目录 `~/.claude/skills/icode/` 同源。技能文件**禁止硬编码**任何具体用户路径（如 `/home/xxx`、`C:\Users\xxx`），所有全局路径必须用 `~` 表达，确保技能可移植
- 每条记录：`ticket_id`(`{工程名}-{N}`，工程名冲突时追加 `project_path` 短 hash 后缀保唯一)、`project_path`、`out_dir`、`requirement_summary`(≤200token)、`requirement_points`(≤10条)、`keywords`(≤8个)、`has_00_init`/`has_plan`、`status`、`created_at`。**`has_00_init` 语义 = 该工单是否已产出 `00_init.md`（走过 init 或 log，log 也会产出 00_init.md），与"是否走过步骤0 init"解耦**——log 未走过 init 但产出 00_init.md 故也为 true
- **只存指针和摘要，不存产物正文**。产物仍在各工程 `.icode_output/`，工程隔离不破坏。

**写索引时机**：
- `/icode log` 产出 `log_analysis.md` + `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`（根因摘要）+`requirement_points`（修复要点）+`has_00_init=true`+`status=log_done`
- 步骤0 首轮写 `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`+空 `requirement_points`+`has_00_init=true`
- 步骤0 每轮对话更新后：刷新 `requirement_points`（从「3.新增需求点」自动提炼）+ `requirement_summary`
- 步骤1 写完 `01_plan.md` 后：刷新 `requirement_summary`（基于完整计划）+ `has_plan=true`；**常规新建目录首跑时**（跳过步骤0）在此首次生成 `ticket_id` 并回填 metadata、首次写入索引条目（`has_00_init=false`）
- 步骤6 终审完成后：刷新 `status=completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新

**检索注入流程**（`/icode init`、`/icode log`、`/icode plan`、`/icode start` 共用检索，分流注入）：

1. **检索阶段**（强制思考**之前**；`/icode init`/`/icode log` 在建目录后检索，`/icode plan`/`/icode start` 在目录管理+确定需求来源后检索——确保用完整需求做相关性判断）：Read 全局索引，主代理扫所有 `requirement_summary`，结合当前需求/症状判断相关性，选 top-2 命中（明确无关则 0 条）。排除当前正在写的 `ticket_id`，不自我参考。
2. **注入阶段**（按命令分流）：

| 命令 | 命中后注入内容 | 来源 | 体积上限 |
|------|--------------|------|---------|
| `/icode init` | 命中工单的 `requirement_points`（需求要点清单） | 读 metadata 或 `00_init.md`「3.新增需求点」 | ≤500 token/条 |
| `/icode plan` / `/icode start` | 命中工单的 **ADR 章节 + 风险评估章节** | 定点读 `01_plan.md` 对应章节（**不读全文**） | ≤1K token/条 |
| `/icode log` | 命中工单的 **根因结论 + 决定性证据** | 定点读 `log_analysis.md`「核心结论 + 决定性证据」章节（**不读全文**） | ≤800 token/条 |

3. **注入形式**：历史参考作为主代理的**思考输入**，在强制思考文字块里加一节「历史参考」，影响后续产出质量。**零命中不注入，不强凑参考**。

**防撑爆三道闸门**：
- **索引体积**：全局索引单条只存摘要+要点+关键词，整体 <2K token
- **命中数**：最多 top-2
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
- **工程污染防护**：`.icode_output/` 是 icode 产物目录，建议在工程 `.gitignore` 中加入 `.icode_output/`，避免产物误提交；icode 本身**不自动修改工程的 `.gitignore`**（工程配置由用户掌控）。历史检索的全局索引位于 `~/.claude/icode_data/`，不在任何工程内，无污染风险
- **跨会话恢复**：运行 `ls -d .icode_output/.icode_output_*` 确认目录后，直接调用对应步骤即可
- **中断恢复**：重新执行某步骤可覆盖该步骤输出

## 各步骤详细规则

各步骤的详细 prompt、维度要求、执行流程请读取对应文件：

| 步骤 | 命令 | 详细文件 |
|------|------|----------|
| log | `log` | [steps/log.md](steps/log.md) |
| 0 | `init` | [steps/00_init.md](steps/00_init.md) |
| 1 | `plan` / `start` | [steps/01_plan.md](steps/01_plan.md) |
| 2 | `review` | [steps/02_review.md](steps/02_review.md) |
| 3 | `merge` | [steps/03_merge.md](steps/03_merge.md) |
| 4 | `code` | [steps/04_code.md](steps/04_code.md) |
| 5 | `deepcheck` | [steps/05_deepcheck.md](steps/05_deepcheck.md) |
| 6 | `audit` | [steps/06_audit.md](steps/06_audit.md) |

**执行步骤时，必须先读取对应的 `steps/XX_*.md` 文件，按其中的详细指令执行。**

## 共享规则文件（references/）

各 step 文件不再重复定义跨步骤共享的规则，统一引用 `references/` 下的共享文件。执行某步骤时若该步骤引用了共享文件，**必须先用 Read 工具实读该文件完整内容**（不得凭 SKILL.md 概述或记忆执行，否则产出不合规——见反偷懒第15条）：

| 共享文件 | 内容 | 引用方 |
|---------|------|--------|
| [references/thinking.md](references/thinking.md) | 强制思考前置（ultrathink/MCP/降级文字块/各步子项） | 所有 step |
| [references/adversarial.md](references/adversarial.md) | 对抗分析模式（3质疑者/裁决优先级/诚实降级/证据回指） | 02_review / log |
| [references/dir_and_metadata.md](references/dir_and_metadata.md) | 目录管理 + ticket_id 生成 + 全局索引写入 + metadata 模板 | init / log / plan |
