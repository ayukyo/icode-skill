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

> **"当前时间"取值约定（强制，防 LRU 失效）**：`updated_at` / `created_at` / `last_used_at` 等**所有时间字段必须是运行时取的真实系统当前时间**（如 Bash `date +%Y-%m-%dT%H:%M:%S`、Python `datetime.now()`），**禁止写死固定值**（如 `2026-06-29T09:30:00`）。理由：LRU 淘汰与排序依赖 `last_used_at` 区分新旧，若时间戳被写死成同一个固定值，所有条目时间相同 → LRU 退化为随机删除、排序失序、续期续错（见历史 bug：某工单 `last_used_at` 被刷新但 `hit_count=0` 的数据失真）。

- `ticket_id` / `project_path`（当前工程根绝对路径）/ `out_dir`（`.icode_output/.icode_output_{N}`）
- `requirement_summary` / `requirement_points` / `keywords` 取自本步骤 metadata
- 入口命令的标记：
  - `/icode log` 产出：`has_00_init` = true（已产出 00_init.md）、`has_plan` = false、`status` = `log_done`
  - 步骤0 init 首轮：`has_00_init` = true、`has_plan` = false、`status` = `init_in_progress`、`requirement_points` 暂空
  - 步骤1 常规新建首跑（跳过 init/log）：`has_00_init` = false、`has_plan` = true、`status` = `plan_done`
- `created_at` = 当前时间
- `last_used_at` = 当前时间（**新增**：检索命中时更新，LRU淘汰依据；首次写入=created_at）
- `hit_count` = 0（**新增**：检索命中时+1，达20永久保留）
- `stale` = false（**新增**：过时校验失败置true，stale工单不再注入不续期；软stale可复活见下）
- `stale_reason` = `null`（**新增**：失败原因 `anchor_gone`/`checkout_mismatch`/`path_gone`/`semantic_deviation`/`timeout` 之一，未标 stale 时为 null）
- `stale_checked_commit` = `null`（**新增**：上次 stale 评估时的工作树 HEAD，可复活判据）
- `created_commit` = 工单创建时工作树 HEAD（`git rev-parse HEAD`，**只读**；非 git 仓库/失败→`null` 走纯锚点兜底），不可变，commit 上下文 stale 判据
- `created_branch` = `git rev-parse --abbrev-ref HEAD`（detached 记 `HEAD`，非 git 仓库→`null`），仅标签
- 写回 index.json，置 metadata `indexed = true`、`ticket_id = {生成的 ticket_id}`
- **写入后执行 LRU 淘汰**（见下方「索引淘汰规则」）

## 索引条目更新（已有 ticket_id 的情况）

按 metadata 的 `ticket_id` 定位 index.json 中本工单条目，更新对应字段：

- 步骤0 每轮对话后：刷新 `requirement_summary` / `requirement_points`（从 `00_init.md`「3.新增需求点」自动提炼）
- 步骤1 写完 `01_plan.md` 后：`requirement_summary`（基于完整计划刷新）、`has_plan` = true、`status` = `plan_done`
- 步骤6 终审后：`status` = `completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新

## 检索命中续期 + 过时校验（检索阶段执行）

> **⚠️ index.json 读取方式（防与 DOC 混淆）**：本段 index.json 指**全局工单索引** `~/.claude/icode_data/index.json`，是完整 JSON 文件，必须用 `json.load` **整体解析 `tickets` 数组全量读**，禁止按行截断（如只读前 50 行--12 条工单约占 350 行，前 50 行仅覆盖 2 条，会漏掉其余工单导致检索失真）。「前 50 行」规则**仅适用于** `project_docs/<id>/*.md` 章节（见下文「段零·工程文档检索」段步骤 2），两者不可混用。

检索阶段（init/log/plan/start 启动时扫 index.json）采用**两段式检索**（详见 SKILL.md「检索注入流程」）——段一 keywords Jaccard 粗筛取 ≤10 候选（零 token，复活预扫后排除剩余 stale/当前 ticket_id），段二只把候选 keywords+requirement_points 喂 LLM 精读打分选 top-N 命中（N 由梯度决定）。对 top-N 命中工单，**先做过时校验，再续期**：

### 项目路径校验（防注入已删除工程）

**触发时机**：检索命中准备注入前，与代码锚点校验同级。

**校验方法**：

```bash
test -d "{project_path}" || {  # 工程根目录已删除/移动
  # 标记该工单 stale=true
  # 跳过该条注入（即使 hit_count 高也不注入）
}
```

**设计意图**：工程被删/移动后，老工单的 ADR/需求已无意义，注入会误导。stale=true 后该工单不再被段一粗筛命中（段一前显式排除）、不再被注入、不再续期。

**与现有 stale 机制的关系**：复用 stale 字段，不引入新状态值；项目路径校验失败的工单走与代码锚点失效相同的 stale 路径。

### 过时校验（防注入过时信息）

索引存的是工单**当时的摘要**，但工程会迭代，老工单的 ADR/需求可能已被后续工单推翻。命中过时工单注入会误导。

> **⚠️ Git 操作安全白名单（强制，违反即不合规）**：本段所有 git 调用必须**只读**，仅允许：`git rev-parse HEAD` / `git rev-parse --abbrev-ref HEAD` / `git rev-parse --git-dir` / `git merge-base --is-ancestor <A> <B>`（仅取退出码 0/1）/ `git status --porcelain` / `git log --oneline -1` / `git cat-file -e <sha>`。**禁止** `checkout`/`switch`/`reset`/`stash`/`clean`/`commit`/`add`/`rm`/`rebase`/`merge`/`cherry-pick`/`push`/`fetch`/`pull`/`branch -D`/`tag -d` 等一切写操作与网络操作。stale 检测**只读工作树现状，绝不改工作树/索引/提交**--"checkout 变化时重评"指检测到**用户外部**改了 HEAD 后只读重评，**绝不由技能主动 checkout**。`-C {project_path}` 前缀用于跨工程工单切换目录执行只读命令，必需且允许；全部本地完成，离线可用。

**校验方法**（对 top-N 命中工单，注入前逐条；`H = git -C {project_path} rev-parse HEAD`（该工单工程的当前 HEAD，每候选取一次；非 git 仓库/失败→`null` 走纯锚点兜底））：
1. **项目路径校验**：`test -d {project_path}` 失败→置 `stale=true`+`stale_reason=path_gone`+`stale_checked_commit=H`，**跳过注入**（即使 hit_count 高也不注入），避免对已删除工程的引用注入
2. **commit 上下文校验**（`created_commit` 非 null 时；为 null 跳过本步进第3步纯锚点兜底）：
   - `H == created_commit`→工作树恰为工单出生提交，完全匹配，**not stale，高置信注入**（快路径，跳过第3步锚点校验）
   - `git merge-base --is-ancestor {created_commit} {H}`：退出 0→正常前向演进，进第3步；退出 1（H 是 `created_commit` 的祖先/分叉）→置 `stale=true`+`stale_reason=checkout_mismatch`+`stale_checked_commit=H`，**软 stale 跳过注入**（checkout 变化时可复活，见「stale 字段·可复活」）；退出 128（`created_commit` 不可达，如 GC/换库）→视同 `created_commit=null` 进第3步纯锚点兜底
3. **代码锚点校验**（廉价，现有）：读该工单 `01_plan.md` 的 ADR 章节提取**代码锚点**（如"calc.c mul_overflows"），Grep 该工单工程（`{project_path}`）确认是否仍存在；不存在（删除/重构/重命名）→置 `stale=true`+`stale_reason=anchor_gone`+`stale_checked_commit=H`，**跳过注入**
4. **语义偏离校验**（新，贵层，仅对已决定注入的 top-N≤3 条）：Read 该工单工程（`{project_path}`）锚点处当前代码，按 **ADR 偏离 checklist** 判 ADR 前提是否仍成立：①ADR 涉及的函数/类签名是否仍匹配 ②返回值/边界值/错误码是否仍如 ADR 所述 ③ADR 推理依赖的调用关系/数据结构是否仍存在。任一不成立→置 `stale=true`+`stale_reason=semantic_deviation`+`stale_checked_commit=H`，**跳过注入**（专抓"锚点符号在但语义已变"，锚点校验抓不到）

通过全部校验→工单有效，正常注入 + 续期（`stale_checked_commit=H` 在评估时已更新）。

### stale 字段

- index.json 每条加 `stale`（默认 false）+ `stale_reason`（默认 null）+ `stale_checked_commit`（默认 null）
- `stale=true` 的工单：**不再注入**（检索时跳过），但仍保留在索引（不删，留追溯）
- stale 工单默认**不被段一粗筛命中**（关键词交集前即排除）、不参与 hit_count 续期（不再被命中）--但有**可复活例外**（见下）
- stale 由五种途径触发，每种填对应 `stale_reason`：①检索命中注入前被动校验失败（`path_gone`/`checkout_mismatch`/`anchor_gone`/`semantic_deviation`）；②每次写索引后主动扫描最旧 K 条锚点失效（`anchor_gone`）；③僵尸未完成态超时降级（`timeout`，见「索引淘汰规则」规则 5）
- **软 stale vs 硬 stale**：`checkout_mismatch`/`anchor_gone`/`path_gone`/`semantic_deviation` 为**软 stale**（依赖当前 checkout，可复活）；`timeout` 为**硬 stale**（活动维度，checkout 变化不复活，下次写索引刷新活动时自然解除）
- **可复活规则**（解决 checkout 假阳性，核心）：检索段一前对每条 stale 工单取 `H = git -C {project_path} rev-parse HEAD`（该工单工程当前 HEAD）；对每条 `stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的工单，**临时置 `stale=false`** 让其重入段一候选集，按「过时校验」重评。重评仍失败→`stale=true` 且更新 `stale_checked_commit=H`（同 HEAD 下次不再重评，省算）；用户 checkout 回正常→`stale_checked_commit != H` 成立→自动复活重评。**临时 checkout 旧提交误判的 stale，回正常 HEAD 后自动复活，不再永久粘住**（每条 stale 工单一次 `git -C` 调用 <10ms，stale 通常少数；git 调用只读，见「过时校验·Git 操作安全白名单」）
- 若该工单产物被刷新（如重跑步骤6终审）：**步骤6 终审刷新索引时已自动重置** `stale=false`+`stale_reason=null`+`stale_checked_commit=null`（旧 stale 判据失效，下次检索按当前 `01_plan` 锚点重评）；其他产物刷新场景可手动重置

### 续期（校验通过才续期）

- `last_used_at` = 当前时间（续期，LRU不淘汰）
- `hit_count` += 1（累计命中次数）
- 写回 index.json
- `stale_checked_commit` 在「过时校验」评估阶段已更新为当前 `H`（**与 hit_count 解耦**--续期去重跳过 hit_count +1 时，stale_checked_commit 仍随评估更新，保证可复活判据准确）

> **原子同步（强制，防数据失真）**：`last_used_at` 与 `hit_count` 必须在**同一次写回**中同步更新，**不得只更新其一**。历史 bug：某次命中只更新了 `last_used_at` 却漏 `hit_count` 自增，导致 `last_used_at` 被刷新但 `hit_count=0`——这条工单在 LRU 排序里因 hit_count 低沉底、却被 last_used_at"续期"误导排序，**直接破坏淘汰准确性**。续期写入时若任一字段更新失败，整次续期回滚不落盘。

## 索引淘汰规则（LRU + 命中续期 + 永久保留）

**目的**：index.json 是检索缓存非档案，随工单增长会膨胀。靠 LRU 淘汰失去复用价值的老工单，保留高价值工单。淘汰只删索引条目，**不删各工程 `.icode_output/` 产物**（产物保留，索引只是指针）。

**触发时机**：每次写索引（首次写入/条目更新/命中续期）后执行：先排序，再淘汰扫描，最后主动 stale 扫描。

**排序规则**（写入时重排 tickets 数组）：按 `hit_count` 降序、同值按 `last_used_at` 降序。高复用价值 + 近期被用的工单排前，段一粗筛扫 `keywords` 时主代理先看到高价值项，判断相关性更快；LRU 淘汰时最老的自然在末尾。

**淘汰规则**：
1. **容量上限 200 条**：tickets 数组超 200 时触发淘汰
2. **永久保留**：`hit_count >= 20` 的工单永久不淘汰（被复用≥20 次的高价值工单）
3. **未完成态保留**：`status` 为 `init_in_progress`/`log_done`/`log_in_progress`/`review_in_progress`/`deepcheck_in_progress`/`code_in_progress` 的工单**默认**不淘汰（流程未结束）——**但有超时降级例外**（见规则 5）
4. **LRU 淘汰**：超上限时，在**可淘汰集**中淘汰 `last_used_at` 最老的（数组末尾），直到条目数 ≤ 200。**可淘汰集** = `hit_count < 20` 且满足下列**任一**：① `stale=false` 且 `status = completed/review_done/deepcheck_done/plan_done/plan_finalized`（正常已完成/推进态）；② `stale=true` 且 `stale_reason = timeout`（规则 5 超时降级的僵尸未完成态，status 仍 in_progress 但已降级可淘汰）。**`stale=true` 且 `stale_reason != timeout` 的软 stale 工单不在可淘汰集**（保留待 checkout 变化后复活重评）
5. **僵尸未完成态超时降级**：未完成态工单（init/log/review/deepcheck/code in_progress）若 `last_used_at` 距当前**超过 30 天**无更新，视为"建了就扔"的僵尸工单——**不删 status，改置 `stale=true`+`stale_reason=timeout`**（复用 stale 机制，不污染 status 语义），降级后该条不再受规则 3 的未完成态保护，**纳入规则 4 可淘汰集②**（status 仍 in_progress，由可淘汰集②的 `stale_reason=timeout` 判定可淘汰）。**触发时机**：每次写索引触发淘汰扫描时，顺带检查所有未完成态工单是否超时。区分"正在用"（30 天内有更新）与"建了就扔"（超时）。**timeout 为硬 stale**：复活条件 `stale_reason != timeout` 排除它，超时降级不复活、直接走淘汰
6. **淘汰不报错**：静默移除，用户无感

**主动 stale 扫描**（防过时信息堆积，规则 5 之外的第二道清理）：

- **现状漏洞**：原设计 stale 校验只在"检索命中准备注入前"才做——没被命中的老条目永远 stale=false，永远不会被这个机制清理，stale 清理形同虚设。
- **新增主动扫描**：每次写索引触发淘汰扫描后，**顺带对 `last_used_at` 最旧的 K 条**（**K=min(30, 当前条目数)**，索引 150 条时覆盖率 20%）做一次代码锚点 Grep 校验（方法同「过时校验」段第3步）。锚点失效→置 `stale=true`+`stale_reason=anchor_gone`+`stale_checked_commit=H`（H=该工单 `git -C {project_path} rev-parse HEAD`（只读）；属软 stale，checkout 变化时可复活）。把"被动校验"变"主动清理"。
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
  "requirement_summary": "{根因一句话摘要，≤100 token}",
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
  "requirement_summary": "{基于粗略需求的一句话摘要，≤100 token；无参数时填空字符串}",
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
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{刷新全局索引时回填}",
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
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
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

## 注入缓存机制（防重复注入，两源共用）

> 本机制解决「同一开发链路内重复注入同一来源的同一信息切片」问题。**历史检索复用（现有）**和**段零工程文档检索（icode doc 新增）**共用此缓存。定义在此处一处，五入口（init/log/plan/start/fast）统一引用。

### 设计动机

五入口（init/log/plan/start/fast）启动时都会触发检索注入，但一次开发链路常跨多命令（如 init→start 复用同目录），同一历史工单/文档章节会被多次命中，导致 **token 浪费**（重复注入同一切片）+ **hit_count 扭曲**（同目录多次续期虚高，扭曲 `hit_count >= 20` 永久保留判定）。

### 缓存文件

`{ICODE_OUT_DIR}/_inject_cache.json`（工单目录内，与 `.ico_metadata.json` 平级，跟随工单生命周期）：

```json
{
  "ticket_id": "demo-5",
  "injections": [
    {
      "source": "history",
      "ref_id": "demo-3",
      "slice": "requirement_points",
      "by_cmd": "init",
      "at": "2026-07-06T15:30:00Z"
    },
    {
      "source": "project_doc",
      "ref_id": "07_messages_and_ipc.md",
      "slice": "section:07_messages_and_ipc.md",
      "by_cmd": "plan",
      "at": "2026-07-06T15:35:00Z"
    }
  ]
}
```

### slice 取值词表（两源统一）

| source | slice | 注入命令 | 含义 |
|--------|-------|---------|------|
| `history` | `requirement_points` | init | 需求要点清单 |
| `history` | `adr_risks` | plan / start / fast | ADR + 风险评估章节 |
| `history` | `root_cause_evidence` | log | 根因结论 + 决定性证据 |
| `project_doc` | `section:<file>` | init/log/plan/start/fast | 工程文档章节（可细化到小节锚点 `section:<file>#<anchor>`） |
| `project_doc` | `section:<file>#stale-summary` | init/log/plan/start/fast | stale 章节降级注入的简要说明（不读正文小节，见「stale 章节降级注入」） |

### 去重规则（核心）

**注入前查缓存**——对每个准备注入的项，按 `(source, ref_id, slice)` 三元组查 `injections` 数组：

- **三元组已存在** → **跳过注入**（不重复进思考输入）+ **跳过续期**（历史源不重复 `+1` hit_count）
- **三元组不存在** → 注入 + 追加一条缓存记录

**不同 slice 允许共存**（不是 bug，是特性）：

- init 注 `(history, demo-3, requirement_points)`，后续 start 注 `(history, demo-3, adr_risks)`——两个不同 slice，各自注入一次，合理
- 这是"一次开发链路里不同步骤注入同一工单的不同信息切片"的正常场景

### 续期去重（hit_count 防重复 +1）

**历史源**命中才续期 `hit_count`（工程文档源无续期概念）。同一工单目录内，对同一历史 `ticket_id` 的续期**只算一次**：

- **判定依据**：缓存 `injections` 中是否已有 `source == "history" AND ref_id == <该 ticket_id>` 的**任一**记录
- **已有** → 已续期过，本次命中不再 `+1`（但若 slice 是新的，仍允许注入该新 slice，只是不续期）
- **没有** → 首次命中，注入 + 续期（`hit_count += 1` + `last_used_at` 原子同步）+ 写缓存

**跨工单目录**命中同一历史工单 → 算新的续期（不同 `_inject_cache.json`，各自首次命中 `+1`）。符合"独立开发才算多次复用"。

### 生命周期与崩溃恢复

- **跟随工单目录 + append-only**：`.icode_output_N/` 删除→缓存消失；每条注入立即写盘，崩溃不丢、重启不重复注入
- **与续跑机制正交**：不读 `*_in_progress`，不影响步骤 2/5 断点续跑
- **向后兼容**：旧工单无缓存→首次检索创建空缓存 `{"ticket_id":"<本工单>","injections":[]}`（ticket_id 读 metadata，暂无填空串）

### 工程污染防护

缓存只在 `.icode_output_N/` 内（不进工程根/git，建议 `.gitignore` 已含 `.icode_output/`），与 `.ico_metadata.json` 平级独立，不污染检索字段。

## project_docs 工程文档库（icode doc 步骤用）

> `/icode doc` 生成的工程级知识库。**零配置、零状态文件、零索引文件**——只有章节 .md 文件，每个自带身份证（前 50 行三合一）。详见 [steps/doc.md](../steps/doc.md) 与 [references/doc_template.md](doc_template.md)。

### 目录布局

```text
~/.claude/icode_data/project_docs/
└── <project_id>/                    # project_id = basename(git rev-parse --show-toplevel)
    ├── 00_overview.md               # 永远首章，元信息块含 generation_commit/submodules
    ├── 10_architecture.md
    ├── 20_<module>_<topic>.md       # 十位桶：20-29 模块章节，留空隙可插入
    ├── 30_<module>_<topic>.md       # 第二个模块桶
    ├── ...
    ├── 90_glossary.md
    └── 99_code_facts_audit.md       # 永远末章
```

### project_id 语义

- **git-root 模式**（cwd 在 git 仓库内）：`project_id` = `basename($(git rev-parse --show-toplevel))`，不区分子目录
- **`repo` 根模式**（cwd 不在 git 仓库但有 `.repo/manifest.xml`，如 Google `repo` 管理的多仓库项目）：`project_id` = `basename(从 cwd 向上找第一个含 .repo/ 的目录)`
- 子目录（如 `myrepo/module_a/`）是同一 project 下的**章节模块**（章节名带模块前缀 `20_module_a_overview.md`），不是独立 project
- **冲突处理**：若 `~/.claude/icode_data/project_docs/<basename>/` 已存在且其 `00_overview.md` 元信息块的 `project_path` 与当前 git_root 不同 → 自动追加短 hash 后缀（如 `myproject__a3f2`），AI 靠元信息块的 git remote 区分同名工程
- **零配置**：不引入任何配置文件，工程名等可配信息全在章节元信息块
- **`resolve_project_id(cwd)` 算法**：
  1. `git rev-parse --show-toplevel` 成功 → git-root 模式，返回 `(basename(git_root), "git-root", git_root)`
  2. 否则从 cwd 向上逐级 `test -d $d/.repo`，首个命中 → repo-root 模式，返回 `(basename(repo_root), "repo-root", repo_root)`
  3. 都失败 → 报错"请在 git 仓库或 repo 管理的项目内运行 /icode doc 或段零检索"

### 章节前 50 行三合一（自带身份证）

每个章节前 50 行承载项目元信息块（工程名/git/提交/子模块/产品线/模块/时间）+ KEYS 块（检索词带 `[小节锚点]`，段零按小节注入不灌全章）+ 简要说明（50~100 字），完整结构见 [references/doc_template.md](doc_template.md)。**元信息块替代 config + state + index 三个文件**——文件系统即数据库（`ls` 枚举、元信息查状态、KEYS 做检索）。

### 段零·工程文档检索（init/log/plan/start/fast 共用）

五入口启动时，与历史检索复用并行做段零检索，**候选合并后统一排序**注入（不分来源，最相关者胜）。除工程自身章节（`project_docs/`）外，自动覆盖工程依赖的模块共享文档（`module_docs/`，按仓库+分支 key 跨工程共享，详见「module_docs 工程模块库」段）。

```text
1. cwd → resolve_project_id(cwd) → (project_id, project_type)  # git-root 或 repo-root
2. ls ~/.claude/icode_data/project_docs/<project_id>/*.md
   ├─ 不存在 → 段零零命中（输出一行 ℹ️ 提示"本工程尚未生成知识库，可运行 /icode doc"，不阻塞，不写缓存）
   └─ 存在 → 逐章读前 50 行 → KEYS 匹配 + 简要说明语义打分 → project 候选集
3. 读 project_docs/<project_id>/_meta.json → module_deps 列表
   对每个 dep：ls ~/.claude/icode_data/module_docs/<dep.key>/*.md
   ├─ 目录不存在或无 .md：
   │   - dep.generated == false（按需未生成，见 doc.md 步骤5「module_docs 生成范围」用户指定模块名场景）-> 不报缺失，提示"可 `/icode doc <name>` 按需生成"，跳过该 dep
   │   - dep.generated 缺省或 true（本应生成却缺失）-> 收集到「缺失模块」列表（末尾汇总警告），跳过该 dep
   └─ 存在 → 读前 50 行 → KEYS 匹配 → module 候选集
      **commit 一致性校验（防跨工程 commit 漂移误导）**：比对工程 `_meta.json.module_deps[].commit`（本工程 pin 的 commit）与 `module_docs/<key>/_meta.json.current_commit`：
      - 一致 → 正常注入（模块文档版本与本工程代码版本匹配）
      - 不一致（同分支不同 commit，跨工程 /icode doc 互相覆盖所致，见「module_docs key 计算」）→ **降级注入**：注入正文但附警告「⚠️ 模块 `<name>` 文档基于 commit `<current_commit>`，本工程 pin `<dep.commit>`，API/行为以本工程代码为准，须 Read 实证」，提示下游不得直接采信模块文档的 file:line / 接口描述
      - key 只含 url+branch 不含 commit，同分支不同 commit 共用同一 key 无法靠目录隔离，段零必须运行时比对 commit 兜底；`/icode doc` 生成时的全量重生成覆盖（见 [doc.md](../steps/doc.md) 步骤5）只把文档更新到"最后一次跑 doc 的工程"的 commit，不能消除跨工程漂移
3.5 **反查父项目**（子仓库内工作时）：如果 cwd 在 git-root 模式 → 计算 `cwd_relative = realpath(cwd) 相对 realpath(.repo 所在目录)`（**注意：是相对 .repo 根，不是相对 git_root**——例如 cwd 在 `myproject/module_a/`、.repo 在 `myproject/.repo/` 时，cwd_relative = `module_a/`）→ 若 `.repo/manifest.xml` 存在且 `cwd_relative` 精确匹配或为某 `<project path>` 的子路径 → 该 manifest project 的"父 repo 根"=`.repo/` 所在目录；把父 project（repo-root）也纳入检索（读 `project_docs/<父 project_id>/_meta.json` + 章节），候选合并排序时一并参与打分（来源标签标「来源：project:父 project_id」）
4. 合并排序：project 候选 + module 候选 + 反查父项目章节 + 历史检索段一/二候选 → 统一按相关度排序 → top-N（强相关≤2 + 弱相关≤1，总量≤3 条）
5. 对每个 top-N 项（注入策略按来源区分）：
   - **project 章节**：stale 判定（查 `_meta.json.stale_files` + 运行时 `git diff` 兜底，见「stale 检测」）-- stale → slice=`section:<file>#stale-summary`，按「stale 章节降级注入」只注简要说明+警告（不读正文小节）；非 stale → slice=`section:<file>#<anchor>`，按 KEYS [小节锚点] 定点读对应小节（不读全章，≤1K token/条）→ 注入思考输入「历史参考」节
   - **module 章节**：按步骤3 commit 校验结果 -- 一致 → slice=`section:<file>#<anchor>` 注正文；不一致 → 降级注正文+警告（步骤3，须 Read 实证）
   - 查 _inject_cache.json → 已注入的同 slice 跳过；否则注入并写缓存
   - 命中附来源标签：「来源：project:myproject」或「来源：module:module_a@main@a3f2b1c」
```

**防重复注入**：与历史源共用 `_inject_cache.json`（见上节），跨命令不重复注入同一章节。

**stale 检测**（注入前被动校验，控 token；与历史工单 stale「跳过注入」对齐防误导目标）：读 `00_overview.md` 元信息块的 `generation_commit` + submodule commits，与 `git rev-parse HEAD` + `git submodule status` 对比；HEAD 变更且 `git diff <commit>..HEAD --name-only` 命中该章 KEYS「文件位置」**或命中章节正文涉及的目录前缀**（应对 KEYS 文件位置不完整 / 新增文件 / 重构改路径导致漏判的假阴性）→ 判定该章 stale。**两源合一**：先查工程 `_meta.json.stale_files`（主动扫描持久化结果，见下文「project_docs 主动 stale 扫描」）快速识别 stale 章节（降级注入摘要不注正文，见下文「stale 章节降级注入」），再运行时 `git diff` 兜底（`_meta.json` 可能比最新 HEAD 旧）。判定宁可放宽（假阳性仅触发降级，假阴性才会误导）。

**stale 章节降级注入（不注正文，防误导）**：stale 章节**绝不注入正文小节**（避免过时 file:line / IPC 契约 / 调用链误导新工作流），改为**只注入「简要说明」（50~100 字概览，不含 file:line，误导风险低）+ 警告行**「⚠️ 章节 `<文件>` 基于旧 commit `<generation_commit>`，当前 HEAD `<HEAD>`，已过时仅注入摘要，建议重跑 `/icode doc` 更新」。与历史工单 stale「跳过注入」同等防误导强度--过时的正文小节不进新工作流思考输入；降级保留「简要说明」仅给新工作流方向性参考，不构成事实依据（配合下文「不盲信约束」）。 注入缓存 slice 记为 `section:<file>#stale-summary`（与正文小节 `section:<file>#<anchor>` 区分，避免去重混淆；章节重跑变新鲜后注入正文小节不被误跳过）。

**不盲信约束（段零注入的工程/模块文档仅作参考）**：段零注入的 project_docs / module_docs 章节是 `/icode doc` 生成时的**快照**，可能因工程迭代而过时（即使未标 stale 也只是「未检测到过时」，非「已验证最新」）。下游 init/plan/start/fast/log 步骤**不得将注入的文档描述当作事实直接采信**：凡涉及代码行为 / 位置 / 接口契约 / 调用链 / 错误码的断言，**必须用 Read/Grep 实证当前代码**后再纳入决策（与 [anti_laziness.md](anti_laziness.md)「段零文档不盲信」条 + [01_plan.md](../steps/01_plan.md) 计划断言实证一致）。文档只作「设计意图与模块关系」的启发，不作「代码事实」的依据。

**附加输出**（段零末尾汇总）：
- ⚠️ **缺失模块警告**（若步骤 3 收集到「缺失模块」列表）：输出「工程引用了以下 module_docs 但不存在：\`{dep1.name}\`(\`{dep1.key}\`), \`{dep2.name}\`(\`{dep2.key}\`)... — 请检查是否已 /icode doc 生成，或工程 _meta.json.module_deps 是否写错 key」
- ⚠️ **未生成模块警告**（若工程 `_meta.json.unresolved_modules` 非空）：输出「工程有 N 个未生成模块（拉取失败）：\`{name1}\`(\`{reason1}\`), \`{name2}\`(\`{reason2}\`)... — 子仓库代码本地化后重跑 /icode doc 时自动恢复」
- ℹ️ **按需未生成模块提示**（若步骤 3 收集到 `generated == false` 的模块）：输出「工程有 N 个模块未生成 module_docs（按需未生成，上次 /icode doc 指定了其他模块聚焦）：\`{name1}\`, \`{name2}\`... - 可 \`/icode doc <name>\` 按需生成」

**工程隔离**：段零严格按当前 cwd 的 project_id 检索**工程自身章节**（不跨 project 注入 `project_docs/`），但**自动跨仓库跨分支覆盖依赖的 `module_docs/`**（多个 project 引用同一上游仓库同分支只一份，自动复用）。姊妹工程引用走章节正文内的文字描述，不自动跨库。


### project_docs 主动 stale 扫描（`/icode doc` 末尾执行，防过时章节堆积）

对比 index.json 的主动 stale 扫描，project_docs 章节此前只有"段零命中前被动检测"一条路径--长期不跑 `/icode doc` 的工程，过时章节无机制标记，首次段零命中才被动检测（且只降级注入，不清理）。本机制补第二道清理（与 index.json「主动 stale 扫描」对齐，见上文「索引淘汰规则·主动 stale 扫描」）：

- **触发时机**：`/icode doc` 执行末尾（见 [doc.md](../steps/doc.md) 步骤8）
- **扫描范围**：全库章节（project_docs 章节量可控，每章 Grep 锚点 <1K token，全量可控；不像 index.json 只扫最旧 K 条）
- **校验方法**：逐章读其 KEYS「文件位置」列出的文件路径，用 Grep 确认锚点代码仍存在（方法同 index.json「过时校验」）
- **结果写 `_meta.json.stale_files`**：失效的章节文件名写入工程 `_meta.json.stale_files` 数组；章节重生成（锚点恢复）后从此数组移除
- **段零消费**：段零检索时先读 `stale_files` 快速跳过（无需每章 git diff，控 token），再运行时 `git diff` 兜底（见上文「stale 检测」两源合一）

## module_docs 工程模块库（按仓库+分支共享）

> **核心思想**：工程依赖的"独立模块"（git submodule / `repo` 管理 / CMake FetchContent / monorepo 子目录 / vendor / 用户配置）按"上游仓库 + 分支"为粒度共享文档。**多个工程引用同一上游仓库同一分支只生成一份**，段零自动覆盖，开发时快速定位关联模块的文档。

> **「独立模块」判定澄清**："独立模块"指**该模块本身是独立 git 仓库**（无论它是否同时是工程核心模块）。`repo` 管理的子项目（如某任务控制中枢模块，是工程核心但同时是独立子仓库）判定为独立模块 -> 生成 module_docs；其工程视角（在工程 IPC 拓扑中的角色）写在 project_docs。**"是否独立 git 仓库"是唯一判定标准，与"是否工程核心"无关**--核心模块同时是独立仓库时，两者都生成，互补不重复（职责划分见 [doc_template.md](doc_template.md)「九 · 职责划分」）。

### 目录布局（module_docs 层）

```text
~/.claude/icode_data/
├── project_docs/                          # 工程级（v1 已有）
│   └── <project_id>/
│       ├── _meta.json                    # 工程元信息（含 module_deps 列表）
│       ├── 00_overview.md
│       └── .../*.md
└── module_docs/                          # 模块级（按仓库+分支 key）
    └── <url_basename_sanitized>_<sha256(repo_url+":"+branch)[:12]>/   # 如 module_a@main → module_a_9f3a2b1c4d8e
        ├── _meta.json                    # 模块元信息（url/branch/commit/used_by）
        ├── 00_overview.md
        └── .../*.md
```

### `module_docs` key 计算

```text
key = <url_basename_sanitized> + "_" + sha256(repo_url + ":" + branch)[:12]
```

- **`url_basename_sanitized`**：url 去掉末尾 `.git` 取最后一段（`basename`），非 `[a-zA-Z0-9_-]` 字符替换为 `_`（防边界情况，如 url basename 含 `.` `@` `:` 等）
- **人眼可读**：目录名以模块名开头（如 `module_a_9f3a2b1c4d8e`），`ls module_docs/` 一眼看出是哪个模块的文档，不用反查 hash
- **保留 hash 去重**：同 basename 不同 fork（不同 url，如协议/用户名不同）→ basename 相同但 hash 不同 → 不同 key（如 `module_a_aaaa` vs `module_a_bbbb`）
- **例**：`git@example.com:user/myproject/module_a.git` @ `main` → basename `module_a` → key `module_a_9f3a2b1c4d8e`
- 不同仓库（不同 URL）→ basename 或 hash 至少一个不同 → 不同 key
- 同仓库不同分支（main vs dev）→ hash 包含 branch → 不同 key
- 同仓库同分支不同 commit → 同一 key（key 不含 commit）。**段零检索时**比对工程 pin 的 commit 与 `module_docs/<key>/_meta.json.current_commit`，不一致→降级注入+警告（见「段零·工程文档检索」步骤 3 commit 一致性校验）；`/icode doc` 生成时 commit 不一致→全量重生成覆盖（见 [doc.md](../steps/doc.md) 步骤5）。两者不矛盾：生成时覆盖更新文档版本，检索时校验是否匹配当前工程 pin 的 commit

### `_meta.json` 模板（工程与模块各一份）

**工程 _meta.json**（`project_docs/<project_id>/_meta.json`）：

```json
{
  "project_id": "myproject",
  "project_type": "repo-root",
  "git_root": "/home/user/myproject",
  "module_deps": [
    {
      "type": "repo",
      "name": "module_a",
      "url": "git@example.com:user/myproject/module_a.git",
      "branch": "main",
      "key": "<url_basename_sanitized>_<sha256[:12]>",
      "commit": "a3f2b1c",
      "path": "module_a/",
      "generated": true
    }
  ],
  "unresolved_modules": [
    {
      "name": "lib_b",
      "type": "cmake",
      "reason": "CMake FetchContent build 目录未下载，无法本地读取",
      "attempt_at": "2026-07-06T15:30:00Z"
    }
  ],
  "stale_files": []
}
```

**模块 _meta.json**（`module_docs/<key>/_meta.json`）：

```json
{
  "repo_url": "git@example.com:user/myproject/module_a.git",
  "branch": "main",
  "current_commit": "a3f2b1c",
  "used_by": ["myproject", "another_project"],
  "last_generated_at": "2026-07-06T15:30:00Z",
  "last_generated_commit": "a3f2b1c",
  "generation_trigger": "auto"
}
```

### 工程元信息块"依赖子模块"字段规范

`00_overview.md` 元信息块加：

```markdown
> **项目元信息**
> - 工程名：myproject
> - 项目类型：repo-root（.repo/manifest.xml 管理）
> - Git 地址：git@example.com:user/myproject.git
> - 分支/提交：main @ a3f2b1c
> - 依赖子模块（按仓库+分支）：
>   - module_a → module:module_a@main@a3f2b1c（module_docs/<key>/）
>   - module_b → module:module_b@dev@b8c3d4e（module_docs/<key>/）
> - 章节归属模块：myproject
> - 章节生成时间：2026-07-06T15:30:00Z
```

### 6 级模块检测（按优先级，`/icode doc` 执行）

| # | 方式 | 识别 | 提取 URL + branch | commit 获取 |
| --- | --- | --- | --- | --- |
| 1 | git submodule | `.gitmodules` | url + `git config -f .gitmodules submodule.<name>.branch` | `git submodule status` |
| 2 | `repo` 管理 | `.repo/manifest.xml` | `<remote fetch>` + `<project name>` 拼 URL，`<project revision>` = branch | `cd <submodule_path> && git rev-parse HEAD` |
| 3 | CMake FetchContent | `CMakeLists.txt` 扫 `FetchContent_Declare` | `GIT_REPOSITORY` + `GIT_TAG` | 通常不可用（build 目录未下载），fallback 警告 |
| 4 | monorepo 启发式 | 无 .gitmodules + 多子目录各自有 README + 独立构建文件（CMakeLists/package.json/Cargo.toml/Makefile） | 子目录路径，branch 标 "unknown" | 不可用 |
| 5 | vendor 扫描 | `vendor/<lib>/` 子目录 | 子目录路径，type="vendor-no-git" | 不可用 |
| 6 | 用户配置 | `.icode_doc_modules`（工程根） | 显式列路径/URL | 显式 |

**去重**（两步，避免单一目录被多种方式识别导致重复生成）：
1. **先按"归一化绝对路径"合并**——归一化绝对路径 = `realpath <path>` 解析（处理符号链接 + 路径规范化）后**去尾部 `/`**；同一归一化路径被多种方式识别（如 vendor + monorepo 都识别某路径）→ 合并到 type 列表，type 字段记录多标签如 `"vendor,monorepo"`
2. **再按 `key = <url_basename_sanitized>_<sha256(url+":"+branch)[:12]>` 去重**（同一上游仓库同分支只一份，key 格式含模块名前缀便于人眼辨认，详见本段「module_docs key 计算」）

### 子仓库代码获取

- **git submodule**：`git submodule foreach 'git archive HEAD | tar -x -C $tmp/<name>'`（或逐个 `cd <submodule> && git archive HEAD | tar -x -C $tmp`）
- **`repo` 子仓库**：`cd <submodule_path> && git archive HEAD | tar -x -C $tmp`
- **monorepo/vendor**：直接读子目录文件（不需 archive）
- **CMake FetchContent**：通常 build 目录未下载，fallback 警告「依赖模块代码未本地化，跳过生成」

### 段零跨仓库自动覆盖（关键收益）

- 同一上游仓库（如 `module_a@main`）被多个工程都引用 → 段零都查同一份 `module_docs/<key>/`，**只生成一次、复用**
- 详见「段零·工程文档检索」段步骤 3（module_deps 检索）+ 步骤 3.5（反查父项目）

**生成时不省 token**（用户闲时跑，模块文档可详细完整生成），**检索时省 token**（段零只读前 50 行粗筛 + 命中按小节锚点读，不灌全章）。
