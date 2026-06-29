# 目录管理 + ticket_id + 索引写入 + metadata 模板（共享规则）

> 本文件汇集 icode "创建工单目录 / 生成 ticket_id / 写全局索引 / metadata 模板" 的共享规则。入口命令（init/log）和步骤1在创建工单时引用本文件，不重复定义。

## 目录管理

### 创建新目录（用于 init / log，以及 start / plan 在不满足复用条件时）

```bash
mkdir -p .icode_output   # 统一父目录，所有产物收纳于此
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR=".icode_output/.icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

### 复用 / 创建新目录决策（仅用于 start / plan）

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

### 复用决策三档

- `REUSE=2`（入口态有歧义）→ **必须问用户"复用 / 新建"**（无论命令是否带参——带参可能是补充旧需求也可能是新需求，区分不了，故一律问），按答复定；复用时将 `00_init.md` 作为步骤1主要需求输入（命令行参数作补充）
- `REUSE=0`（非入口态）→ 带参新建、无参报错
- **不得擅自复用**（会丢失新需求）也**不得擅自新建**（会丢失 init/log 上下文）

### 检测最新目录（用于 review / merge / code / deepcheck / audit）

```bash
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  echo "错误：没有找到 .icode_output/.icode_output_N 目录，请先运行 /icode start <需求> 或 /icode init"
  exit 1
fi
ICODE_OUT_DIR=".icode_output/.icode_output_${LAST}"
```

## ticket_id 生成规则

- `ticket_id` = `{工程名}-{N}`（工程名取 `project_path` 的 basename；N 为当前 `.icode_output_N` 的 N）
- **工程名冲突处理**：若索引中已存在相同 `{工程名}-{N}` 但 `project_path` 不同的条目，ticket_id 追加 `project_path` 的短 hash 后缀（如 `myproject-1-a3f2`）以保唯一
- **入口命令（init/log）共享 N 序列**：init/log 各自创建新目录时，N 是当前目录下**所有** `.icode_output_*` 中的最大 N + 1，不区分 init/log（demo-5 是 init，demo-9 是 log，N 单调递增）
- 生成后**回填 metadata 的 `ticket_id` 字段**，供后续步骤检索时排除当前工单（避免反推）
- **唯一性保证**：写索引前必须 Python 解析 index.json 检查无重复 ticket_id；如发现重复（手工误操作导致），用 `python3 -c` 脚本去重（保留 status 最靠后的）

## 全局索引写入（首次写入）

Read `~/.claude/icode_data/index.json`（不存在则创建 `{"version":"1","updated_at":"当前时间","tickets":[]}`），追加一条新记录：

- `ticket_id` / `project_path`（当前工程根绝对路径）/ `out_dir`（`.icode_output/.icode_output_{N}`）
- `requirement_summary` / `requirement_points` / `keywords` 取自本步骤 metadata
- 入口命令的标记：
  - `/icode log` 产出：`has_00_init` = true（已产出 00_init.md）、`has_plan` = false、`status` = `log_done`
  - 步骤0 init 首轮：`has_00_init` = true、`has_plan` = false、`status` = `init_in_progress`、`requirement_points` 暂空
  - 步骤1 常规新建首跑（跳过 init/log）：`has_00_init` = false、`has_plan` = true、`status` = `plan_done`
- `created_at` = 当前时间
- `last_used_at` = 当前时间（**新增**：检索命中时更新，LRU淘汰依据；首次写入=created_at）
- `hit_count` = 0（**新增**：检索命中时+1，达10永久保留）
- `stale` = false（**新增**：过时校验发现代码锚点失效置true，stale工单不再注入不续期）
- 写回 index.json，置 metadata `indexed = true`、`ticket_id = {生成的 ticket_id}`
- **写入后执行 LRU 淘汰**（见下方「索引淘汰规则」）

## 索引条目更新（已有 ticket_id 的情况）

按 metadata 的 `ticket_id` 定位 index.json 中本工单条目，更新对应字段：

- 步骤0 每轮对话后：刷新 `requirement_summary` / `requirement_points`（从 `00_init.md`「3.新增需求点」自动提炼）
- 步骤1 写完 `01_plan.md` 后：`requirement_summary`（基于完整计划刷新）、`has_plan` = true、`status` = `plan_done`
- 步骤6 终审后：`status` = `completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新

## 检索命中续期 + 过时校验（检索阶段执行）

检索阶段（init/log/plan/start 启动时扫 index.json）采用**两段式检索**（详见 SKILL.md「检索注入流程」）——段一 keywords Jaccard 粗筛取 ≤20 候选（零 token，排除 stale/当前 ticket_id），段二只把候选 summary 喂 LLM 精读打分选 top-N 命中（N 由梯度决定）。对 top-N 命中工单，**先做过时校验，再续期**：

### 过时校验（防注入过时信息）

索引存的是工单**当时的摘要**，但工程会迭代，老工单的 ADR/需求可能已被后续工单推翻。命中过时工单注入会误导。

**校验方法**（只查代码锚点是否还在，不重读全文，控 token）：
1. 读该工单 `01_plan.md` 的 ADR 章节，提取其涉及的**代码锚点**（如"calc.c mul_overflows"、"calc.h calc_sqrt"）
2. 用 Grep 快速确认锚点代码是否仍存在于当前工程
3. **锚点不存在**（代码已删除/重构/重命名）→ 该工单过时，置 `stale=true`，**跳过注入**（即使 hit_count 高也不注入）
4. **锚点存在** → 工单仍有效，正常注入

### stale 字段

- index.json 每条加 `stale`（默认 false）
- `stale=true` 的工单：**不再注入**（检索时跳过），但仍保留在索引（不删，留追溯）
- stale 工单**不被段一粗筛命中**（关键词交集前即排除）、不参与 hit_count 续期（不再被命中）
- stale 由三种途径触发：①检索命中准备注入前被动校验锚点失效；②每次写索引后主动扫描最旧 K 条锚点失效；③僵尸未完成态超时降级（见「索引淘汰规则」规则 5）
- 若该工单产物被刷新（如重跑步骤6终审），可手动重置 `stale=false`

### 续期（校验通过才续期）

- `last_used_at` = 当前时间（续期，LRU不淘汰）
- `hit_count` += 1（累计命中次数）
- 写回 index.json

> **原子同步（强制，防数据失真）**：`last_used_at` 与 `hit_count` 必须在**同一次写回**中同步更新，**不得只更新其一**。历史 bug：某次命中只更新了 `last_used_at` 却漏 `hit_count` 自增，导致 `last_used_at` 被刷新但 `hit_count=0`——这条工单在 LRU 排序里因 hit_count 低沉底、却被 last_used_at"续期"误导排序，**直接破坏淘汰准确性**。续期写入时若任一字段更新失败，整次续期回滚不落盘。

## 索引淘汰规则（LRU + 命中续期 + 永久保留）

**目的**：index.json 是检索缓存非档案，随工单增长会膨胀。靠 LRU 淘汰失去复用价值的老工单，保留高价值工单。淘汰只删索引条目，**不删各工程 `.icode_output/` 产物**（产物保留，索引只是指针）。

**触发时机**：每次写索引（首次写入/条目更新/命中续期）后执行：先排序，再淘汰扫描，最后主动 stale 扫描。

**排序规则**（写入时重排 tickets 数组）：按 `hit_count` 降序、同值按 `last_used_at` 降序。高复用价值 + 近期被用的工单排前，段一粗筛扫 `keywords` 时主代理先看到高价值项，判断相关性更快；LRU 淘汰时最老的自然在末尾。

**淘汰规则**：
1. **容量上限 200 条**：tickets 数组超 200 时触发淘汰
2. **永久保留**：`hit_count >= 10` 的工单永久不淘汰（被复用≥10 次的高价值工单）
3. **未完成态保留**：`status` 为 `init_in_progress`/`log_done`/`log_in_progress`/`review_in_progress`/`deepcheck_in_progress`/`code_in_progress` 的工单**默认**不淘汰（流程未结束）——**但有超时降级例外**（见规则 5）
4. **LRU 淘汰**：超上限时，在 `hit_count < 10` 且 `status = completed/review_done/deepcheck_done/plan_done/plan_finalized`（已完成或已推进态）的工单中，淘汰 `last_used_at` 最老的（数组末尾），直到条目数 ≤ 200
5. **僵尸未完成态超时降级**：未完成态工单（init/log/review/deepcheck/code in_progress）若 `last_used_at` 距当前**超过 30 天**无更新，视为"建了就扔"的僵尸工单——**不删 status，改置 `stale=true`**（复用 stale 机制，不污染 status 语义），降级后该条不再受规则 3 的未完成态保护，纳入规则 4 可淘汰集。**触发时机**：每次写索引触发淘汰扫描时，顺带检查所有未完成态工单是否超时。区分"正在用"（30 天内有更新）与"建了就扔"（超时）
6. **淘汰不报错**：静默移除，用户无感

**主动 stale 扫描**（防过时信息堆积，规则 5 之外的第二道清理）：

- **现状漏洞**：原设计 stale 校验只在"检索命中准备注入前"才做——没被命中的老条目永远 stale=false，永远不会被这个机制清理，stale 清理形同虚设。
- **新增主动扫描**：每次写索引触发淘汰扫描后，**顺带对 `last_used_at` 最旧的 K 条**（K=min(10, 当前条目数)）做一次代码锚点 Grep 校验（方法同「过时校验」段）。锚点失效→置 `stale=true`。把"被动校验"变"主动清理"。
- **控 token**：只扫最旧的 K 条（最可能过时），不扫全量；Grep 单条锚点 <1K token。
- stale 工单仍受排序影响沉在数组末尾，但**不注入不续期**，可在后续 LRU 淘汰中被规则 4 移除（若它已达可淘汰态）或长期留追溯（若未完成态）。

## .ico_metadata.json 模板

入口命令（init/log）和步骤1常规新建目录时创建。完整字段定义见 SKILL.md「元信息文件」段，此处仅列入口创建时的最小模板：

### `/icode log` 产出后

```json
{
  "requirement": "{根因转成的修复需求描述}",
  "created_at": "当前时间",
  "status": "log_done",
  "completed_steps": ["log"],
  "code_files": [],
  "requirement_summary": "{根因一句话摘要，≤200 token}",
  "requirement_points": ["修复要点1", "修复要点2"],
  "keywords": "{≤8个技术关键词}",
  "indexed": false,
  "ticket_id": "{写入索引后回填}"
}
```

### 步骤0 init 首轮

```json
{
  "requirement": "{用户输入的原始需求，无参数时填空字符串}",
  "created_at": "当前时间",
  "status": "init_in_progress",
  "completed_steps": ["0"],
  "code_files": [],
  "requirement_summary": "{基于粗略需求的一句话摘要，≤200 token；无参数时填空字符串}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组，无参数时填空数组}",
  "indexed": false,
  "ticket_id": "{步骤8 写入索引后回填}"
}
```

### 步骤1 常规新建目录

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "plan_done",
  "completed_steps": ["1"],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤200 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{步骤5 刷新索引时回填}",
  "mode": "full",
  "max_rounds": 3
}
```

> 复用步骤0目录的情况：metadata 已存在，步骤1只更新 status（→plan_done）、completed_steps（追加"1"）、刷新检索字段，不重建。

### `/icode fast` 新建目录

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "plan_done",
  "completed_steps": ["1"],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤200 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{写入索引后回填}",
  "mode": "fast",
  "max_rounds": 1
}
```

**`mode` 字段**（新增，可选，默认 `"full"`）：

- `"full"`：全流程模式（`/icode start`），步骤2 review 默认 3 轮 + 对抗，步骤5 deepcheck 三阶段循环
- `"fast"`：精简模式（`/icode fast`），步骤2 review 固定 1 轮无对抗，步骤5 deepcheck 只跑 Reverse
- **字段缺失**视为 `"full"`（向后兼容旧 metadata）

**`max_rounds` 字段**（新增，可选）：

- 步骤2 review 软上限轮数。`"full"` 默认 3；`"fast"` 下区分场景（见下「步骤2/5 读 mode 字段的契约」）
- 用户可通过 `/icode review N` 临时覆盖（仅本轮）。`mode="fast"` 时：自动串联（`/icode fast` 调起、未带参 N）固定 1 轮；fast 工单上显式跑 `/icode review N`（带参）则 N 生效触发升级（场景二）
- 字段缺失视为 3（向后兼容）

**步骤2/5 读 mode 字段的契约**：

- 步骤2 review：开头读 metadata.mode，若 `"fast"` 则按「是否带参 N」区分两种场景（详见 [steps/02_review.md](../steps/02_review.md)「fast 模式行为（区分两种场景）」段）——自动串联锁死1轮无对抗；单步命令 `/icode review N` 触发升级跑 N 轮+对抗
- 步骤5 deepcheck：开头读 metadata.mode，若 `"fast"` 则只跑 Reverse 阶段（详见 [steps/05_deepcheck.md](../steps/05_deepcheck.md)「fast 模式降级」段）
- 单步命令（`/icode review N` / `/icode deepcheck`）独立调用时**仍读 mode 字段**——这是 fast→full 升级机制的核心：
  - **fast→full 升级**：fast 工单上用户主动跑 `/icode review 5` 想做更深度审查时，**参数 N 覆盖 mode**（意图明确优先于工单模式），按 full 模式跑 N 轮+对抗（**此场景下 fast 的 `param_max_rounds` 忽略被绕开**——用户用参数显式表达升级意图，参数优先级最高）
  - **full→fast 降级**：不允许——单步命令不强制按 fast 模式执行（用户若想走 fast 应改用 `/icode fast` 重启链路，而不是在 full 工单上强制 fast 降级）
  - 单步命令读 mode 字段只用于 **状态显示**（如 `▶ 步骤2 检测到 fast 模式，但 N=5 显式升级，按 5 轮执行`），不强制降级
