#!/usr/bin/env bash
# verify/patch 语义分离 + doc 工作清单契约（§10: test_patch_verify_separation / test_doc_worklist_resume）
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

T=$(mktemp -d /tmp/icoverify.XXXXXX); trap 'rm -rf "$T"' EXIT

# 1) verify 步骤存在且为纯验证（不写 patch_history）
if [ -f steps/verify.md ]; then ok "steps/verify.md 存在"; else bad "steps/verify.md 缺失"; fi
if grep -q 'patch_history' steps/verify.md && grep -q '不写 `patch_history`' steps/verify.md; then
  ok "verify 声明与 patch_history 分离"; else bad "verify 未声明 patch_history 分离"; fi
if grep -q '不自动升级' steps/verify.md && grep -q 'delivery_verdict' steps/verify.md; then
  ok "verify 不自动升级 delivery_verdict"; else bad "verify 未声明 delivery_verdict 不自动升级"; fi

# 2) patch 只保留修改后监听；显式触发验证归 verify --test，结果仍记 verification_runs
if grep -q '仅用于 patch 修改后的自动监听' steps/08_patch.md \
  && grep -q '/icode verify --test <target>' steps/08_patch.md \
  && ! grep -Eq '/icode patch --test([[:space:]`]|$)' steps/08_patch.md \
  && grep -q 'verification_runs' steps/08_patch.md; then
  ok "08_patch 仅保留 --listen，显式测试归 verify + verification_runs 分离"
else
  bad "08_patch 的 patch/verify 分离契约不完整"
fi

# 3) metadata schema 含 verification_runs 严格结构（kind/outcome 枚举）
if python3 - <<'PY'
import json
s=json.load(open("schemas/ticket-metadata.schema.json"))
v=s["properties"]["verification_runs"]["items"]
assert v["required"]==["run_id","at","kind","outcome"], v["required"]
assert v["properties"]["kind"]["enum"]==["deploy","listen","device_test"]
assert v["properties"]["build_source"]["enum"]==["fresh","reused","existing","unknown",None]
assert v["properties"]["outcome"]["enum"]==["pass","fail","inconclusive"]
print("ok")
PY
then ok "verification_runs schema 严格枚举"; else bad "verification_runs schema 不符"; fi

# 4) doc_worklist.json 契约：状态机文档 + 幂等续跑语义
if grep -q 'doc_worklist.json' steps/doc.md && grep -q 'pending | generated | verified | done | blocked | deferred | out_of_scope' steps/doc.md; then
  ok "doc.md 声明 doc_worklist 状态机"; else bad "doc.md 未声明 doc_worklist 状态机"; fi
if grep -q '幂等续跑' steps/doc.md && grep -q 'request_id' steps/doc.md; then
  ok "doc.md 声明幂等续跑 + request_id"; else bad "doc.md 未声明幂等续跑"; fi

# 5) doc_worklist.json 样例结构合法（写一个最小 worklist 再读回）
WL="$T/doc_worklist.json"
cat > "$WL" <<'EOF'
{
  "schema_version": 1, "project_id": "demo", "request_id": "req-1", "generated_at": "2026-09-07T00:00:00Z",
  "items": [
    {"id":"00_overview","kind":"project_chapter","candidate":"overview","reason":"固定章节","baseline":"abc123","target":"00_overview.md","status":"done","note":""}
  ]
}
EOF
if python3 - "$WL" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
assert d["schema_version"]==1 and d["request_id"]=="req-1"
assert d["items"][0]["status"]=="done"
print("ok")
PY
then ok "doc_worklist.json 样例结构合法"; else bad "doc_worklist 样例结构非法"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
