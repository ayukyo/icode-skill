# 步骤 bak — 工程工单手动备份（独立步骤，不参与 1~6 流程推进）

**命令**: `/icode bak [--project <path>]`
**产出**: `~/.claude/icode_data/project_backup/<project_id>/bak_<时间戳>/`（工程 `.icode_output/` 全量快照：工单 `.icode_output_N/` + `.debug/` 调试孪生 + `limit.local` + `ppt`）+ 快照内 `MANIFEST.json` + `<project_id>/latest` 符号链接（指向最新快照）+ 全局索引 `backup_path` 字段更新
**会话**: 主会话
**定位**: **独立备份步骤**——把工程全部工单产物备份到全局 `~/.claude/icode_data/`，**可多次执行**（每次生成新时间戳快照，`rsync --link-dest` 硬链接去重未变文件）。**不创建工单目录、不写工单 metadata、不更新 `completed_steps`/`status`、不参与步骤1~6推进、不改工程内任何文件**。

> **为什么需要**：工单完整产物（`00_init`/`01_plan`/`03_plan_final`/`04_code`/`06_audit`/`log_analysis`/`08_patch`/`tb_source`/…）存放在工程内 `.icode_output/`，**删工程目录即永久丢失**——全局索引只存摘要+指针，不存产物正文（见 SKILL.md「全局索引」）。**`/icode bak` 是删工程前的安全网**：删工程前跑一次，工单完整产物即在全局留底；工程被删后，检索命中该工程工单时从备份读完整产物走历史参考（**工程优先 → 备份兜底 → stale**，详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「过时校验·备份工单」）。
>
> ⚠️ **边界（如实说明）**：工程已删后再跑 bak **无复制源、无法事后补救**——必须先备份后删除。

## 0. 前置校验

1. **源工程存在**：`test -d {src_root}` 失败 → 报错「工程不存在（bak 是删工程前的安全网，工程已删则无复制源，无法事后补救）」退出
2. **有工单可备份**：`test -d {src_root}/.icode_output` 失败 → 提示「该工程无 .icode_output/，无工单可备份」退出（**不建备份目录**）
3. **rsync 可用性**：`command -v rsync` 失败 → 退化 `cp -r` 全量复制（无硬链接去重），提示去重未生效不阻断

## 1. 参数解析与源工程定位

- `--project <path>`：指定要备份的工程根（绝对路径或 `~` 展开）；不传 → 用当前目录所在 git 仓库根
- 源工程根：`git rev-parse --show-toplevel`（cwd 场景；非 git 仓库 → cwd 本身）
- `project_id` = 源工程根 basename（仅备份目录组织用；索引 `backup_path` 按 `ticket_id` 匹配，不依赖 project_id 解析）

## 2. 执行备份

```bash
BAK_ROOT="$HOME/.claude/icode_data/project_backup/<project_id>"
mkdir -p "$BAK_ROOT"
# 快照名：运行时取真实系统时间（禁止写死/固定值，对齐「当前时间取值约定」）
SNAP="bak_$(date +%Y%m%d_%H%M%S)"
SNAP_DIR="$BAK_ROOT/$SNAP"
mkdir -p "$SNAP_DIR"
# rsync --link-dest 硬链接去重：未变文件指向上一快照，不占新空间；禁止 --delete（备份绝不删旧文件）
PREV="$(readlink -f "$BAK_ROOT/latest" 2>/dev/null || true)"
if [ -n "$PREV" ] && [ -d "$PREV" ]; then
  rsync -a --link-dest="$PREV" --exclude='*.tmp' --exclude='*.broken.*' "<src_root>/.icode_output/" "$SNAP_DIR/"
else
  rsync -a --exclude='*.tmp' --exclude='*.broken.*' "<src_root>/.icode_output/" "$SNAP_DIR/"
fi
# rsync 缺失时退化（全量复制，无去重）：
# cp -r "<src_root>/.icode_output/" "$SNAP_DIR/"
# 验证：快照非空 + 工单目录计数（磁盘状态为准，禁止 echo 伪确认）
test -n "$(ls -A "$SNAP_DIR")" || { echo "❌ 快照为空，备份失败"; exit 1; }
echo "工单目录数: $(ls -d "$SNAP_DIR"/.icode_output_* 2>/dev/null | wc -l)"
```

## 3. 写 MANIFEST.json（快照内）

python 生成（`datetime.now()` 运行时取真实时间；字段可回溯本次备份的源/范围/上一快照）：

```python
python3 - "$SNAP_DIR" "$SRC_ROOT" "$PROJECT_ID" "$PREV" <<'PY'
import json, os, sys, glob, datetime
SNAP_DIR, SRC_ROOT, PROJECT_ID, PREV = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] or None
ticket_dirs = sorted(os.path.basename(d) for d in glob.glob(os.path.join(SNAP_DIR, ".icode_output_*")))
def has(sub):
    return os.path.isdir(os.path.join(SNAP_DIR, sub))
manifest = {
    "command": "bak",
    "snapshot": os.path.basename(SNAP_DIR),
    "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "project_id": PROJECT_ID,
    "source_path": SRC_ROOT,
    "source_out_dir": os.path.join(SRC_ROOT, ".icode_output"),
    "ticket_count": len(ticket_dirs),
    "ticket_dirs": ticket_dirs,
    "has_debug": has(".debug"),
    "has_limit_local": has("limit.local"),
    "has_ppt": has("ppt"),
    "prev_snapshot": PREV,
}
with open(os.path.join(SNAP_DIR, "MANIFEST.json"), "w") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
PY
```

## 4. 更新 latest 符号链接

```bash
ln -sfn "$SNAP_DIR" "$BAK_ROOT/latest"
```

## 5. 更新全局索引 backup_path

**目的**：让「工程被删后索引仍能从备份找到工单」成立——这是本命令与检索衔接的核心。遍历快照内每个 `.icode_output_N/.ico_metadata.json` 取 `ticket_id`，命中全局索引则置 `backup_path = 该快照目录`（指向最新快照，覆盖旧值）。**写前重读合并 + 原子写**（对齐「全局索引写入」并发安全契约）：

```python
python3 - "$SNAP_DIR" <<'PY'
import json, os, sys, glob, datetime
SNAP_DIR = sys.argv[1]
idx_path = os.path.expanduser("~/.claude/icode_data/index.json")
if not os.path.exists(idx_path):
    print("索引不存在，跳过 backup_path 更新（备份文件已落盘）")
    sys.exit(0)
with open(idx_path) as f:
    idx = json.load(f)
by_id = {t.get("ticket_id"): t for t in idx["tickets"]}
updated = []
for d in sorted(glob.glob(os.path.join(SNAP_DIR, ".icode_output_*"))):
    meta = os.path.join(d, ".ico_metadata.json")
    if not os.path.isfile(meta):
        continue
    with open(meta) as f:
        tid = json.load(f).get("ticket_id", "")
    if tid and tid in by_id and by_id[tid].get("backup_path") != d:
        by_id[tid]["backup_path"] = d
        updated.append(tid)
if updated:
    idx["updated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    tmp = idx_path + ".bak_tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    os.replace(tmp, idx_path)  # 原子写，防写中断损坏
    print(f"更新 backup_path: {len(updated)} 条")
else:
    print("无新增 backup_path 更新（已指向最新快照或工单未索引）")
PY
```

> 索引回写只扫快照内顶层 `.icode_output_*/`；`ticket_id` 为空的目录（含 debug 孪生——快照内位于 `.debug/` 子路径、glob 扫不到）及无 `.ico_metadata.json` 的目录**文件照常备份**，仅不写 `backup_path`。

## 6. 收尾报告

- 快照路径（`$SNAP_DIR`）+ 是否 `latest` 已指向 + 上一快照（去重基线）
- 工单目录数 + 快照体积（`du -sh "$SNAP_DIR"`）
- 更新 `backup_path` 的索引条目数
- 若 rsync 退化 cp：提示「硬链接去重未生效，本次为全量复制」

## 边界处理

| 场景 | 行为 |
|------|------|
| 工程无 `.icode_output/` | 提示无工单可备份，退出（不建备份目录） |
| 工程已删（`--project` 指向不存在路径） | 报错「工程不存在，bak 是删工程前的安全网，无复制源无法事后补救」 |
| 非 git 仓库 | 源工程根 = cwd，project_id = cwd basename（对齐 doc/list 降级） |
| 全局索引不存在 | 备份照做；索引更新跳过并提示 |
| 工单目录无 `.ico_metadata.json`（垃圾目录）/ `ticket_id` 为空（含 debug 孪生，快照内位于 `.debug/`） | 文件照备份；索引回写只扫快照顶层 `.icode_output_*`，不写 backup_path |
| rsync 缺失 | 退化 `cp -r` 全量复制（无去重），提示不阻断 |
| 快照名冲突（时间戳到秒） | 实际不可能；冲突则报错不覆盖（禁删旧快照） |

## 反偷懒

- **禁止硬编码快照名/时间**：`date` 运行时取（对齐「当前时间取值约定」）
- **禁止只备份部分工单**：全量 `.icode_output/`（工单 + `.debug` + limit.local + ppt），不得挑拣
- **禁止删/覆盖旧快照**：每次新建时间戳快照，保留历史（多次备份是特性）
- **禁止 echo 伪确认**：用 `ls -A` 非空 + 工单目录计数验证磁盘状态
- **禁止改工程内文件**：源工程只读，绝不写工程内任何文件
- **禁止跳过索引 `backup_path` 更新**：那是「工程被删后索引仍能从备份找到工单」的前提
- **禁止写死全局路径**：用 `~` 表达（可移植，对齐索引文件路径约定）

## 示例

```bash
/icode bak                          # 备份当前工程全部工单（默认：cwd 的 git 仓库根）
/icode bak --project ~/work/myproj  # 备份指定工程
```

## 与检索的衔接（写 backup_path 后的行为契约）

`/icode bak` 后，匹配工单的索引条目带 `backup_path = <快照>/.icode_output_N`。检索过时校验时，`project_path` 失效但 `backup_path` 有效 → 该工单为 **backup 活跃态（不标 stale）**，从备份读完整产物注入（历史参考语义，须实证），命中正常续期 + 按 verdict 分流（详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「过时校验·备份工单」）。**工程优先**：`project_path` 有效（工程恢复/重克隆）永远优先走工程，备份仅兜底。

## MCP 推荐

本步骤为 **L0（确定性执行，不强制思考）**（见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段）：纯文件复制 + 索引字段更新，无 LLM 分析子任务，不调用 sequential-thinking，也**不推荐**其他 MCP。
