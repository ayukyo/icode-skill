#!/usr/bin/env bash
# SKILL.md 提示预算 + 机器真源等价契约（§10: test_prompt_budget_contract）
# 目标：SKILL.md 瘦身为路由器 ≤40KB；被引用的「SKILL.md「XX」段」锚点全部保留（防断链）；
#       gates.json 状态机与 schema 为合法机器真源。
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

# 1) SKILL.md ≤ 40KB（降 ≥70%：原 160.8KB）
SIZE=$(wc -c < SKILL.md)
if [ "$SIZE" -le 40960 ]; then
  ok "SKILL.md 大小 $SIZE B ≤ 40KB"
else
  bad "SKILL.md 大小 $SIZE B 超 40KB"
fi

# 2) SKILL.md 内所有「SKILL.md「XX」段」被引用锚点文本均存在（防瘦身断链）
python3 - <<'PY'
import os, re, sys
text = open("SKILL.md", encoding="utf-8").read()
refs = set()
for root, _, files in os.walk("."):
    if ".git" in root: continue
    for fn in files:
        if not (fn.endswith(".md") or fn.endswith(".sh")): continue
        p = os.path.join(root, fn)
        try: c = open(p, encoding="utf-8").read()
        except Exception: continue
        for m in re.findall(r'SKILL\.md「([^」]*)」', c):
            refs.add(m)
missing = [r for r in refs if r not in text]
if missing:
    print("  ❌ 缺失锚点:", missing); sys.exit(1)
print("  ✅ 全部", len(refs), "个被引用锚点保留")
PY
[ $? -eq 0 ] && ok "被引用锚点全保留" || bad "存在缺失锚点"

# 3) gates.json state_machine 合法（机器真源：states/transitions/gate_policy/close_phases）
if python3 - <<'PY'
import json
g = json.load(open("mcp/workflow-gate/gates.json"))["state_machine"]
assert set(g["states"]) >= {"init_in_progress","plan_done","code_done","completed"}
assert len(g["transitions"]) >= 14
assert set(g["gate_policy"]["gated_targets"]) >= {"plan_done","review_done","plan_finalized","code_done","deepcheck_done","completed"}
assert g["close_phases"][0]=="close_planned" and g["close_phases"][-1]=="closed"
print("ok")
PY
then ok "gates.json state_machine 真源合法"; else bad "gates.json state_machine 非法"; fi

# 4) schema 三文件合法 JSON 且版本/枚举正确
if python3 - <<'PY'
import json
m = json.load(open("schemas/ticket-metadata.schema.json"))
assert m["properties"]["schema_version"]["const"]==3
assert "extensions" in m["properties"] and m["additionalProperties"] is False
assert "verification_runs" in m["properties"] and "close_state" in m["properties"]
e = json.load(open("schemas/ticket-event.schema.json"))
assert e["properties"]["event_type"]["enum"] and "previous_event_hash" in e["properties"]
i = json.load(open("schemas/ticket-index.schema.json"))
item_props = i["definitions"]["vnextEntry"]["properties"]
assert item_props["control_schema_version"]["const"] == 3
assert item_props["out_dir"]["pattern"].startswith(r"^\.icode_output/")
assert "archive_path" in item_props and "backup_path" in item_props
print("ok")
PY
then ok "三 schema 版本/枚举/结构合法"; else bad "schema 非法"; fi

# 5) tools/icode_control.py 可执行 + 控制面子命令齐全
if python3 tools/icode_control.py --help 2>&1 | grep -q 'resolve-ticket'; then
  help=$(python3 tools/icode_control.py --help 2>&1)
  missing=0
  for cmd in create resolve-ticket validate event transition metadata-update index-write index-update migration record-verification archive-manifest close-phase reopen snapshot; do
    printf '%s' "$help" | grep -q "$cmd" || missing=1
  done
  [ "$missing" -eq 0 ] && ok "icode_control.py 14 个控制面子命令可用" || bad "icode_control.py 子命令不完整"
else bad "icode_control.py 子命令缺失"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
