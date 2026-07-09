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

### 2. 历史检索（与 `/icode start` 计划模式一致）

- 读 `~/.claude/icode_data/index.json`，**两段式检索**：段一 keywords Jaccard 粗筛取 ≤10 候选（零 token，可复活预扫后排除剩余 stale/当前 `ticket_id`），段二候选 keywords+requirement_points 精读打分选 top-N 命中（N 由梯度决定，明确无关则 0 条），过时校验后注入 ADR+风险章节
- 排除当前 `ticket_id`，不自我参考
- **段零·工程文档检索**（与历史检索并行，候选合并排序）：`resolve_project_id(cwd)`（支持 git-root / repo-root 双模式，`repo` 根模式从 cwd 向上找 `.repo/manifest.xml`）→ `ls ~/.claude/icode_data/project_docs/<project_id>/`，逐章读前 50 行按 KEYS 匹配 → 读 `project_docs/<project_id>/_meta.json` 的 `module_deps` 列表，对每个 dep 查 `module_docs/<dep.key>/` → **反查父项目**（cwd 在子仓库 + path 匹配 manifest 的 `<project path>` → 父 repo-root 也纳入检索）；命中按 `[小节锚点]` 定点读小节；无知识库则零命中（ℹ️ 不阻塞）。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」+「module_docs 工程模块库」段；**stale 章节降级注入摘要不注正文 + module commit 一致性校验**见「stale 章节降级注入」「步骤 3 commit 一致性校验」段
- **注入防重复**（两源共用 `_inject_cache.json`）：无缓存则创建空 `{"ticket_id":"<本工单>","injections":[]}`；注入前按 `(source, ref_id, slice)` 查缓存去重，已注入的跳过。历史源 slice=`adr_risks`；段零 slice=`section:<file>`。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段

### 3. 创建 metadata

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

### 4. 入口警告

启动时打印（**不阻塞**）：

```
⚠️ /icode fast 模式：
   - 步骤2 review 固定 1 轮无对抗验证
   - 步骤5 deepcheck 只跑 Reverse 阶段（跳过 Fixed/Free）
   - 依赖 plan+1 轮 review+Reverse 单阶段+audit 四道关卡
   - 复杂需求（跨模块/新架构/安全敏感）建议改用 /icode start 全流程
```

### 5. 串联执行

按以下顺序调用对应步骤文件：

| 顺序 | 命令 | 步骤文件 | 产出 | status 转换 |
|---|---|---|---|---|
| 1 | 步骤1 plan | `steps/01_plan.md` | `01_plan.md` | → `plan_done` |
| 2 | 步骤2 review | `steps/02_review.md` | `02_review.md` | → `review_done` |
| 3 | 步骤3 merge | `steps/03_merge.md` | `03_plan_final.md` | → `plan_finalized` |
| 4 | 步骤4 code | `steps/04_code.md` | 代码文件 | → `code_done` |
| 5 | 步骤5 deepcheck | `steps/05_deepcheck.md` | `05_deepcheck.md` | → `deepcheck_done` |
| 6 | 步骤6 audit | `steps/06_audit.md` | `06_audit.md` | → `completed` |

**步骤2/5 的 fast 模式行为**（由各自步骤文件读 `metadata.mode` 字段判定）：

- **步骤2 review**：检测到 `mode=="fast"` 时，按**是否带参 N**区分两种场景（详见 [steps/02_review.md](02_review.md) 顶部「fast 模式行为」段）：
  - **场景一·自动串联**（`/icode fast` 调起、未带参 N，`FAST_LOCKED=true`）：`max_rounds` 强制 1、跳过步骤 2.5.5 对抗（issue 直接标 `confirmed`，**降级为单视角审查**）、循环控制 `total_rounds >= 1` 直接终止。输出 `▶ 步骤2 fast 模式：1 轮审查，无对抗验证`
  - **场景二·单步升级**（fast 工单上显式跑 `/icode review N`，`FAST_LOCKED=false`）：**N 优先级最高**——按 N 轮跑 + 恢复对抗验证 + 走正常 (a)(b)(c) 循环控制，与 full 模式一致（这是 fast→full 升级机制，用户显式表达升级意图）

- **步骤5 deepcheck**：检测到 `mode=="fast"` 时
  - 跑完 Reverse 阶段后**直接终止**（不切换到 Fixed / Free）
  - 跳过 Free 阶段 A6 独立 3 质疑者 spawn（不适用）
  - 输出标记：`▶ 步骤5 fast 模式：仅 Reverse 阶段`

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

1. **每步仍必须执行强制思考前置**（先 Read `references/thinking.md` + `references/anti_laziness.md`，缺证据视为不合规）
2. **每步仍必须产出对应产物文件**（不跳过 01_plan.md / 02_review.md / 03_plan_final.md / 05_deepcheck.md / 06_audit.md）
3. **入口警告必须如实打印**（不静默跳过提示）
4. **跳过对抗验证不是"省略证据"**，而是承认 fast 模式下没有对抗资源——用户自负其责
5. **fast 模式不能用于：声称"做了深度审查"**——明确"1 轮无对抗"的边界，避免误导后续读者
