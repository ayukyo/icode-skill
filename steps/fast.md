# 命令 fast — 精简全流程（plan → review(1轮无对抗) → merge → code → deepcheck(Reverse 单阶段) → audit）

**命令**: `/icode fast <需求>`
**目标**: 保留全流程 6 步结构，每步只跑最关键的最小动作，链路耗时约为全流程的 65%
**会话**: 主会话

## 与全流程（`/icode start`）的差异

| 维度 | full（默认） | fast |
|---|---|---|
| 步骤2 review 轮数 | 默认3轮，可自动延长至 `max(10, N×2)` | **固定 1 轮** |
| 步骤2 review 对抗验证 | 3 质疑者（证据/替代/充分性）独立 spawn | **跳过对抗** |
| 步骤5 deepcheck 阶段 | Reverse → Fixed → Free 三阶段循环 | **只跑 Reverse 阶段** |
| 步骤5 deepcheck Free 对抗 | 3 质疑者 | 不适用（不跑 Free） |
| 入口警告 | 无 | **打印警告，用户自负其责** |
| `metadata.mode` | `"full"`（默认，可省略） | `"fast"` |

**保留一致的产物结构**：fast 与 full 产出**同样命名**的产物文件（`01_plan.md` / `02_review.md` / `03_plan_final.md` / `05_deepcheck.md` / `06_audit.md`）。差异只在每步**动作的最小集**——下游所有步骤（merge/audit/readme）无需感知模式差异。

## 适用场景

- 单文件或少量文件改动（建议 < 5 文件）
- 已有相似工程经验、不需要多轮审查收敛
- 改动边界清晰、不涉及架构变更或新协议引入
- 紧急修复、对交付速度敏感

**不适用场景**（建议回退到 `/icode start` 全流程）：跨模块重构、新架构引入、安全敏感模块改动、跨协议/跨仓集成、需要对抗验证防确认偏误的场景。

## 执行流程

### 1. 目录决策（与 `/icode start` 复用规则一致）

```bash
mkdir -p .icode_output
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
REUSE=0
if [ -n "$LAST" ]; then
  CAND=".icode_output/.icode_output_${LAST}"
  if [ -f "$CAND/.ico_metadata.json" ] && [ -f "$CAND/00_init.md" ] && [ ! -f "$CAND/01_plan.md" ]; then
    STATUS=$(grep -oP '"status"\s*:\s*"\K[^"]+' "$CAND/.ico_metadata.json")
    case "$STATUS" in
      init_in_progress|log_done) REUSE=2 ;;
    esac
  fi
fi
# REUSE=2 问用户；REUSE=0 带参新建 / 无参报错
```

复用语义：复用入口态目录时，命令参数作补充输入，主体需求取自 `00_init.md`。

> **历史检索 + 段零工程文档检索**：委托给步骤4 串联执行的 [01_plan.md](01_plan.md) 步骤2，fast 不单独检索。原因：slice 与 plan 完全相同（历史 `adr_risks` / 段零 `section:<file>`），`_inject_cache.json` 按 `(source, ref_id, slice)` 去重兜底，fast 单独检索是冗余动作（检索白跑，注入被 plan 截胡），与 fast 省 token 目标矛盾。详见 [SKILL.md](../SKILL.md)「历史检索复用」段。

### 2. 创建 metadata

`/icode fast` 新建目录时，`.ico_metadata.json` 写入：

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "plan_done",
  "completed_steps": ["1"],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组，从需求/计划技术栈提炼，不得为空--空 keywords 工单无法被段一粗筛命中}",
  "indexed": false,
  "ticket_id": "{写入索引后回填}",
  "mode": "fast",
  "max_rounds": 1
}
```

**新增字段**（仅 fast 模式有值，full 模式可省略或留空，默认 `"full"`）：
- `mode`：工单模式，`"fast"` / `"full"`（默认 `"full"`）
- `max_rounds`：步骤2 软上限，fast 固定为 1（full 默认 3）

复用入口态目录时，沿用现有 metadata，只追加/更新 `mode="fast"`、`max_rounds=1`。

### 3. 入口警告

启动时打印（**不阻塞**）：

```
⚠️ /icode fast 模式：
   - 步骤2 review 固定 1 轮无对抗验证
   - 步骤5 deepcheck 只跑 Reverse 阶段（跳过 Fixed/Free）
   - 依赖 plan+1 轮 review+Reverse 单阶段+audit 四道关卡
   - 复杂需求（跨模块/新架构/安全敏感）建议改用 /icode start 全流程
```

### 4. 串联执行

按以下顺序调用对应步骤文件：

| 顺序 | 命令 | 步骤文件 | 产出 | status 转换 |
|---|---|---|---|---|
| 1 | 步骤1 plan | `steps/01_plan.md` | `01_plan.md` | → `plan_done` |
| 2 | 步骤2 review | `steps/02_review.md` | `02_review.md` | → `review_done` |
| 3 | 步骤3 merge | `steps/03_merge.md` | `03_plan_final.md` | → `plan_finalized` |
| 4 | 步骤4 code（含末尾 1.5 "Code Review Fix" 4 维度复检） | `steps/04_code.md` | 代码文件 + `04_code_review_fix.md` | → `code_done` |
| 5 | 步骤5 deepcheck | `steps/05_deepcheck.md` | `05_deepcheck.md` | → `deepcheck_done` |
| 6 | 步骤6 audit | `steps/06_audit.md` | `06_audit.md` | → `completed` |

**步骤2/5 的 fast 模式行为**（由各自步骤文件读 `metadata.mode` 字段判定）：

- **步骤2 review**：检测到 `mode=="fast"` 时，按**是否带参 N**区分两种场景（详见 [steps/02_review.md](02_review.md) 顶部「fast 模式行为」段）：
  - **场景一·自动串联**（`/icode fast` 调起、未带参 N，`FAST_LOCKED=true`）：`max_rounds` 强制 1、跳过步骤 2.5.5 对抗（issue 直接标 `confirmed`，**降级为单视角审查**）、循环控制 `total_rounds >= 1` 直接终止。输出 `▶ 步骤2 fast 模式：1 轮审查，无对抗验证`
  - **场景二·单步升级**（fast 工单上显式跑 `/icode review N`，`FAST_LOCKED=false`）：**N 优先级最高**——按 N 轮跑 + 恢复对抗验证 + 走正常 (a)(b)(c) 循环控制，与 full 模式一致（这是 fast→full 升级机制，用户显式表达升级意图）

- **步骤4 code（含末尾 1.5 "Code Review Fix"）**：fast 模式下也执行 1.5 复检（**4 维度对所有工单都触发**，不分模式）——同事提示词的工程化复检机制不因 fast 而省略。差异：fast 模式下复检节奏紧凑，但仍产出 `04_code_review_fix.md` 并落盘 `code_review_fix_with_issues` 字段。详见 [steps/04_code.md](04_code.md)「1.5 子段」

- **步骤5 deepcheck**：检测到 `mode=="fast"` 时
  - 跑完 Reverse 阶段后**直接终止**（不切换到 Fixed / Free）
  - 跳过 Free 阶段 A6 独立 3 质疑者 spawn（不适用）
  - 输出标记：`▶ 步骤5 fast 模式：仅 Reverse 阶段`
  - **读取 `04_code_review_fix.md`**（若存在）作为补充参考：若 `code_review_fix_with_issues=true`，deepcheck 入口输出警告⚠️，Reverse 阶段重点关注 4 维度未通过项

## 中断续跑

复用现有 metadata 续跑机制。fast 模式下 `completed_steps` 预期：`["1","2","3","4","5","6"]`。

续跑判定（找 1~6 范围最大已完成步骤推进）天然兼容：每步都会落盘 `status` 与对应计数器，中断后重启可从断点恢复。

## 与 full 模式的切换

- **fast → full 升级**：**允许**。用户能在 fast 工单上单独跑 `/icode review N` 或 `/icode deepcheck` 补全剩余步骤。**单步命令仍读 `mode` 字段，但用户用参数 N 显式表达升级意图时，参数优先级最高**（详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「步骤2/5 读 mode 字段的契约」段）。已走完 fast 的工单再跑 `/icode review 5` 会被识别为 `status=completed` 重新审查（按步骤 2.3 规则），不破坏数据。
- **full → fast 降级**：**不允许**（单步命令不强制按 fast 模式执行；用户若想走 fast 应改用 `/icode fast` 重启链路）。
- **同一工单跨模式混跑**：未限制，`completed_steps` 与 `status` 反映实际走过的步骤。

## 与其他命令的关系

- `/icode help`：输出时包含 fast 命令说明
- `/icode status`：识别 `mode` 字段，输出「工单模式：fast/full」
- `/icode readme`：步骤7 不区分模式，统一生成交付报告

## 反偷懒约束

fast 模式的"精简"不等于"偷懒"：

1. **每步仍必须执行强制思考前置**（按 `references/thinking_core.md`「强制思考前置·统一契约」段执行三件套 Read，缺证据视为不合规）
2. **每步仍必须产出对应产物文件**（不跳过 01_plan.md / 02_review.md / 03_plan_final.md / 05_deepcheck.md / 06_audit.md）
3. **入口警告必须如实打印**（不静默跳过提示）
4. **跳过对抗验证不是"省略证据"**，而是承认 fast 模式下没有对抗资源——用户自负其责
5. **fast 模式不能用于：声称"做了深度审查"**——明确"1 轮无对抗"的边界，避免误导后续读者
## MCP 推荐（v2.2 强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| serena | 🟢* | 代码理解 + 符号编辑--有可索引源码时（plan/deepcheck 内嵌） |
| context7 | 🟢* | 库 API 核对--涉及第三方库时 |
| vision-bridge | 🟢* | UI 截图--用户给图时 |
| memory | 🟢* | read_graph 查跨工单记忆--有历史工单时 |
| playwright | ⚪ | fast 模式不跑 E2E |
| **cheap-research** | 🟢* | **降本增强**：plan（audit_facts/retrieve_similar/apply_migration）+ review（diff_summary/fill_template/scan_patterns/trace_refs）+ code（apply_migration）+ deepcheck（diff_summary/summarize）+ audit（fill_template/summarize）每步都有单闸门入选子任务；未装走 Agent(model="haiku") 兜底，不阻塞。**不接管决策**：3 质疑者对抗/架构决策/终审裁决/修复方案一律不走（零灰区原则）。详见 [mcp_per_step.md](../references/mcp_per_step.md) |

**强制约束（v2.2）**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
