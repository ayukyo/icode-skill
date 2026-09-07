#!/usr/bin/env bash
# 控制面故障注入回归：出生/事件/事务/幂等/并发索引/verify 语义。
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
T=$(mktemp -d /tmp/icoreg.XXXXXX); trap 'rm -rf "$T"' EXIT
WS="$T/ws"; mkdir -p "$WS/.icode_output"

# 1) create 原子产生 metadata + 出生事件。
D1="$WS/.icode_output/.icode_output_1"
if $CTL create --dir "$D1" --ticket-id reg-1 --requirement r --birth plan \
    --request-id create-1 >/dev/null \
   && $CTL validate --dir "$D1" --skip-linters | grep -q '"ok": true'; then
  ok "create 原子出生后 validate 通过"
else bad "create 原子出生契约失败"; fi

# 1b) create 不得认领非空目录、跨 normal/debug 域或接受控制字段注入。
DNE="$WS/.icode_output/.icode_output_6"; mkdir -p "$DNE"; touch "$DNE/existing.txt"
if $CTL create --dir "$DNE" --ticket-id reg-ne --requirement r --birth plan 2>/dev/null \
    | grep -q 'ticket_create_nonempty'; then ok "create 拒绝认领非空目录"; else bad "create 覆盖非空目录"; fi
DISO="$WS/.icode_output/.icode_output_7"
if $CTL create --dir "$DISO" --ticket-id reg-iso --requirement r --birth debug-log 2>/dev/null \
    | grep -q 'birth_directory_isolation'; then ok "create 强制 normal/debug 域隔离"; else bad "create 接受跨域出生"; fi
DPF="$WS/.icode_output/.icode_output_8"
if $CTL create --dir "$DPF" --ticket-id reg-pf --requirement r --birth plan \
    --metadata-json '{"status":"completed"}' 2>/dev/null | grep -q 'birth_protected_fields'; then
  ok "create 拒绝注入控制字段"
else bad "create 接受控制字段注入"; fi

# 1c) 业务 metadata 也必须经单一 writer：set/append 原子幂等，控制字段受保护。
META_WS="$T/meta-ws"; mkdir -p "$META_WS"
DMETA="$META_WS/.icode_output/.icode_output_1"
$CTL create --dir "$DMETA" --ticket-id reg-meta --requirement r --birth init >/dev/null
if $CTL metadata-update --dir "$DMETA" --set-json '{"keywords":["controlled"]}' \
    --append-json '{"requirement_points":["p1"]}' --request-id meta-1 >/dev/null \
   && $CTL metadata-update --dir "$DMETA" --set-json '{"keywords":["controlled"]}' \
    --append-json '{"requirement_points":["p1"]}' --request-id meta-1 \
      | grep -q already_applied \
   && python3 - "$DMETA/.ico_metadata.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); assert d["keywords"]==["controlled"]
assert d["requirement_points"]==["p1"]
PY
then ok "metadata-update 原子 set/append 且幂等"
else bad "metadata-update 原子/幂等契约失败"; fi
if $CTL metadata-update --dir "$DMETA" --set-json '{"status":"completed"}' 2>/dev/null \
    | grep -q metadata_update_protected; then
  ok "metadata-update 拒绝改写生命周期控制字段"
else bad "metadata-update 可绕过 transition"; fi
python3 - "$DMETA/.ico_metadata.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["requirement_summary"]="bypass"
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
if $CTL validate --dir "$DMETA" --skip-linters 2>/dev/null \
    | grep -q metadata_hash_after; then
  ok "绕过 writer 直写非状态字段也被检测"
else bad "非状态 metadata 直写未被检测"; fi

# 2) 直写完成态不得伪装成出生态。
python3 - "$D1/.ico_metadata.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["status"]="plan_done"; d["completed_steps"]=["1"]
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
if $CTL validate --dir "$D1" --skip-linters 2>/dev/null | grep -q 'status_event_consistency'; then
  ok "绕过 transition 直写 plan_done 被拦截"
else bad "直写 plan_done 未被拦截"; fi

# 3) 即使重算 hash，非法事件 schema 仍必须拒绝。
D2="$WS/.icode_output/.icode_output_2"
$CTL create --dir "$D2" --ticket-id reg-2 --requirement r --birth plan >/dev/null
python3 - "$D2/.ico_events.jsonl" <<'PY'
import hashlib,json,sys
p=sys.argv[1]; e=json.loads(open(p).readline()); e["timestamp"]="bad"
m={k:v for k,v in e.items() if k!="event_hash"}
e["event_hash"]=hashlib.sha256(json.dumps(m,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
open(p,"w").write(json.dumps(e,ensure_ascii=False,sort_keys=True)+"\n")
PY
if $CTL validate --dir "$D2" --skip-linters 2>/dev/null | grep -q 'event_schema'; then
  ok "非法事件即使自洽 hash 仍被 schema 拒绝"
else bad "非法事件 schema 未被校验"; fi

# 3b) 字段合法且重算 hash 的非法生命周期事件也必须被语义校验拒绝。
D11="$WS/.icode_output/.debug/.icode_output_11"
$CTL create --dir "$D11" --ticket-id debug:reg-11 --requirement r --birth debug-log >/dev/null
python3 - "$D11/.ico_events.jsonl" <<'PY'
import hashlib,json,sys,uuid
p=sys.argv[1]; rows=[json.loads(x) for x in open(p) if x.strip()]; prev=rows[-1]["event_hash"]
e={"schema_version":1,"event_id":str(uuid.uuid4()),"ticket_id":"debug:reg-11","request_id":None,
   "timestamp":"2026-09-07T00:00:00","actor":"icode","event_type":"state_changed",
   "payload":{"from":"init_in_progress","to":"completed","delivery_verdict":"verified"},
   "previous_event_hash":prev,"event_hash":""}
m={k:v for k,v in e.items() if k!="event_hash"}
e["event_hash"]=hashlib.sha256(json.dumps(m,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
open(p,"a").write(json.dumps(e,ensure_ascii=False,sort_keys=True)+"\n")
PY
if $CTL validate --dir "$D11" --skip-linters 2>/dev/null | grep -q 'event_semantics'; then
  ok "自洽 hash 的非法生命周期事件被拒绝"
else bad "非法生命周期事件绕过语义校验"; fi

# 4) 生产环境 --skip-gates 必须 fail-closed。
D3="$WS/.icode_output/.icode_output_3"
$CTL create --dir "$D3" --ticket-id reg-3 --requirement r --birth plan >/dev/null
if (unset ICODE_CONTROL_TEST_MODE; $CTL transition --dir "$D3" --to plan_done --skip-gates) 2>/dev/null \
    | grep -q 'production_gate_bypass'; then
  ok "生产 --skip-gates 被拒绝"
else bad "生产 --skip-gates 可绕过门禁"; fi

# 5) --latest 必须按数字后缀，不得按字典序把 9 当作比 10 新。
D9="$WS/.icode_output/.icode_output_9"; D10="$WS/.icode_output/.icode_output_10"
$CTL create --dir "$D9" --ticket-id reg-9 --requirement r --birth init >/dev/null
$CTL create --dir "$D10" --ticket-id reg-10 --requirement r --birth init >/dev/null
if $CTL resolve-ticket --latest --workspace "$WS" 2>/dev/null | grep -q '"ticket_id": "reg-10"'; then
  ok "--latest 按数字后缀选择"
else bad "--latest 仍受字典序影响"; fi

# 6) metadata+event 二阶写的事件故障必须回滚 metadata。
D4="$WS/.icode_output/.icode_output_4"
$CTL create --dir "$D4" --ticket-id reg-4 --requirement r --birth plan >/dev/null
if python3 - "$D4" <<'PY'
import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location("ctl","tools/icode_control.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d=pathlib.Path(sys.argv[1]); before=m.load_metadata(d); after=dict(before); after["indexed"]=True
def fail(*_a,**_k): raise OSError("injected append failure")
m.append_prepared_event=fail
try:
    with m.DirLock(d): m.commit_metadata_and_event(d,before,after,"external_note",{"x":1})
except OSError:
    pass
else:
    raise AssertionError("fault did not fire")
assert m.load_metadata(d)==before
assert not (d/m.TXN_NAME).exists()
PY
then ok "事件追加失败时 metadata 回滚"; else bad "半写回滚契约失败"; fi

# 6b) linter 运行窗口内任何 metadata/事件变化都必须让旧门禁结果失效。
DCON="$WS/.icode_output/.icode_output_12"
$CTL create --dir "$DCON" --ticket-id reg-12 --requirement r --birth plan >/dev/null
if python3 - "$DCON" <<'PY'
import importlib.util,pathlib,sys,types
spec=importlib.util.spec_from_file_location("ctl","tools/icode_control.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d=pathlib.Path(sys.argv[1])
def mutate_during_lint(*_a,**_k):
    meta=m.load_metadata(d); meta["requirement_summary"]="raced"
    m.atomic_write_json(d/m.METADATA_NAME, meta)
    return [{"gate_id":"injected","ok":True}]
m.run_gate_linters=mutate_during_lint
args=types.SimpleNamespace(dir=str(d),to="plan_done",delivery_verdict=None,
                           request_id=None,skip_gates=False)
try:
    m.cmd_transition(args)
except m.ControlError as exc:
    assert exc.extra.get("gate_id")=="concurrent_write"
else:
    raise AssertionError("stale gate result advanced state")
assert m.load_metadata(d)["status"]=="init_in_progress"
PY
then ok "门禁窗口并发变更使旧结果失效"
else bad "旧门禁结果跨并发变更推进状态"; fi

# 7) request_id 同键改 payload 必须报冲突。
$CTL event --dir "$D4" --type external_note --payload '{"v":1}' --request-id same-1 >/dev/null
if $CTL event --dir "$D4" --type external_note --payload '{"v":2}' --request-id same-1 2>/dev/null \
    | grep -q 'idempotency_conflict'; then
  ok "request_id 同键异操作被拒绝"
else bad "request_id 冲突未拦截"; fi
if $CTL event --dir "$D4" --type state_changed \
    --payload '{"from":"init_in_progress","to":"completed"}' 2>/dev/null \
    | grep -q 'reserved_event_type'; then
  ok "通用 event 拒绝伪造控制事件"
else bad "通用 event 可伪造控制事件"; fi
if $CTL transition --dir "$D4" --to plan_done --delivery-verdict verified 2>/dev/null \
    | grep -q 'delivery_verdict_target'; then
  ok "非 completed 状态拒绝 delivery_verdict"
else bad "delivery_verdict 可污染中间态"; fi

# 7b) snapshot 事件追加失败时不得留虚假快照；linter exit 0 但非 JSON 也须 fail-closed。
if python3 - "$D4" <<'PY'
import importlib.util,pathlib,sys,types
spec=importlib.util.spec_from_file_location("ctl","tools/icode_control.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d=pathlib.Path(sys.argv[1]); before_events=m.read_events(d)[0]
def fail(*_a,**_k): raise OSError("injected snapshot event failure")
m.append_prepared_event=fail
try:
    m.cmd_snapshot(types.SimpleNamespace(dir=str(d), verify=False))
except OSError:
    pass
else:
    raise AssertionError("snapshot fault did not fire")
assert not (d/m.SNAPSHOT_NAME).exists()
assert m.read_events(d)[0] == before_events
m.subprocess.run=lambda *_a,**_k: types.SimpleNamespace(
    returncode=0, stdout="not-json", stderr="")
reports=m.run_gate_linters(d, to_status="plan_done")
assert reports and all(not item["ok"] and item.get("fail_closed") for item in reports)
PY
then ok "snapshot 失败回滚且 linter 非 JSON fail-closed"
else bad "snapshot/linter 故障边界未闭合"; fi

# 8) record-verification 原子、幂等，且不污染 patch_history。
if $CTL record-verification --dir "$D4" --kind listen --outcome inconclusive \
    --evidence 'window:no-trigger' --request-id verify-1 >/dev/null \
   && $CTL record-verification --dir "$D4" --kind listen --outcome inconclusive \
    --evidence 'window:no-trigger' --request-id verify-1 | grep -q already_applied \
   && python3 - "$D4/.ico_metadata.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); assert len(d["verification_runs"])==1; assert "patch_history" not in d
PY
then ok "verify 记录原子幂等且不写 patch_history"
else bad "verify/patch 分离契约失败"; fi

# 9) 文档实际使用的对象/布尔/文本字段可通过严格 schema。
D5="$WS/.icode_output/.icode_output_5"
SHAPES='{"tdd":{"mode":"contract","status":"not_assessed","reason":"r","red":null,"green":null,"regression":null},"fix_tiers":{"A":[],"B":[],"C":[]},"unresolved_issues_at_cap":false,"test_failures":false,"code_review_fix_with_issues":false,"correct_direction":"next","tb_source":{"lib":"L","num":1,"pid":"P"}}'
if $CTL create --dir "$D5" --ticket-id reg-5 --requirement r --birth init \
    --metadata-json "$SHAPES" >/dev/null \
   && $CTL validate --dir "$D5" --skip-linters | grep -q '"ok": true'; then
  ok "实际 metadata 字段形状通过严格 schema"
else bad "实际 metadata 字段形状被误拒"; fi

# 10) 多进程索引写不得丢条目。
IDX="$T/index.json"; PIDS=""; count=20
for n in $(seq 20 $((20+count-1))); do
  d="$WS/.icode_output/.icode_output_$n"
  $CTL create --dir "$d" --ticket-id "reg-$n" --requirement r --birth init >/dev/null
  ($CTL index-write --ticket-dir "$d" --index "$IDX" >/dev/null) & PIDS="$PIDS $!"
done
all_ok=1
for pid in $PIDS; do wait "$pid" || all_ok=0; done
if [ "$all_ok" -eq 1 ] && python3 - "$IDX" "$count" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); ids=[x["ticket_id"] for x in d["tickets"]]
assert len(ids)==int(sys.argv[2]) and len(ids)==len(set(ids))
PY
then ok "并发索引写入无丢失"; else bad "并发索引丢条目或重复"; fi

# 11) 同 ticket 刷新保留顶层扩展与历史条目字段。
python3 - "$IDX" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["custom_top"]={"keep":True}
e=next(x for x in d["tickets"] if x["ticket_id"]=="reg-20")
e["legacy_field"]="keep"; e["last_used_at"]="2000-01-01T00:00:00"
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
$CTL index-write --ticket-dir "$WS/.icode_output/.icode_output_20" --index "$IDX" >/dev/null
if python3 - "$IDX" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); e=next(x for x in d["tickets"] if x["ticket_id"]=="reg-20")
assert d["custom_top"]["keep"] is True and e["legacy_field"]=="keep"
assert e["last_used_at"] == "2000-01-01T00:00:00"
PY
then ok "索引刷新保留扩展/历史字段且不伪续期"; else bad "索引刷新丢失字段或错误续期"; fi

# 12) 索引独有字段必须经受控入口更新，且未知字段 fail-closed。
$CTL index-update --ticket-id reg-20 --increment-hit --set-json '{"stale":false}' --index "$IDX" >/dev/null
if python3 - "$IDX" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); e=next(x for x in d["tickets"] if x["ticket_id"]=="reg-20")
assert e["hit_count"] == 1 and e["stale"] is False and e["legacy_field"] == "keep"
PY
then ok "index-update 原子更新且保留历史字段"; else bad "index-update 更新异常"; fi
if $CTL index-update --ticket-id reg-20 --set-json '{"unregistered":true}' --index "$IDX" >/dev/null 2>&1; then
  bad "index-update 接受未登记字段"
else
  ok "index-update 拒绝未登记字段"
fi

# 13) 真实部署中的 legacy 混合索引可读；被当前工单接管的单条必须升级为严格 v3。
LEGACY_IDX="$T/legacy-index.json"
python3 - "$LEGACY_IDX" "$WS" <<'PY'
import json,sys
json.dump({"version":"1", "updated_at":"old", "tickets":[
    {"ticket_id":"reg-5", "project_path":sys.argv[2], "out_dir":".icode_output",
     "status":"legacy_custom", "keywords":"legacy-string", "legacy_field":"keep"},
    {"ticket_id":"old-only", "out_dir":"/legacy/absolute/path", "status":"superseded"}
]}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
PY
if $CTL index-write --ticket-dir "$D5" --index "$LEGACY_IDX" >/dev/null \
   && $CTL validate --dir "$D5" --index "$LEGACY_IDX" --skip-linters \
      | grep -q '"ok": true' \
   && python3 - "$LEGACY_IDX" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); mine=next(x for x in d["tickets"] if x["ticket_id"]=="reg-5")
old=next(x for x in d["tickets"] if x["ticket_id"]=="old-only")
assert d["version"] == 1 and mine["control_schema_version"] == 3
assert mine["out_dir"] == ".icode_output/.icode_output_5"
assert isinstance(mine.get("keywords", []), list) and mine["legacy_field"] == "keep"
assert "control_schema_version" not in old
PY
then ok "legacy 混合索引可读且目标条目受控升级"
else bad "legacy 索引兼容或目标条目升级失败"; fi

# 14) 损坏索引中的重复身份必须 fail-closed，不得静默挑一条覆盖。
python3 - "$IDX" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); e=next(x for x in d["tickets"] if x["ticket_id"]=="reg-20")
d["tickets"].append(dict(e)); json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
if $CTL index-update --ticket-id reg-20 --increment-hit --index "$IDX" 2>/dev/null \
    | grep -q 'index_precheck'; then
  ok "重复索引身份触发 fail-closed"
else bad "重复索引身份被静默覆盖"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
