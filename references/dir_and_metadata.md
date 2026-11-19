# 目录管理 + ticket_id + 索引写入 + metadata 模板（共享规则）

> 本文件汇集 icode "创建工单目录 / 生成 ticket_id / 写全局索引 / metadata 模板" 的共享规则。入口命令（init/log）和步骤1在创建工单时引用本文件，不重复定义。进入本文件前必须先按 [project_intake.md](project_intake.md) 解析实际工程根；下文所有 `cwd`/`WORKSPACE_ROOT`/`GIT_ROOT` 示例均假定已在该根执行，禁止在只包裹一个子仓的容器目录落工单。

## 目录管理

### 创建新目录（用于常规 init / log，以及 start / plan 在不满足复用条件时）

> `/icode init --guide` 是派生产物例外：它在进入本节前即分流，用 `resolve-ticket --latest --require-status init_in_progress --require-artifact 00_init.md --require-init-ready` 选择最新结构完整的正常工单，**绝不执行创建或 REUSE=2 询问逻辑**。详见 [guide_contract.md](guide_contract.md)。

```bash
# ——工作区根锚定（建前必做，L1 致命，阻塞流程）——
# 输出目录必须落在「当前会话工作区根」内：先解析工作区根（git 仓库 = git rev-parse --show-toplevel；非 git 仓库 = pwd 兜底），
# 之后所有路径一律基于该绝对根。禁止凭历史路径假设/硬编码另一副本绝对路径写产物
# （历史事故：工单被写入另一仓库副本、用户按报告路径找不到；.icode_output 被 gitignore 时 git status 也不提示）。
# ⚠️ 概念区分（勿混）：WORKSPACE_ROOT = 「当前 checkout 根」，即产物物理落点——worktree 内就是 worktree 根，**不做 F1 归一**；
#   而 project_id 的 F1 归一（worktree 归主仓根，见「project_id 与 branch 语义」）只用于索引/limit/doc 键，**不用于产物落点**。
#   原地（非 worktree）时两概念同根；worktree 时产物必须落在 worktree 内（隔离 checkout），否则破坏 worktree 隔离（工单产物进错仓库）。
#   归档/备份不在此步：归档（worktree 回流后 archive_path）与备份（/icode bak）作用于已入索引的工单，debug 工单不入索引、无归档/备份流程。
WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
mkdir -p "$WORKSPACE_ROOT/.icode_output"   # 统一父目录，所有产物收纳于此（基于工作区根绝对路径）
# ⚠️ 目录号必须由下方递增逻辑计算，禁止手写/硬编码 N（如 .icode_output_10）——手写绕过递增 = 拆除"max+1 防误复用"安全网（历史事故：误复用已有工单目录，覆盖其 metadata 与 00_init）
LAST=$(ls -d "$WORKSPACE_ROOT"/.icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR="$WORKSPACE_ROOT/.icode_output/.icode_output_${NEXT}"
# ——硬熔断① 建前检查（防误复用，L1 致命，阻塞流程）——
# 目标目录已存在 = 递增逻辑被绕过 / 目录号手写 / 并发竞态 → 立即中止，禁止覆盖
test -d "$ICODE_OUT_DIR" && { echo "❌ 误复用风险：目标目录已存在 $ICODE_OUT_DIR（递增逻辑被绕过或目录号手写/硬编码），禁止在此新建工单"; exit 1; }
mkdir -p "$ICODE_OUT_DIR"
# ——硬熔断② 建后验证（防误复用，L1 致命，阻塞流程）——
# 新建目录必须为空；非空 = 误复用（已含旧产物 / 旧工单所有权标志 .ico_metadata.json）→ 立即中止
[ -z "$(ls -A "$ICODE_OUT_DIR")" ] || { echo "❌ 目录非空=误复用，禁止在此新建工单"; exit 1; }
# ——硬熔断③ 建后校验（工作区根锚定，L1 致命，阻塞流程）——
# 产物目录真实路径必须位于工作区根内；不在 = cwd 漂移 / 路径假设错误（凭历史路径写到了别的仓库副本）→ 立即中止，不静默写入
case "$(realpath "$ICODE_OUT_DIR")" in
  "$WORKSPACE_ROOT"/*) : ;;
  *) echo "❌ 输出目录 $(realpath "$ICODE_OUT_DIR") 不在当前工作区根 $WORKSPACE_ROOT 内（cwd 漂移或路径假设错误），禁止在此新建工单"; exit 1 ;;
esac
# 建后确认只能用磁盘状态（ls -A 为空 / 目录 mtime），禁止用 echo "created" 充当确认（echo 与磁盘状态无关，是伪确认）
# ——创建完成提示（强制输出）——
echo "📁 ICODE_OUT_DIR = $(realpath "$ICODE_OUT_DIR")"   # 绝对路径，供用户一眼核对是否落在当前工作区
grep -qE '^[^#]*\.icode_output' "$WORKSPACE_ROOT/.gitignore" 2>/dev/null && echo "⚠️ 产物目录被 gitignore，git status 不会提示写入位置，请以上方 📁 绝对路径为准（L3 提示，非阻断）"
```

**「创建新目录」debug 变体（`--debug` 模式，init/log 用）**：

`/icode init --debug` / `/icode log --debug` 的工单目录建在 `.icode_output/.debug/` 子目录下，**N 与正常工单互不干扰**（各自从 1 递增）；工作区根锚定 + 硬熔断①②③ 与正常工单完全一致：

```bash
# 工作区根锚定（建前必做，同「创建新目录」段，L1 致命）：debug 产物必须落在当前工作区根内，
# 禁止凭历史路径假设/硬编码另一副本绝对路径写产物（历史事故：debug 工单被写入另一仓库副本，用户按路径找不到）。
# debug 忽略 --worktree 原地创建：cwd 在 worktree 内时 WORKSPACE_ROOT = worktree 根，产物落该 checkout 的 .icode_output/.debug/
WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
mkdir -p "$WORKSPACE_ROOT/.icode_output/.debug"
LAST=$(ls -d "$WORKSPACE_ROOT"/.icode_output/.debug/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR="$WORKSPACE_ROOT/.icode_output/.debug/.icode_output_${NEXT}"
# 硬熔断① 建前检查：目标 debug 目录已存在 = 递增被绕过/目录号手写 → 中止
test -d "$ICODE_OUT_DIR" && { echo "❌ 误复用风险：目标 debug 目录已存在 $ICODE_OUT_DIR（递增逻辑被绕过或目录号手写），禁止在此新建工单"; exit 1; }
mkdir -p "$ICODE_OUT_DIR"
# 硬熔断② 建后验证：新建目录必须为空
[ -z "$(ls -A "$ICODE_OUT_DIR")" ] || { echo "❌ 目录非空=误复用，禁止在此新建 debug 工单"; exit 1; }
# 硬熔断③ 建后校验（工作区根锚定，L1 致命）：真实路径必须位于工作区根内，不在 = cwd 漂移/路径假设错误 → 中止
case "$(realpath "$ICODE_OUT_DIR")" in
  "$WORKSPACE_ROOT"/*) : ;;
  *) echo "❌ 输出目录 $(realpath "$ICODE_OUT_DIR") 不在当前工作区根 $WORKSPACE_ROOT 内（cwd 漂移或路径假设错误），禁止在此新建 debug 工单"; exit 1 ;;
esac
# 创建完成提示（强制输出）：绝对路径供核对
echo "📁 ICODE_OUT_DIR = $(realpath "$ICODE_OUT_DIR")"
grep -qE '^[^#]*\.icode_output' "$WORKSPACE_ROOT/.gitignore" 2>/dev/null && echo "⚠️ 产物目录被 gitignore，git status 不会提示写入位置，请以上方 📁 绝对路径为准（L3 提示，非阻断）"
```

- 正常工单的「创建新目录」`ls -d "$WORKSPACE_ROOT"/.icode_output/.icode_output_*` 只匹配**顶层**目录，天然排除 `.debug/` 子目录下的 debug 工单（物理隔离，互不污染）
- debug 工单 metadata 写 `debug: true` + 独立状态名（`debug_in_progress` / `debug_done`），详见 [debug_mode.md](debug_mode.md)
- **空目录即出生（vNext 强制）**：硬熔断②确认目录为空后，下一项写操作必须是 `icode_control.py create`；正常入口用 `--birth init|plan|log`，debug 入口用 `--birth debug-init|debug-log`。`create` 自身会复验目录形状、normal/debug 域和空目录所有权；trace、checkpoint、附件或步骤报告均不得先于出生事件落盘。

**Crosscheck 非工单变体**：`/icode crosscheck` 使用 `.icode_output/.crosscheck/.icode_output_N/`，编号只在 `.crosscheck` 内递增；它不创建 metadata、不进入正常/debug 创建与复用逻辑，也不进入全局索引。目录身份、多轮和原工单零回写规则见 [crosscheck_mode.md](crosscheck_mode.md)，实现只能调用 `tools/icode_crosscheck.py`。

### 复用 / 创建新目录决策（用于 start / plan / fast）

```bash
# 工作区根锚定（建前必做，同「创建新目录」段，L1 致命）：一切路径基于当前工作区根绝对路径，
# 禁止凭历史路径假设/硬编码另一副本绝对路径写产物（历史事故：工单被写入另一仓库副本，用户按路径找不到）
WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
mkdir -p "$WORKSPACE_ROOT/.icode_output"
LAST=$(ls -d "$WORKSPACE_ROOT"/.icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
REUSE=0
if [ -n "$LAST" ]; then
  CAND="$WORKSPACE_ROOT/.icode_output/.icode_output_${LAST}"
  # 判定最新目录是否为"入口态"：有 .ico_metadata.json + 00_init.md，且无 01_plan.md
  if [ -f "$CAND/.ico_metadata.json" ] && [ -f "$CAND/00_init.md" ] && [ ! -f "$CAND/01_plan.md" ]; then
    # {ICODE_SKILL_ROOT} 先解析为本次实际加载的技能根，不能使用业务工程根。
    STATUS=$(python3 -c "import sys; sys.path.insert(0,'{ICODE_SKILL_ROOT}/tools'); import icode_control as ctl; print(ctl.read_metadata_readonly(sys.argv[1]).get('status',''))" "$CAND") || exit 1
    # 入口态：init_in_progress 或 log_done
    case "$STATUS" in
      init_in_progress|log_done) REUSE=2 ;;  # 2 = 有歧义，需问用户
    esac
  fi
fi
# REUSE=2：有歧义，问用户"复用 / 新建"；REUSE=0：非入口态，带参新建 / 无参报错
if [ "$REUSE" = "0" ]; then
  NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
  ICODE_OUT_DIR="$WORKSPACE_ROOT/.icode_output/.icode_output_${NEXT}"
  # 硬熔断① 建前检查（同「创建新目录」段，防误复用）：目标目录已存在 = 递增被绕过 → 中止
  test -d "$ICODE_OUT_DIR" && { echo "❌ 误复用风险：目标目录已存在 $ICODE_OUT_DIR（递增逻辑被绕过或目录号手写/硬编码），禁止在此新建工单"; exit 1; }
  mkdir -p "$ICODE_OUT_DIR"
  # 硬熔断② 建后验证：新建目录必须为空
  [ -z "$(ls -A "$ICODE_OUT_DIR")" ] || { echo "❌ 目录非空=误复用，禁止在此新建工单"; exit 1; }
  # 硬熔断③ 建后校验（工作区根锚定，L1 致命）：真实路径必须位于工作区根内，不在 = cwd 漂移/路径假设错误 → 中止
  case "$(realpath "$ICODE_OUT_DIR")" in
    "$WORKSPACE_ROOT"/*) : ;;
    *) echo "❌ 输出目录 $(realpath "$ICODE_OUT_DIR") 不在当前工作区根 $WORKSPACE_ROOT 内（cwd 漂移或路径假设错误），禁止在此新建工单"; exit 1 ;;
  esac
  # 创建完成提示（强制输出）：绝对路径供核对
  echo "📁 ICODE_OUT_DIR = $(realpath "$ICODE_OUT_DIR")"
  grep -qE '^[^#]*\.icode_output' "$WORKSPACE_ROOT/.gitignore" 2>/dev/null && echo "⚠️ 产物目录被 gitignore，git status 不会提示写入位置，请以上方 📁 绝对路径为准（L3 提示，非阻断）"
fi
```

### 复用决策两档（仅 REUSE=2 / REUSE=0，无 REUSE=1）

- `REUSE=2`（入口态有歧义）→ **必须问用户"复用 / 新建"**（无论命令是否带参——带参可能是补充旧需求也可能是新需求，区分不了，故一律问），按答复定；复用时将 `00_init.md` 作为步骤1主要需求输入（命令行参数作补充）
- `REUSE=0`（非入口态）→ 带参新建、无参报错
- **不得擅自复用**（会丢失新需求）也**不得擅自新建**（会丢失 init/log 上下文）

### 检测最新目录 / 当前控制根（用于 review / merge / code / deepcheck / audit / patch / status）

```bash
# 工作区根锚定（同「创建新目录」段）：ICODE_OUT_DIR 一律基于当前工作区根绝对路径，防 cwd 漂移误定位
WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
LAST=$(ls -d "$WORKSPACE_ROOT"/.icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -n "$LAST" ]; then
  ICODE_OUT_DIR="$WORKSPACE_ROOT/.icode_output/.icode_output_${LAST}"
elif [ -f "$WORKSPACE_ROOT/.icode_output/.active_ticket.json" ]; then
  # reopen 后产物的永久控制根在归档目录，新 checkout 只放身份指针。
  ICODE_OUT_DIR=$(python3 - "$WORKSPACE_ROOT/.icode_output/.active_ticket.json" <<'PY'
import json, pathlib, sys
sys.path.insert(0, '{ICODE_SKILL_ROOT}/tools')
import icode_control as ctl
p=json.load(open(sys.argv[1], encoding="utf-8"))
root=pathlib.Path(p["control_root"]).resolve()
m=ctl.read_metadata_readonly(root)
assert m.get("ticket_id")==p.get("ticket_id"), "active ticket pointer 与 metadata 身份不一致"
assert pathlib.Path(m.get("artifact_root") or "").resolve()==root, "artifact_root 不一致"
print(root)
PY
  ) || exit 1
  echo "🗂️ reopened 工单控制根 = $ICODE_OUT_DIR"
else
  echo "错误：没有找到 .icode_output/.icode_output_N 目录，请先运行 /icode start <需求> 或 /icode init"
  exit 1
fi
```

`reopen` 不把旧工单目录复制回新 checkout；它写入小指针 `.icode_output/.active_ticket.json`，以 `ticket_id + control_root` 找回归档产物。代码操作根始终是 `metadata.active_checkout.path`，产物/事件根始终是 `ICODE_OUT_DIR=metadata.artifact_root`，两者不得混用。

## ticket_id 生成规则

- `ticket_id` = `{工程名}-{N}`（工程名取 `project_path` 的 basename；N 为当前 `.icode_output_N` 的 N）
- **工程名冲突处理（生成时必须检查）**：生成 ticket_id 后，**必须 Python 解析 index.json 检查是否已有相同 `{工程名}-{N}` 但 `project_path` 不同的条目**；有则追加 `project_path` 短 hash 后缀（`sha256(project_path)[:4]`，如 `myproject-1-a3f2`）保唯一。若生成时漏检，`index-write` 会以 `ticket_id_ownership` fail-closed，不会自动改 ID 或覆盖旧条目。
- **入口命令（init/log）共享 N 序列**：init/log 各自创建新目录时，N 是当前目录下**所有** `.icode_output_*` 中的最大 N + 1，不区分 init/log（demo-5 是 init，demo-9 是 log，N 单调递增）
- 生成后立即传给 `create --ticket-id`，**不允许空 ID 出生后再回填**。
- **唯一性保证**：创建前检查；`index-write` 再做 ticket_id/out_dir 所有权硬熔断和写后唯一性验证。

## 全局索引写入（首次写入）

> **vNext 工单索引写入唯一入口**：`python3 tools/icode_control.py index-write --ticket-dir {ICODE_OUT_DIR}`——锁覆盖完整读-合并-写窗口，并做混合代际整文件校验、fsync+原子 rename、写后唯一性验证。vNext 工单**禁止手工直改 index.json**；legacy **工单**需先显式迁移。legacy **索引条目**不要求批量重写：无 `control_schema_version` 的旧条目按最小可读契约保留，新写/接管条目标记 `control_schema_version=3` 并严格校验；工具不可用时 fail-closed，只允许只读诊断。以下字段段落是索引语义契约，不是绕过 writer 的手工写入授权。

控制面在锁内读取 `~/.claude/icode_data/index.json`（不存在则创建 `{"version":1,"updated_at":"当前时间","tickets":[]}`），合并一条新记录；调用方不得自行追加：

> **"当前时间"取值约定（强制，防 LRU 失效）**：`updated_at` / `created_at` / `last_used_at` 等**所有时间字段必须是运行时取的真实系统当前时间**（如 Bash `date +%Y-%m-%dT%H:%M:%S`、Python `datetime.now()`），**禁止写死固定值**（如 `2026-06-29T09:30:00`）。理由：LRU 淘汰与排序依赖 `last_used_at` 区分新旧，若时间戳被写死成同一个固定值，所有条目时间相同 → LRU 退化为随机删除、排序失序、续期续错（见历史 bug：某工单 `last_used_at` 被刷新但 `hit_count=0` 的数据失真）。

- `control_schema_version` = `3` / `ticket_id` / `project_path`（当前工程根绝对路径）/ `out_dir`（`.icode_output/.icode_output_{N}`）
- `requirement_summary` / `requirement_points` / `keywords` / `workload_estimate` / `workload_reason` 取自本步骤 metadata
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
- `verdict` = `"unknown"`（**新增**：方向结论，详见 SKILL.md「verdict 字段族」；首次写入固定 `unknown`，由后续标注/终审/批量识别改写）
- `verdict_reason` / `correct_direction` / `verdict_source` / `verdict_at` / `superseded_by` = `null`（**新增**：verdict 关联字段，标注时按需填）
- `verdict_premise_deps` = `[]` / `verdict_review_needed` = `false`（**新增**：硬复活字段，默认空/false；`disproved`/`superseded` 标注时按需填 `verdict_premise_deps` 支持依赖变化检测）
- **并发与唯一性**：`index-write/index-update` 在跨进程文件锁内完成整个读-合并-校验-写后复验窗口。同 ticket_id 指向不同目录或同目录指向不同 ticket_id 均 fail-closed，不自动改身份；必须先按短 hash 规则生成正确 ID 再重试。成功后工具原子置 metadata `indexed=true` 并留 `index_updated` 事件。
- **写入后执行 LRU 淘汰**（见下方「索引淘汰规则」）
- **写入后执行 LRU 淘汰**（见下方「索引淘汰规则」）

## 索引条目更新（已有 ticket_id 的情况）

按 metadata 的 `ticket_id` 定位 index 中本工单条目。metadata 镜像字段先合并回 metadata，再统一调 `python3 tools/icode_control.py index-write --ticket-dir {ICODE_OUT_DIR}`；检索命中/stale 等索引独有字段调 `index-update`。legacy 需先显式迁移，不允许人工读-改-写绕过锁。

- 步骤0 每轮对话后：刷新 `requirement_summary` / `requirement_points`（从 `00_init.md`「3.新增需求点」自动提炼）
- 步骤1 写完 `01_plan.md` 后：`requirement_summary`（基于完整计划刷新）、`has_plan` = true、`status` = `plan_done`
- 步骤6 终审后：`status` = `completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新；**终审时确认 verdict**（默认保持 `unknown` 不阻塞流程；用户标 `verified`/`disproved`/`superseded` 时回填 `verdict`+`verdict_reason`+`correct_direction`+`verdict_source`+`verdict_at`，详见 [steps/06_audit.md](../steps/06_audit.md)）
- `/icode bak` 备份后：对快照内每个 `.icode_output_N/.ico_metadata.json` 的 `ticket_id`，命中全局索引则置 `backup_path = ~/.claude/icode_data/project_backup/<project_id>/<快照>/.icode_output_N`（**指向最新快照**，覆盖旧值；未命中/无 metadata/`ticket_id` 为空则跳过）。**写前重读合并 + 原子写**（并发安全契约，同本段开头）。这是「工程被删后索引仍能从备份找到工单」的前提，详见 [steps/bak.md](../steps/bak.md)

## metadata 三基线字段（现场运行 / 当前分析 / 修复验证，P0）

`/icode log` 的「现场运行版本基线门」（见 [steps/log.md](../steps/log.md) 阶段1）把"产生日志时的代码版本"与"当前工作区代码"分离，落盘为三个**可选** metadata 字段（**缺失视为 `[]`，向后兼容旧 metadata**，不影响 status/readme/audit；详细字段结构见 SKILL.md「可选字段」表 + [steps/log.md](../steps/log.md) 步骤9）：

- `runtime_code_baselines`（现场运行版本，`/icode log` 写入）：日志/版本文件检出的**现场运行版本**数组，每条 `{module, repo_path, evidence_source, raw_version, commit, dirty, resolved, confidence, relation_to_analysis_head}`。
  - `evidence_source`（必填）：版本证据来源（如"节点 A 启动日志 boot.log:12"）——每条候选必须能回指证据；
  - `commit`：解析出的完整 commit；`dirty=true` 表示 Hash 只能定位基础提交、不能精确复现运行二进制，**置信度不得标高**；
  - `resolved`：是否在本地仓库唯一解析为 commit；`confidence` ∈ `high`/`medium`/`low`；
  - `relation_to_analysis_head` ∈ `same_as_head`（现场=当前 HEAD）/ `ancestor_of_head`（现场是当前 HEAD 的祖先）/ `ahead_or_forked`（现场落后/分叉，演进对照须走 `git diff/log` 判定）/ `unresolved`（Hash 不可达/歧义）。
- `analysis_code_baselines`（当前分析版本，`/icode log` 写入）：分析所用**当前 HEAD** 数组，每条 `{module, repo_path, commit, branch}`。
- `verification_code_baselines`（修复验证版本，验证完成后回填）：每条 `{module, commit, verified_at, scenarios}`——验证设备实际部署 Hash + 场景清单，供 `/icode readme` 简报「验证版本」槽位消费。

**只读约束**：版本关联 git 调用必须在「Git 操作安全白名单」内（见「过时校验」段）；历史源码用 `git -C <repo> show <hash>:<path>` 只读读取，**禁止** checkout/switch/reset/stash/clean，分析阶段不自动 fetch——Hash 本地不可达时如实记录降级（`relation_to_analysis_head=unresolved`），由用户决定是否补齐仓库对象或发布清单，**不得把当前 HEAD 自动填成现场版本**。

## 检索命中续期 + 过时校验（检索阶段执行）

> **⚠️ index.json 读取方式（防与 DOC 混淆）**：本段 index.json 指**全局工单索引** `~/.claude/icode_data/index.json`，是完整 JSON 文件，必须用 `json.load` **整体解析 `tickets` 数组全量读**，禁止按行截断（如只读前 50 行--12 条工单约占 350 行，前 50 行仅覆盖 2 条，会漏掉其余工单导致检索失真）。「前 50 行」规则**仅适用于** `project_docs/<id>/*.md` 章节（见下文「段零·工程文档检索」段步骤 2），两者不可混用。

检索阶段（init/log/plan/start 启动时扫 index.json）采用**两段式检索**（详见 SKILL.md「检索注入流程」）——段一 keywords Jaccard 粗筛取 ≤10 候选（零 token，复活预扫后排除剩余 stale/当前 ticket_id；**归档/备份工单天然不受影响**：`archive_path` 非 null 且 `test -d` 有效的归档工单（archived 活跃态）或 `backup_path` 非 null 且 `test -d` 有效的备份工单（backup 活跃态）均为活跃态，**非 stale**，不被段一排除，照常进候选走归档/备份读档），段二只把候选 keywords+requirement_points 喂 LLM 精读打分选 top-N 命中（N 由梯度决定）。对 top-N 命中工单，**先做过时校验，再续期**：

### 项目路径校验（防注入已删除工程）

**触发时机**：检索命中准备注入前，与代码锚点校验同级。

**校验方法**：

```bash
test -d "{project_path}" || {  # 工程根目录已删除/移动
  # 标记该工单 stale=true
  # 跳过该条注入（即使 hit_count 高也不注入）
}
```

**设计意图**：工程被删/移动后，老工单的 ADR/需求已无意义，注入会误导。stale=true 后该工单不再被段一粗筛命中（段一前显式排除）、不再被注入、不再续期。**区别（归档工单 = archived 活跃历史工单，不进入 stale 机制）**：worktree 回流已归档（`archive_path` 有效）的工单，`project_path` 失效属预期（工单已交付），ADR/根因已归档可读档复用——该工单**不标 stale**，为 archived 活跃态，段一照常进候选、过时校验走「归档读档」、命中正常续期+按 verdict 分流，**待遇与主仓工单一致**，仅产物来源不同（archive_path vs project_path）。「达到像主仓使用一样」= 检索命中、ADR/根因注入、命中续期、verdict 分流、LRU 保留全部一致。

**与现有 stale 机制的关系**：复用 stale 字段，不引入新状态值；项目路径校验失败的工单走与代码锚点失效相同的 stale 路径。

**备份工单（backup 活跃态，`/icode bak` 产物）**：`backup_path` 非 null 且 `test -d {backup_path}` 有效的工单，`project_path` 失效（工程被删）时**重置 `stale=false`+`stale_reason=null`+`stale_checked_commit=null`**（backup 活跃态——即使此前因锚点失效标过 stale，工程已删后备份是唯一可读快照，旧 stale 判据作废），产物源=backup_path（读完整工单产物：`01_plan.md` ADR/风险、`log_analysis.md` 根因/结论、`06_audit.md` 终审等），跳过代码锚点/commit/语义校验（历史快照，无当前代码可校验，注入走历史参考语义——作启发、非当前代码事实，须 Grep/Read 实证，见「不盲信约束」），命中**正常续期 + 按 verdict 分流**（`disproved`/`superseded` 降级注避坑提示 + ⚠️『基于备份历史代码，未经当前代码实证，须用户判断前提是否仍适用』），待遇与主仓工单一致。**工程优先**：`test -d {project_path}` 有效（工程恢复/重克隆）永远优先走工程，备份仅兜底。backup_path 失效（备份也被删）→ 才置 `stale=true`+`stale_reason=path_gone`+跳过注入（真病态）。**与归档工单并行**：`archive_path`（worktree 归档）与 `backup_path`（手动备份）任一有效即活跃态，互不排斥。备份命令见 [steps/bak.md](../steps/bak.md)。

### 过时校验（防注入过时信息）

> 本节已独立到 [references/project_docs_retrieval.md](project_docs_retrieval.md)；判定历史工单/结论是否过时时按该文件执行。

### stale 字段

- index.json 每条加 `stale`（默认 false）+ `stale_reason`（默认 null）+ `stale_checked_commit`（默认 null）
- `stale=true` 的工单：**不再注入**（检索时跳过），但仍保留在索引（不删，留追溯）
- stale 工单默认**不被段一粗筛命中**（关键词交集前即排除）、不参与 hit_count 续期（不再被命中）--但有**可复活例外**（见下）
- stale 由三类途径触发，每种填对应 `stale_reason`：①检索命中注入前被动校验失败（`path_gone`/`checkout_mismatch`/`anchor_gone`/`semantic_deviation`）；②工作流写索引前由调用方主动扫描最旧 K 条，锚点失效（`anchor_gone`）；③僵尸未完成态超时降级（`timeout`，见「索引淘汰规则」规则 5）
- **软 stale vs 硬 stale**：`checkout_mismatch`/`anchor_gone`/`path_gone`/`semantic_deviation` 为**软 stale**（依赖当前 checkout，可复活）；`timeout` 为**硬 stale**（活动维度，checkout 变化不复活，下次写索引刷新活动时自然解除；**注意此处"硬"方向与 verdict 的"硬复活"相反**——"硬 stale"的"硬"=timeout 强制不可复活，"硬复活"的"硬"=确定性可复活，见 verdict_premise_deps 字段）
- **可复活规则**（解决 checkout 假阳性，核心）：检索段一前对每条 stale 工单取 `H = git -C {project_path} rev-parse HEAD`（该工单工程当前 HEAD）；对每条 `stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的工单，**临时置 `stale=false`** 让其重入段一候选集，按「过时校验」重评。重评仍失败→`stale=true` 且更新 `stale_checked_commit=H`（同 HEAD 下次不再重评，省算）；用户 checkout 回正常→`stale_checked_commit != H` 成立→自动复活重评。**临时 checkout 旧提交误判的 stale，回正常 HEAD 后自动复活，不再永久粘住**（每条 stale 工单一次 `git -C` 调用 <10ms，stale 通常少数；git 调用只读，见「过时校验·Git 操作安全白名单」）
- 若该工单产物被刷新（如重跑步骤6终审）：**步骤6 终审刷新索引时已自动重置** `stale=false`+`stale_reason=null`+`stale_checked_commit=null`（旧 stale 判据失效，下次检索按当前 `01_plan` 锚点重评）；其他产物刷新场景可手动重置

### 续期（校验通过才续期）

> **verdict 分流（v2 新增，防错误工单被机制奖励）**：`verdict="verified"`/`"unknown"` 正常续期（`last_used_at`+`hit_count` 原子同步）；`verdict="disproved"`/`"superseded"` 命中**只更新 `verdict_at` 记录最近提醒，不续期 `last_used_at`、不续期 `hit_count`**--被证伪/取代的工单不应因反复命中被当高价值永久保留优先注入。续期去重缓存判定仍按 `(source=history, ref_id=ticket)` 任一记录判（见「续期去重」），disproved/superseded 工单命中仍写缓存记录（防同目录重复注入），只是不 +1 hit_count。**向后兼容**：旧工单无 `verdict` 字段视为 `"unknown"`，续期行为与现状完全等价

- `last_used_at` = 当前时间（续期，LRU不淘汰）--仅 `verified`/`unknown`；`disproved`/`superseded` 跳过
- `hit_count` += 1（累计命中次数）--仅 `verified`/`unknown`；`disproved`/`superseded` 跳过
- `verdict_at` = 当前时间（仅 `disproved`/`superseded` 命中时更新，记录提醒次数，不触发续期）
- 续期经 `index-update --ticket-id <id> --increment-hit`；disproved/superseded 仅更新 `verdict_at` 时用 `--set-json`，不加 hit
- `stale_checked_commit` 在「过时校验」评估阶段已更新为当前 `H`（**与 hit_count 解耦**--续期去重跳过 hit_count +1 时，stale_checked_commit 仍随评估更新，保证可复活判据准确）

> **归档/备份工单正常续期（archived/backup 活跃态）**：`archive_path` 有效（走「归档读档」注入）或 `backup_path` 有效（走「备份读档」注入）的工单，命中**正常续期**（`last_used_at`+`hit_count` 原子同步），与主仓工单一致——归档/备份是已完成交付的真实工单，被复用越多越有保留价值，应参与 LRU 正常保留；仅 `verdict` 为 `disproved`/`superseded` 时按 verdict 语义跳过续期（见上）。续期去重缓存照常写记录（防同目录重复注入）

> **原子同步（强制，防数据失真）**：`last_used_at` 与 `hit_count` 必须在**同一次写回**中同步更新，**不得只更新其一**。历史 bug：某次命中只更新了 `last_used_at` 却漏 `hit_count` 自增，导致 `last_used_at` 被刷新但 `hit_count=0`——这条工单在 LRU 排序里因 hit_count 低沉底、却被 last_used_at"续期"误导排序，**直接破坏淘汰准确性**。续期写入时若任一字段更新失败，整次续期回滚不落盘。

## 索引淘汰规则（LRU + 命中续期 + 永久保留）

> **按需加载**：`/icode bak`、索引 LRU 淘汰、归档/备份永久保留判定时，先 Read [ticket_storage_details.md](ticket_storage_details.md)「索引淘汰规则」段。

## .ico_metadata.json 模板

以下模板描述**完整逻辑 metadata**。新建工单实际文件由控制面写为 storage v2 envelope，字段读取统一走 [SKILL.md「票据逻辑读取约定」](../SKILL.md)；物理协议、同票 ledger 引用及只读 / writer 边界见 [控制面契约 §1.1](control_plane.md#11-物理-storage-v2-与只读逻辑对象)。不要把模板复制到已有票据覆盖控制文件。

入口命令（init/log）和步骤1常规新建目录时创建。完整字段定义见 SKILL.md「元信息文件」段，此处仅列入口创建时的最小模板：

> **`schema_version` 字段（vNext 控制面，schema v3）**：所有新建工单一律先确定非空唯一 `ticket_id`，再用 `tools/icode_control.py create` 原子产生 `schema_version:3` metadata + `ticket_created` 事件；三个 gate schema 版本均为 `1`。旧工单无 schema_version = legacy（只读适配，不静默伪装 vNext）。

> **`template_version` 字段**（schema 版本，自 [v1.1 升级](../steps/01_plan.md) 起所有新建工单默认带）：
>
> - 默认值：`"v1.1"`（新增工单一律带；旧工单缺字段视为 `"v0"`，会在第一次进入步骤 1 / 步骤 4 / 步骤 5 时自动迁移补全）
> - 当前生效版本：v1.1（含 §1.5 工程结构快照 / 三链预扫段 / blast-radius 三链自检段）
> - 读取约定：步骤 1 / 步骤 4 / 步骤 5 启动时读 `.ico_metadata.json.template_version`，与本步骤期望的 `CODE_TEMPLATE_VERSION`/`DEEPCHECK_TEMPLATE_VERSION`/`PLAN_TEMPLATE_VERSION`（均 `"v1.1"`）比对——匹配则跳过迁移、不匹配则自动追加新段并升级

> **`migration_log` 字段**（schema 迁移历史，可选，默认 `[]`）：
>
> - 数组结构：每条 `{from, to, at, files}`。`from`/`to` 为版本字符串；`at` 为运行时 `date +%Y-%m-%dT%H:%M:%S` 取系统时间（**禁写死**，LRU 与迁移溯源依赖真实时间）；`files` 为本步迁移涉及的产物文件名数组
> - 写入约定：步骤 1 / 步骤 4 / 步骤 5 自动迁移完成后**追加**一条（不覆盖、不去重），便于 audit 阶段追溯版本演进
> - 失败兜底：迁移任何步骤失败，写 `{from, to, at, skip_reason}` 条目，不阻塞主流程
> - 字段缺失兼容：旧 metadata 缺 `migration_log` 视为 `[]`；新增工单一律初始化为 `[]`

> **三步迁移的相互独立性**：步骤 1 / 步骤 4 / 步骤 5 的迁移契约**互不依赖**——分别读 metadata、各自判定版本、各自追加产物段；同一工单走 `/icode start` 全流程时三步依次触发，migration_log 数组会按顺序追加 3 条（不全靠一记，单独 step 启动也 OK）。

> **verdict 字段族**（方向结论，可选，详见 SKILL.md「verdict 字段族」）：所有入口模板均可选；**创建时可不写**（缺失视为 `"unknown"`，向后兼容旧 metadata）；需标注时回填 `verdict`+`verdict_reason`+`correct_direction`+`verdict_source`+`verdict_at`（`superseded` 额外填 `superseded_by`；`disproved`/`superseded` 可选填 `verdict_premise_deps` 支持硬复活），途径见 `/icode status --verdict`（[steps/status.md](../steps/status.md)）/ 步骤6 终审（[steps/06_audit.md](../steps/06_audit.md)）/ 批量识别扫描。**索引首次写入时 verdict 固定 `"unknown"`、关联字段 null、premise_deps `[]`/review_needed `false`**（见「全局索引写入」段）

> **`scope_escalations` 字段族**（范围升级记录，可选，默认 `[]`，详见 SKILL.md「可选字段」段）：review/deepcheck/audit 阶段发现**计划之外**的新问题/新架构信号（新增持久化协议、全局门控、生命周期语义、跨职责边界组件、新故障模型）拟纳入实施范围时的分类记录。**审查采纳 ≠ 实施授权**：`A_now` 须直接复发证据链（无证据回指默认 `B_confirm` 需用户确认）/ `B_confirm` 未获确认不得进入编码 / `C_follow_up` 进范围外 / `refuted` 丢弃。所有入口模板可选；创建时不写（缺失视为 `[]`，向后兼容）。写入点：review 2.5.6 over-design 审查 / deepcheck over-design 复检 / audit 终审；细则见 反偷懒第 33 条
>
> **`delivery_verdict` 字段族**（验证完成度，可选，默认 `null`，详见 SKILL.md「可选字段」段）：与 `verdict` 方向结论**正交**——`verdict` 答"方案方向对不对"（verified/disproved/superseded/unknown），`delivery_verdict` 答"验证动作完成没"（verified/verification_pending/blocked/not_applicable）。由步骤6 终审按 `test_outcome` / 实机验证 / O-6 用户自担验证豁免情况回填；**`status=completed` 不自动等价于 `delivery_verdict=verified`**。字段缺失视为 `null`（向后兼容，读作"未回填"）。展示：status 模式一「验证结论」行 + readme 交付措辞
>
> **`scope_contract` 字段族**（范围契约，可选，默认 `null`，详见 SKILL.md「可选字段」段）：plan 完成时冻结的语义基线（`{summary, at, source_step}`，summary 为"根因方向 + A/B/C 分档 + 验收边界"的一句话指纹）。供 review/merge/code/deepcheck/audit 核对用户输入或审查是否改变既有契约。字段缺失视为 `null`（向后兼容，读作"未冻结"）。写入点：plan §4.5 落盘 fix_tiers 处一并写。消费点：review 前置校验 / merge 定稿检查 / code 实施核对 / deepcheck 范围核对 / audit 终审范围核对
>
> **`requirement_deltas` 字段族**（用户语义变更记录，可选，默认 `[]`，详见 SKILL.md「可选字段」段）：自动流程期间用户输入改变计划语义（状态身份/生命周期、允许/拒绝条件、持久化一致性或回滚承诺、验收条件/调用方语义/真实环境验证场景）时的分类记录数组（每条 `{at, user_input_summary, changed_aspect, classification, impact, user_confirm}`）。`classification` 枚举：`clarification_only`（仅澄清不改变实现）/ `a_now_with_evidence`（改变 A 档但已有直接证据）/ `needs_user_confirm`（需用户确认）/ `needs_replan`（需回到 plan/review 重新定稿）。**delta 未分流前不得继续扩大代码设计或验收矩阵**（冻结点）。字段缺失视为 `[]`（向后兼容）。写入点：review 前置校验 / merge / code 前置检测用户输入；消费点：merge 定稿检查（未分流禁止定稿）+ code 实施核对
>
> **`workflow_gate_schema_version` 字段族**（workflow gate 版本，可选，默认缺省 = legacy-untracked）：`1` 表示本工单从计划阶段起启用 workflow gate 硬门禁（`semantic_decisions`/`impact_contract`/`acceptance_contract`/`risk_profile`）。字段缺失视为 `legacy-untracked`（阶段一提示模式：缺字段补默认值，只读审计不阻断；`--strict` 强制模式判失败）。写入点：plan 完成时与 `semantic_decisions` 一并写；机器真源 `mcp/workflow-gate/gates.json` + 校验器 `tools/lint_workflow_contract.py`
>
> **`semantic_decisions` 字段族**（语义决策合同，可选，默认 `[]`，详见 SKILL.md「可选字段」段）：任一分析/计划/评审出现**同一输入有两种及以上会改变外部行为的处理方式**、或结论含"待确认/未定义/需要选择/策略不唯一"、或需决定允许/拒绝/合并/替换/保留/迁移、或需决定存活身份/冲突优先级/兼容阈值/失败语义、或当前证据只能说明缺机制不能唯一推导期望行为时，必须先把决策写入本数组。每条 `{dimension, alternatives, selected, evidence, user_confirmed, status}`：
  - `dimension`：决策维度（如"关系冲突处理"）
  - `alternatives`：候选方案数组（会改变外部行为的选项）
  - `selected`：用户已明确选择的值（未选择为 `null`）
  - `evidence`：`user-confirmed`（用户确认）或其他证据回指
  - `user_confirmed`：bool，是否用户显式确认
  - `status`：`resolved`（已解决）/ `open` / `pending` / `rejected`
  - **门禁（机器判定）**：存在 `status != resolved` 且非 `diagnosis_only` → `plan`/`code`/`patch` 阻断（`python3 tools/lint_workflow_contract.py` 校验，真源 [mcp/workflow-gate/gates.json](../mcp/workflow-gate/gates.json)）。**禁止以"最保守/最安全/通常如此"代替用户选择**。诊断命令可结束但必须显式标 `diagnosis_only=true`（"仅诊断"），不得进入实现。写入点：plan §4.5 落盘 fix_tiers 处；消费点：plan/code/patch 前置校验 + merge 定稿检查
>
> **`diagnosis_only` 字段族**（仅诊断标记，可选，默认 `false`）：bool。语义决策门禁的第二个终态——**本轮只交付诊断结论，不允许进入实现阶段**。置 `true` 时 `semantic_decisions` 未解决允许诊断结束（lint 全量扫描 pass + warning "仅诊断"），但 `--step plan/code/patch` 仍阻断（不得进入实现）。字段缺失视为 `false`
>
> **`impact_contract` 字段族**（身份变化影响合同，可选，默认 `null`，详见 SKILL.md「可选字段」段）：设计/代码涉及**实体身份变化**（合并/删除/去重/重命名/重新分配、实体替代、依赖归属变化、权威状态与运行时投影不一致、旧身份提交后不应继续被查询/枚举/执行）时必须填完整 9 维影响清单。结构 `{identity_change, authoritative_writer, persistent_references, derived_metadata, runtime_indexes, queries_and_selectors, async_links, recovery_paths, external_projections, rollback_and_failure, completeness}`——除 `identity_change`（bool）与 `completeness`（`complete`/`incomplete`）外，9 维每项 `{status: "affected"|"not-affected"|"unassessed", evidence}`（必答且必须带证据）。**门禁（机器判定）**：`identity_change=true` 且 `completeness != complete` → `code`/`deploy`/`audit-verified` 阻断；`completeness=complete` 时 9 维每项必答。字段缺失视为 `null`（未声明身份变化，不阻断，向后兼容）。写入点：plan §4.5；消费点：code/deepcheck/audit 前置核对
>
> **`acceptance_contract` 字段族**（生命周期验收合同，可选，默认 `null`，详见 SKILL.md「可选字段」段）：任何**改变权威状态或实体身份**的修改必须覆盖四阶段 × 四类消费者的验收矩阵。结构 `{requires_lifecycle, authoritative_state_change, scope, invariants, matrix}`——`matrix` 每项 `{scenario, phase, consumer, expected, evidence, status}`。`phase` ∈ `immediate`（权威提交立即完成后）/ `converged`（异步投影与运行时状态收敛后）/ `restart`（进程重启并恢复后）/ `replay`（相同操作重复执行或重放后）；`consumer` ∈ `direct_query`（直接查询）/ `aggregate_selector`（聚合或批量选择）/ `persistent_reference`（依赖记录的归属解析）/ `external_projection`（外部投影与持久化恢复）。**门禁（机器判定）**：涉及生命周期/身份变化时矩阵必须覆盖全部 `phases × consumers` 必填单元（空 cell = 验证不完整），**只验证直接查询不能 `delivery_verdict=verified`**。字段缺失视为 `null`（不强制，向后兼容）。写入点：plan §4.5；消费点：deepcheck 一致性核对 + audit 终审验收
>
### risk_profile 字段族

版本化快速模式风险档案，可选 null；字段 `requested_mode` 记录用户请求，`effective_mode` 记录实际模式。共享解析器为 `tools/fast_policy.py:effective_mode(metadata)`，full 优先且本运行不自动降级；未知显示 unknown，准入由 `assess_fast_risk(metadata,catalog)` 明确 blocked。风险词表、检查映射、理由码与准入步骤只从 [workflow catalog](../mcp/workflow-gate/gates.json) 读取。

无 `policy_version` 或版本 1 保留原九 flag 命中 full / 显式 override 语义，不迁移或重新解释历史。新 fast 出生用版本 2，plan 先调查，再写以下结构（示意中的 flags 必须由 catalog 全量生成，不可省略为默认 false）：

完整风险事实表用于 fast 资格判定。full 可保留已知布尔风险子集（含空表），声明的真实风险仍需对应专项合同；未决语义、重大增量、未知标志或非法类型仍阻断。

```json
{
  "policy_version": 2,
  "requested_mode": "fast",
  "effective_mode": "fast",
  "risk_flags": {"由 catalog 枚举的每项风险事实": false},
  "triggers": [],
  "override": false,
  "classification": {
    "scope": "bounded",
    "scope_digest": "现有 scope_contract 的 canonical JSON SHA256，64位小写hex",
    "evidence_refs": [{"scope": "ticket", "path": "01_plan.md", "sha256": "实际文件SHA256"}]
  }
}
```

`scope_contract` 沿用现有 summary/at/source_step 合同；摘要算法为 UTF-8 的 `json.dumps(scope_contract, sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)` 后 SHA256。`classification.scope` 为 bounded/unbounded；引用 scope 为 ticket/workspace，path 必须相对对应根且不能逃逸，hash 为 64 位小写 hex。建议引用已冻结 ticket 计划，以免普通源码实施使调查证据无端失效；reopen 后 ticket 根与执行 workspace 根分别解析，不假设同目录。

纯函数只能核验类型/词表/范围摘要和引用形状，不能证明实读；控制面准入必须按当前 ticket/execution_workspace 实际读取引用并核对 hash。自由文字“范围明确/放心”不构成证据。出生时可缺分类供 plan 调查；进入 review/merge/code/patch 前必须有效。未决语义 blocked，未解决重大增量 replan，硬 full 风险/既有 full 不降级；override 不豁免语义、身份、真实环境与阶段硬门。普通跨文件/缓存/真实环境事实可 bounded fast，但 required_checks 继续按原证据合同完成。
>
> **`workload_estimate` 字段族**（工作量评估，v2 新增）：由步骤 0 init 收尾时自动评估，辅助用户决定走 `/icode start` 还是 `/icode fast`。详见 SKILL.md「workload_estimate 字段族」与 [steps/00_init.md](../steps/00_init.md)「步骤 9 工作量评估」段：
> - `workload_estimate`（可选，枚举，默认 `"medium"`）：工作量等级。`"small"` 建议 `/icode fast`，`"medium"` 建议 `/icode start`，`"large"` **必须** `/icode start`
> - `workload_reason`（可选，≤80 token）：评估理由
> - **字段缺失兼容**：旧 metadata 无 `workload_estimate` 视为 `"medium"`（中性默认），不阻塞后续步骤
> - **4 维度 max 算法**：需求点数 / 涉及文件数 / 跨模块数 / 大改词命中，任一维度落入即评该级，取最严
>
> **大改词典**（大改词命中维度扫的关键词）：`重构` / `大改` / `跨模块` / `架构` / `迁移` / `拆分` / `整合` / `refactor` / `migration` / `overhaul`
>
> **入口建议映射**：`small` → 建议 `/icode fast`，`medium` → 建议 `/icode start`，`large` → **必须** `/icode start`
>
> **阈值表**（任一维度落入即评该级）：
>
> | 维度 | small | medium | large |
> |------|-------|--------|-------|
> | 需求点数（`requirement_points` 长度） | ≤3 | 4-6 | ≥7 |
> | 涉及文件数（`code_files` 长度） | ≤5 | 6-15 | ≥16 |
> | 跨模块数（`code_files` 顶层目录数） | 1 | 2-3 | ≥4 |
> | 大改词命中（`keywords` + `00_init.md` 第 3 节） | 0 | 1-2 | ≥3 |
> - **每轮重评**：步骤 0 多轮对话期间每轮重评，**仅刷 `workload_estimate`+`workload_reason`，不刷 `requirement_points` 等检索字段**（防索引膨胀）
> **`task_timeout_seconds` 字段**（spawn 等待超时阈值，可选，默认 `120`）：
>
> - 数值类型，整数秒，作用于步骤 2.5.5 / 步骤 log 阶段3 / 步骤 5 Free A6 三处 spawn 3 质疑者子代理（详见 [adversarial.md](adversarial.md)「显式等待 + 超时机制」段）
> - 默认值：缺失视为 `120`（120 秒）；`0` 或负数视为非法 → 静默回落 `120`（防误配 0 导致瞬时超时误降级）
> - 写入约定：可在入口/步骤1 metadata 更新时按工程场景配置（如重型分析工单可调至 180-300；轻量可缩至 60）；建议最小不低于 30s（过短会过早触发重试链路）
> - 字段缺失兼容：旧 metadata 无 `task_timeout_seconds` 视为 `120`，不阻塞后续步骤
> **`no_spawn_env` flag 字段**（环境结构性无 spawn 工具标记，可选，默认 `false`）：
>
> - 布尔类型，标记当前 icode 环境是否**结构性**不支持 Agent 工具 spawn（区别于 transient 失败/超时/截断）
> - 默认值：缺失视为 `false`（环境有 spawn 工具但本轮未成功——属 transient 失败，走超时/重试链路）
> - 判定方法：调 `ToolSearch(query="select:Agent")` 取 Agent 工具 schema，命中则 `false`，未命中则 `true`（详见 [adversarial.md](adversarial.md)「环境无 spawn 工具场景」flag 判定方法）
> - 作用：决定是否允许"主代理代行三视角判断"——**仅** `no_spawn_env = true` 时允许；`false` 时**硬禁止**主代理代行（即使部分子代理未回结果，详见 [adversarial.md](adversarial.md)「环境无 spawn 工具场景」第 4 步 flag 门控）
> - 字段缺失兼容：旧 metadata 无 `no_spawn_env` 视为 `false`，不阻塞现有 spawn 协议

### `/icode log` 产出后

```json
{
  "requirement": "{根因转成的修复需求描述}",
  "created_at": "当前时间",
  "status": "log_in_progress",
  "completed_steps": [],
  "code_files": [],
  "limit_refs": [],
  "requirement_summary": "{根因一句话摘要，≤100 token}",
  "requirement_points": ["修复要点1", "修复要点2"],
  "keywords": "{≤8个技术关键词}",
  "indexed": false,
  "ticket_id": "{在 create 前生成的非空唯一 ID}",
  "template_version": "v1.1",
  "migration_log": [],
  "schema_version": 3,
  "workflow_gate_schema_version": 1,
  "thinking_gate_schema_version": 1,
  "mcp_gate_schema_version": 2
}
```

> `limit_refs`：log 报告 `§2.3 limit 红线对照`（必填小节）/ §6、plan §3/§4/§6 引用的红线编号数组，每条 `{redline_no, source, title, applied_in}`；报告出现「红线 N」即须记录，经 log 步骤 9.5 / plan「limit_refs 机器自检」校验（**读留痕 `limit_checkpoint.md` 存在性 + 引用完整性**，见 [steps/log.md](../steps/log.md) 与 [steps/01_plan.md](../steps/01_plan.md)）。⚠️ `limit_refs` 是**事后回补**，只证明"后来引用了哪些红线"；"**先读索引→精读命中**确实读过"的可审计留痕靠 `{ICODE_OUT_DIR}/limit_checkpoint.md` 的**工单级追加式阶段块**（锚点即阶段块标题：log 前置落「阶段块：log前置检查点」/ plan 前置硬基线落「阶段块：plan前置硬基线」，统一覆盖 init/start/fast，缺失按未读处理）。**字段缺失视为 `[]`（向后兼容旧 metadata）**

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
  "workload_estimate": "small|medium|large",
  "workload_reason": "{≤80 token 一句话评估理由}",
  "indexed": false,
  "ticket_id": "{在 create 前生成的非空唯一 ID}",
  "template_version": "v1.1",
  "migration_log": [],
  "schema_version": 3,
  "workflow_gate_schema_version": 1,
  "thinking_gate_schema_version": 1,
  "mcp_gate_schema_version": 2
}
```

### 步骤1 常规新建目录

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "init_in_progress",
  "completed_steps": [],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{在 create 前生成的非空唯一 ID}",
  "mode": "full",
  "max_rounds": 3,
  "template_version": "v1.1",
  "migration_log": [],
  "schema_version": 3,
  "workflow_gate_schema_version": 1,
  "thinking_gate_schema_version": 1,
  "mcp_gate_schema_version": 2
}
```

> 常规新建先 `create --birth plan`（出生态 `init_in_progress`），复用步骤0目录则沿用已有 metadata；两者都在计划产物/合同完成后经 `transition --to plan_done`，由工具追加 `"1"`。

### `/icode fast` 新建目录

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "init_in_progress",
  "completed_steps": [],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{在 create 前生成的非空唯一 ID}",
  "mode": "fast",
  "risk_profile": {"policy_version": 2, "requested_mode": "fast", "effective_mode": "fast", "override": false},
  "max_rounds": 1,
  "template_version": "v1.1",
  "migration_log": [],
  "schema_version": 3,
  "workflow_gate_schema_version": 1,
  "thinking_gate_schema_version": 1,
  "mcp_gate_schema_version": 2
}
```

**`mode` 字段**（可选，默认 `"full"`）：记录入口请求；行为使用 `tools/fast_policy.py` 的 `effective_mode(metadata)`。有效 full 的 review 为默认多轮+对抗、deepcheck 为三阶段；有效 fast 为单轮无对抗与 Reverse。缺失保持旧 full 默认，非法值显示 unknown 并阻断准入，不静默当 fast/full。

**`max_rounds` 字段**：full 默认 3；有效 fast 自动串联固定 1。显式 `/icode review N` 按原参数优先规则升级审查，并先记录有效 full；该运行后续 deepcheck 同样 full。既有 full 不因再次请求 fast 自动降级。

**步骤2/5 读 mode 字段的契约**：这里的“读 mode”统一指解析有效模式，不能仅凭请求 `metadata.mode` 执行 fast 分支。review/deepcheck、coverage、thinking/MCP 与 UI 使用同一解析结果；未知分类先完成调查与准入，不通过不得编码。详情见 [fast 策略](../steps/fast.md)。


## 重复模式状态（patterns.json，跨工单聚合）

> 治「命中多相似工单 → 武断判重复重构 + 反复重构循环」：检索命中簇出现重复信号时，记录"疑似重复模式 + 曾重构事实 + 上次实事求是评估结论"。状态是**历史事实参考，非跳过闸门**——每次命中仍按当下代码实证实事求是评估，不因"标记过已重构"机械跳过（消费逻辑见 [thinking_detail.md](thinking_detail.md)「重复模式检测」段）。

### 文件

`~/.claude/icode_data/patterns.json`（不存在则创建 `{"version":"1","updated_at":"当前时间","patterns":[]}`）。**跨工单全局共享**，不随工单目录走。由检索注入消费端（thinking_detail「重复模式检测」段）读写。

### 条目结构（每个 pattern）

| 字段 | 说明 |
|------|------|
| `pattern_key` | 唯一键：`{project_id}::{module}::{symptom_cluster}`（`project_id` 取 `resolve_project_id(cwd)`，与工单索引 `project_id` 字段同源；demo 等继承主仓 git 的工程会解析为主仓名，历史工单缺该字段时用 `project_path` basename 兜底；module 取工程/模块名，symptom_cluster 为症状关键词簇） |
| `ticket_ids` | 纳入该模式的工单 ticket_id 数组（追加式，去重） |
| `hit_count` | 累计命中次数（命中时 +1） |
| `refactor_ticket_id` | **曾重构过的工单**（历史事实，缺省 null） |
| `last_assessment` | 上次实事求是评估：`{at, result, evidence, ref_ticket_id}`；`result` ∈ `resolved`（已解决无复发）/ `recurred`（复发需再重构）/ `new_pattern`（新根因重估） |
| `workaround` | **上次规避/修复方案**（可选，缺省省略）：**任一三态结论都提炼记录**（保留最近一次）——resolved 记已落地的实现/规避方案（从当前代码提炼）、recurred 记本轮修复方案、new_pattern 记新方案；命中时直接给出，让重复模式从「告警」升级为「可执行知识」 |

### 读写契约（与 index.json 并发安全契约一致）

- **写前重读合并（并发安全）**：从首次 Read 到写回之间可能已有其他会话写入，**写回前必须重新 Read 最新 patterns.json**，把本会话改动合并进最新 `patterns` 数组，再原子写回——绝不在旧快照上直接覆盖
- 每次评估后更新：`hit_count +1`、`ticket_ids` 追加本工单、`last_assessment` 更新；本次判定需重构且获用户确认后回填 `refactor_ticket_id`；**任一结论都提炼 `workaround` 记入**（覆盖旧值，保留最近一次）——resolved 记已落地解法、recurred 记修复方案、new_pattern 记新方案；无方案可提炼（纯新增、无规避/修复概念）则省略字段，杜绝「resolved 最高频却永远暂无历史方案」的空转
- 命中时若 `workaround` 存在，提示直接带「上次方案：{workaround}」（从告警升级为可执行知识；旧 pattern 无此字段 → 显示「暂无历史方案」即可，向后兼容）
- **时间字段用运行时真实系统当前时间**（禁止写死，同「当前时间」取值约定）

## 注入缓存机制（防重复注入，两源共用）

> **按需加载**：写检索注入结果、排查重复注入时，先 Read [ticket_storage_details.md](ticket_storage_details.md)「注入缓存机制」段。

## project_docs 工程文档库（icode doc 步骤用）

> **按需加载**：`/icode doc` 维护工程文档库时，先 Read [ticket_storage_details.md](ticket_storage_details.md)「project_docs 工程文档库」段。

## module_docs 工程模块库（模块族与工作区快照）

> **按需加载**：工程接入 / 模块族快照相关步骤，先 Read [ticket_storage_details.md](ticket_storage_details.md)「module_docs 工程模块库」段。

