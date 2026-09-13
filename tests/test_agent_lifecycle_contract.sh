#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

CTL="python3 tools/icode_control.py"
TMP="$(mktemp -d /tmp/icode-agent-life.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
DIR="$TMP/.icode_output/.icode_output_1"
mkdir -p "$(dirname "$DIR")"

$CTL create --dir "$DIR" --ticket-id agent-life-1 --requirement agent-runtime \
  --birth init >/dev/null || exit 1

spawn_agent() {
  local request_id="$1" scope="$2"
  $CTL record-agent-spawn --dir "$DIR" \
    --task-scope "$scope" \
    --expected-artifact 'structured-result' \
    --evidence-boundary 'ticket-only' \
    --join-condition 'valid-json-or-terminal-error' \
    --backend fake --model fake-v1 \
    --capability text --request-id "$request_id"
}

OUT1="$(spawn_agent spawn-1 'review facts' 2>/dev/null)"
RC1=$?
if [ "$RC1" -eq 0 ] \
  && printf '%s' "$OUT1" | grep -q 'spawn_id' \
  && spawn_agent spawn-1 'review facts' 2>/dev/null | grep -q 'already_applied'; then
  ok "agent spawn 原子记录且同 request 幂等"
else
  bad "agent spawn 原子记录或幂等失败"
fi

SPAWN1="$(printf '%s' "$OUT1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["spawn"]["spawn_id"])' 2>/dev/null || true)"
spawn_agent spawn-2 'collect evidence' >/dev/null 2>&1
spawn_agent spawn-3 'challenge conclusion' >/dev/null 2>&1
if ! spawn_agent spawn-4 'overflow worker' >"$TMP/overflow.err" 2>&1 \
  && grep -q 'agent_concurrency_limit' "$TMP/overflow.err"; then
  ok "同工单最多三个开放 spawn"
else
  bad "开放 spawn 并发上限未 fail-closed"
fi

if [ -n "$SPAWN1" ] \
  && $CTL record-agent-result --dir "$DIR" --spawn-id "$SPAWN1" \
    --result joined --adopted yes --adoption-reason 'facts accepted' \
    --evidence-ref 'review:summary' --summary 'bounded facts' \
    --request-id result-1 >/dev/null \
  && $CTL record-agent-result --dir "$DIR" --spawn-id "$SPAWN1" \
    --result joined --adopted yes --adoption-reason 'facts accepted' \
    --evidence-ref 'review:summary' --summary 'bounded facts' \
    --request-id result-1 2>/dev/null | grep -q 'already_applied' \
  && spawn_agent spawn-4 'replacement worker' >/dev/null; then
  ok "agent result 原子终结、幂等且释放并发槽"
else
  bad "agent result 终结或并发槽释放失败"
fi

if ! $CTL record-agent-result --dir "$DIR" --spawn-id missing-spawn \
    --result failed --adopted no --adoption-reason 'backend failed' \
    --evidence-ref 'error:backend' --summary 'no output' \
    --error-class backend_error --request-id missing-result >"$TMP/missing.err" 2>&1 \
  && grep -q 'agent_spawn_not_found' "$TMP/missing.err"; then
  ok "未知 spawn 结果被拒"
else
  bad "未知 spawn 被错误终结"
fi

if ! $CTL record-agent-result --dir "$DIR" --spawn-id "$SPAWN1" \
    --result failed --adopted yes --adoption-reason 'invalid adoption' \
    --evidence-ref 'error:backend' --summary 'bad combination' \
    --request-id invalid-result >"$TMP/invalid.err" 2>&1 \
  && grep -q 'agent_result_adoption' "$TMP/invalid.err"; then
  ok "失败终态禁止标记采纳"
else
  bad "失败终态错误允许采纳"
fi

if ! $CTL metadata-update --dir "$DIR" \
    --set-json '{"extensions":{"agent":{"spawns":[]}}}' \
    --request-id bypass-agent >"$TMP/bypass.err" 2>&1 \
  && grep -q 'record-agent-spawn' "$TMP/bypass.err"; then
  ok "metadata-update 不能绕过 Agent 专用 writer"
else
  bad "Agent metadata 可被普通 writer 绕过"
fi

if ! $CTL event --dir "$DIR" --type agent_spawned --payload '{}' \
    --request-id forged-agent >"$TMP/forged.err" 2>&1 \
  && grep -q 'reserved_event_type' "$TMP/forged.err"; then
  ok "通用 event 拒绝伪造 Agent 生命周期"
else
  bad "通用 event 可伪造 Agent 生命周期"
fi

if ICODE_CONTROL_TEST_MODE=1 $CTL validate --dir "$DIR" --skip-linters \
    | grep -q '"ok": true'; then
  ok "Agent metadata 可由事件链重建且 validate 通过"
else
  bad "Agent 生命周期语义校验失败"
fi

CLI="python3 tools/icode_agent.py"
DIR2="$TMP/.icode_output/.icode_output_2"
$CTL create --dir "$DIR2" --ticket-id agent-cli-2 --requirement cli \
  --birth init >/dev/null || exit 1
if $CLI --help 2>/dev/null | grep -q 'run' \
  && $CLI status --dir "$DIR2" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["ok"] and d["status"]["schema_version"] == 1
assert d["status"]["open_spawns"] == []
'; then
  ok "Agent CLI 暴露版本化只读 status"
else
  bad "Agent CLI status 合同失败"
fi

if $CLI run --dir "$DIR2" --backend fake --model fake-v1 \
    --capability text --prompt 'summarize' --instructions 'bounded answer' \
    --task-scope 'cli smoke' --expected-artifact 'text answer' \
    --evidence-boundary 'ticket-only' --join-condition 'non-empty output' \
    --request-id cli-turn-1 --fake-response 'offline answer' \
  | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["ok"] and d["result"]["output_text"] == "offline answer"
' \
  && $CLI status --dir "$DIR2" | python3 -c '
import json,sys
d=json.load(sys.stdin)["status"]
assert len(d["spawns"]) == 1 and d["open_spawns"] == []
assert d["spawns"][0]["result"] == "joined"
' \
  && ICODE_CONTROL_TEST_MODE=1 $CTL validate --dir "$DIR2" --skip-linters \
     | grep -q '"ok": true'; then
  ok "Agent CLI FakeBackend 离线全链路可运行"
else
  bad "Agent CLI 离线全链路失败"
fi

if $CTL trace --dir "$DIR2" | python3 -c '
import json,sys
d=json.load(sys.stdin)
types=[e["type"] for e in d["events"]]
assert "agent_spawned" in types and "agent_result" in types
assert d["open_agents"] == {}
'; then
  ok "Agent 生命周期进入统一 trace 且无伪开放项"
else
  bad "Agent 生命周期未进入统一 trace"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
