#!/usr/bin/env bash
# 契约测试：reasoning gate（分级思考）机器真源 + 运行时校验器
#
# 覆盖 ICODE_SEQUENTIAL_THINKING_OPTIMIZATION.md §11.1 契约测试：
#   11.1.1 静态契约（SKILL/thinking_core/mcp_per_step 引用 trace 与 catalog；
#          不再出现「所有步骤必用 sequential-thinking / 每步至少 3 步」有效契约；
#          register_mcp.py 默认注入 DISABLE_THOUGHT_LOGGING）
#   11.1.2 运行时 fixture（L0/L1/L2/L3 履行、tier 降级、L2 未履行、
#          degraded 无证据、L3 无对抗、over_invoked 灰度、非法触发器、旧工单兼容、
#          敏感数据 / thought 正文、坏 JSON、--step 过滤）
#   11.1.3 样本工单形状回归（L2/L3 全履行 + L0/L1 无 over_invoked）
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LINT="python3 tools/lint_thinking_gate.py"

# ---- 工具函数 ----
make_ticket() {  # $1=dir  $2=metadata_json
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}
write_trace() {  # $1=dir  $2..=jsonl 行
  local dir="$1"; shift
  : > "$dir/.thinking_gate_trace.jsonl"
  for line in "$@"; do
    printf '%s\n' "$line" >> "$dir/.thinking_gate_trace.jsonl"
  done
}
# 生成一条合法 trace 行（外层传参用单引号）
trace_line() {  # $1=step $2=tier $3=default_tier $4=mechanism $5=result $6=triggers_json $7=degraded_reason
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" "$7" <<'PY'
import json, sys, datetime
step, tier, dtier, mech, result, trig, degraded = sys.argv[1:]
print(json.dumps({
  "schema_version": 1, "workflow_version": "2.x", "ticket_id": "demo-ticket",
  "step": step, "tier": tier, "default_tier": dtier,
  "triggers": json.loads(trig), "mechanism": mech,
  "attempted": True,
  "result": result,
  "degraded_reason": degraded if degraded != "null" else None,
  "over_invoked": (mech in ("sequential-thinking", "sequential-thinking+adversarial") and tier in ("L0", "L1")),
  "at": datetime.datetime.now(datetime.timezone.utc).isoformat()
}, ensure_ascii=False))
PY
}

echo "=== 11.1.1 静态契约 ==="
check_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3 ($1 缺: $2)"; fi
}
check_not_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then bad "$3 ($1 意外含: $2)"; else ok "$3"; fi
}

check_contains SKILL.md "\.thinking_gate_trace\.jsonl" "SKILL.md 引用 .thinking_gate_trace.jsonl"
check_contains SKILL.md "reasoning-gate" "SKILL.md 引用 reasoning-gate"
check_contains references/thinking_core.md "\.thinking_gate_trace\.jsonl" "thinking_core.md 引用 .thinking_gate_trace.jsonl"
check_contains references/thinking_core.md "reasoning-gate" "thinking_core.md 引用 reasoning-gate"
check_contains references/mcp_per_step.md "\.thinking_gate_trace\.jsonl" "mcp_per_step.md 引用 .thinking_gate_trace.jsonl"
check_contains references/mcp_per_step.md "reasoning-gate" "mcp_per_step.md 引用 reasoning-gate"
# 有效旧契约必须消失：不得再有「所有步骤必用 sequential-thinking / 每步至少 3 步」作为有效契约
check_not_contains SKILL.md "所有步骤必用" "SKILL.md 不再出现「所有步骤必用」有效契约"
check_not_contains references/thinking_core.md "至少 3 步" "thinking_core.md 不再出现「至少 3 步」作为统一契约"
check_not_contains references/thinking_core.md "所有步骤必用" "thinking_core.md 不再出现「所有步骤必用」式强制"
check_not_contains references/mcp_per_step.md "所有步骤必用" "mcp_per_step.md 不再出现「所有步骤必用」有效契约"
check_not_contains references/mcp_per_step.md "每步至少 3 步" "mcp_per_step.md 不再出现「每步至少 3 步」"
# register_mcp.py 默认注入 DISABLE_THOUGHT_LOGGING
check_contains mcp/sequential-thinking/scripts/register_mcp.py "DISABLE_THOUGHT_LOGGING" "register_mcp.py 注入 DISABLE_THOUGHT_LOGGING"
# 触发器 ID 拼写一致：catalog 与步骤文档/引用文档
for trig in multiple_candidates multi_module_multi_file concurrency_state_machine \
            evidence_conflict deviation_escalation unverified_key_assumption shared_interface_change \
            destructive_irreversible new_global_gate conflicting_high_confidence \
            conclusion_repeatedly_overturned external_evidence_conflict; do
  if grep -rq "$trig" steps/ references/ SKILL.md 2>/dev/null; then
    ok "触发器 $trig 在文档中一致"
  else
    bad "触发器 $trig 未在步骤文档/引用文档中出现"
  fi
done

echo ""
echo "=== 11.1.2 运行时 fixture ==="

# 1. 完整流程全 L2 履行 → pass
META_FULL='{"thinking_gate_schema_version":1,"completed_steps":["log","1","2","3","4","5","6"],"mode":"full","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t1" "$META_FULL"
write_trace "$TMP/t1" \
  "$(trace_line log L2 L2 sequential-thinking success '["multiple_candidates"]' null)" \
  "$(trace_line plan L2 L2 sequential-thinking success '["multi_module_multi_file"]' null)" \
  "$(trace_line review L2 L2 sequential-thinking success '["evidence_conflict"]' null)" \
  "$(trace_line merge L1 L1 decision_record success '[]' null)" \
  "$(trace_line code L2 L2 sequential-thinking success '["concurrency_state_machine"]' null)" \
  "$(trace_line deepcheck L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line audit L3 L2 sequential-thinking+adversarial success '["destructive_irreversible"]' null)"
if $LINT "$TMP/t1" >/dev/null 2>&1; then ok "T1 全流程 L2/L3 履行→exit0"; else bad "T1 全流程应 exit0"; fi

# 2. 缺 trace（有 metadata、无 trace 文件）：in-scope requires_trace step 全 missing → fail
make_ticket "$TMP/t2" "$META_FULL"
if $LINT "$TMP/t2" >/dev/null 2>&1; then bad "T2 无 trace 应 fail"; else ok "T2 无 trace→fail"; fi

# 3. tier 降级（L2 步骤 default_tier=L2 却写 L1）：fail
make_ticket "$TMP/t3" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t3" "$(trace_line plan L1 L2 decision_record success '[]' null)"
if $LINT "$TMP/t3" >/dev/null 2>&1; then bad "T3 tier 降级应 fail"; else ok "T3 tier 降级→fail"; fi

# 4. L2 未履行（attempted=false 伪造 success）：fail
make_ticket "$TMP/t4" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t4" "$(trace_line plan L2 L2 sequential-thinking success '[]' null)"
python3 - "$TMP/t4" <<'PY'
import sys, json
p = sys.argv[1] + "/.thinking_gate_trace.jsonl"
o = json.loads(open(p, encoding="utf-8").readline())
o["attempted"] = False
open(p, "w", encoding="utf-8").write(json.dumps(o, ensure_ascii=False) + "\n")
PY
if $LINT "$TMP/t4" >/dev/null 2>&1; then bad "T4 L2 attempted=false 应 fail"; else ok "T4 L2 attempted=false→fail"; fi

# 5. degraded 但无 degraded_reason：fail
make_ticket "$TMP/t5" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t5" "$(trace_line plan L2 L2 sequential-thinking degraded '[]' null)"
if $LINT "$TMP/t5" >/dev/null 2>&1; then bad "T5 degraded 无原因应 fail"; else ok "T5 degraded 无原因→fail"; fi
# degraded 且带原因：pass
write_trace "$TMP/t5" "$(trace_line plan L2 L2 sequential-thinking degraded '[]' 'ToolSearch 取 schema 后调用返回超时')"
if $LINT "$TMP/t5" >/dev/null 2>&1; then ok "T5b degraded 有原因→pass"; else bad "T5b degraded 有原因应 pass"; fi

# 6. L3 无对抗机制：fail
make_ticket "$TMP/t6" '{"thinking_gate_schema_version":1,"completed_steps":["6"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t6" "$(trace_line audit L3 L2 sequential-thinking success '["destructive_irreversible"]' null)"
if $LINT "$TMP/t6" >/dev/null 2>&1; then bad "T6 L3 无对抗应 fail"; else ok "T6 L3 无对抗→fail"; fi

# 7. L0/L1 over_invoked：默认 pass（灰度观察），--strict 时 fail
META_L1='{"thinking_gate_schema_version":1,"completed_steps":[],"mode":"full","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t7" "$META_L1"
write_trace "$TMP/t7" "$(trace_line merge L1 L1 sequential-thinking success '[]' null)"
if $LINT "$TMP/t7" --step merge >/dev/null 2>&1; then ok "T7 L1 over_invoked 默认 pass"; else bad "T7 L1 over_invoked 默认应 pass"; fi
if $LINT "$TMP/t7" --step merge --strict >/dev/null 2>&1; then bad "T7 --strict over_invoked 应 fail"; else ok "T7 --strict over_invoked→fail"; fi
# over_invoked 统计计数
$LINT "$TMP/t7" --step merge --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["over_invoked"]==1, (r["over_invoked"],)
' && ok "T7b over_invoked 计数=1" || bad "T7b over_invoked 计数不符"

# 8. 非法触发器（不在 catalog 枚举）：fail
make_ticket "$TMP/t8" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t8" "$(trace_line plan L3 L2 sequential-thinking+adversarial success '["用户觉得很重要"]' null)"
if $LINT "$TMP/t8" >/dev/null 2>&1; then bad "T8 非法触发器应 fail"; else ok "T8 非法触发器→fail"; fi

# 9. 升级到 L2 但 triggers 为空：fail（升级必须有确定性原因）
make_ticket "$TMP/t9" '{"thinking_gate_schema_version":1,"completed_steps":[],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t9" "$(trace_line merge L2 L1 sequential-thinking success '[]' null)"
if $LINT "$TMP/t9" --step merge >/dev/null 2>&1; then bad "T9 升级无触发器应 fail"; else ok "T9 升级无触发器→fail"; fi

# 10. 旧工单无 schema：兼容警告；--require-trace 时失败
make_ticket "$TMP/t10" '{"completed_steps":["1"],"ticket_id":"demo-ticket"}'
if $LINT "$TMP/t10" >/dev/null 2>&1; then ok "T10 旧工单→exit0 legacy-untracked"; else bad "T10 旧工单应 exit0"; fi
if $LINT "$TMP/t10" --require-trace >/dev/null 2>&1; then bad "T10 --require-trace 应 exit1"; else ok "T10 --require-trace→exit1"; fi

# 11. trace 出现 thought 字段 / 敏感词：fail
make_ticket "$TMP/t11a" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
python3 - "$TMP/t11a" <<'PY'
import sys, json
p = sys.argv[1]
base = {"schema_version":1,"ticket_id":"demo-ticket","step":"plan","tier":"L2","default_tier":"L2",
        "triggers":[],"mechanism":"sequential-thinking","attempted":True,"result":"success",
        "degraded_reason":None,"over_invoked":False,"at":"2026-01-01T00:00:00Z"}
o = dict(base, thought="这是完整思维链正文，不得进 trace")
open(p+"/.thinking_gate_trace.jsonl","w",encoding="utf-8").write(json.dumps(o,ensure_ascii=False)+"\n")
PY
if $LINT "$TMP/t11a" >/dev/null 2>&1; then bad "T11a trace 含 thought 应 fail"; else ok "T11a trace 含 thought→fail"; fi
make_ticket "$TMP/t11b" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
python3 - "$TMP/t11b" <<'PY'
import sys, json
p = sys.argv[1]
base = {"schema_version":1,"ticket_id":"demo-ticket","step":"plan","tier":"L2","default_tier":"L2",
        "triggers":[],"mechanism":"sequential-thinking","attempted":True,"result":"success",
        "degraded_reason":None,"over_invoked":False,"at":"2026-01-01T00:00:00Z"}
o = dict(base, degraded_reason="cookie=abc123 调用失败")
open(p+"/.thinking_gate_trace.jsonl","w",encoding="utf-8").write(json.dumps(o,ensure_ascii=False)+"\n")
PY
if $LINT "$TMP/t11b" >/dev/null 2>&1; then bad "T11b trace 含敏感词应 fail"; else ok "T11b trace 含敏感词→fail"; fi

# 12. default_tier 与 catalog 不一致：fail
make_ticket "$TMP/t12" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t12" "$(trace_line plan L2 L0 sequential-thinking success '[]' null)"
if $LINT "$TMP/t12" >/dev/null 2>&1; then bad "T12 default_tier 与 catalog 不一致应 fail"; else ok "T12 default_tier 与 catalog 不一致→fail"; fi

# 13. 坏 JSON 行：fail 且 schema_errors 计数
make_ticket "$TMP/t13" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
printf '%s\n' "$(trace_line plan L2 L2 sequential-thinking success '[]' null)" '{bad json' > "$TMP/t13/.thinking_gate_trace.jsonl"
if $LINT "$TMP/t13" --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["schema_errors"]==1, (r["schema_errors"],)
'; then ok "T13 坏 JSON 行→fail(schema_errors=1)"; else bad "T13 坏 JSON 行计数不符"; fi
if $LINT "$TMP/t13" >/dev/null 2>&1; then bad "T13 坏 JSON 行应 fail"; else ok "T13 坏 JSON 行→exit1"; fi

# 14. --step 过滤：全流程工单只校验指定 step
make_ticket "$TMP/t14" "$META_FULL"
write_trace "$TMP/t14" \
  "$(trace_line log L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line plan L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line review L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line merge L1 L1 decision_record success '[]' null)" \
  "$(trace_line code L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line deepcheck L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line audit L2 L2 sequential-thinking success '[]' null)"
if $LINT "$TMP/t14" --step review --strict --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["total_steps_in_scope"]==1 and r["steps"][0]["step"]=="review" and r["steps"][0]["status"]=="fulfilled",(r["total_steps_in_scope"],)
'; then ok "T14 --step review 只校验 review"; else bad "T14 --step review 过滤不符"; fi
# 故意漏掉 review 的 trace → 应 fail
write_trace "$TMP/t14" "$(trace_line log L2 L2 sequential-thinking success '[]' null)"
if $LINT "$TMP/t14" --step review --strict >/dev/null 2>&1; then bad "T14b --step review 缺 trace 应 fail"; else ok "T14b --step review 缺 trace→fail"; fi

# 15. 重跑追加：最后一条为准（先 L3 后 L2 → 当前状态为 L2 == default L2 合法 → pass）
make_ticket "$TMP/t15" '{"thinking_gate_schema_version":1,"completed_steps":["1"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t15" \
  "$(trace_line plan L3 L2 sequential-thinking+adversarial success '["destructive_irreversible"]' null)" \
  "$(trace_line plan L2 L2 sequential-thinking success '[]' null)"
if $LINT "$TMP/t15" >/dev/null 2>&1; then ok "T15 最后一条为准（L2 合法）→pass"; else bad "T15 最后一条为准（L2 合法）应 pass"; fi

# 16. 样本工单形状回归（L2/L3 全履行 + 无 over_invoked + 无 missing）
make_ticket "$TMP/t16" "$META_FULL"
write_trace "$TMP/t16" \
  "$(trace_line log L2 L2 sequential-thinking success '["multiple_candidates"]' null)" \
  "$(trace_line plan L2 L2 sequential-thinking success '["multi_module_multi_file"]' null)" \
  "$(trace_line review L2 L2 sequential-thinking success '["evidence_conflict"]' null)" \
  "$(trace_line merge L1 L1 decision_record success '[]' null)" \
  "$(trace_line code L2 L2 sequential-thinking success '["concurrency_state_machine"]' null)" \
  "$(trace_line deepcheck L2 L2 sequential-thinking success '[]' null)" \
  "$(trace_line audit L3 L2 sequential-thinking+adversarial success '["destructive_irreversible"]' null)"
T16_JSON="$($LINT "$TMP/t16" --json)"
if echo "$T16_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["missing"]==0 and r["schema_errors"]==0 and r["sensitive_data"]==0, (r["missing"],r["schema_errors"],r["sensitive_data"])
assert r["over_invoked"]==0, (r["over_invoked"],)
assert all(s["status"]=="fulfilled" for s in r["steps"]), [s["step"] for s in r["steps"] if s["status"]!="fulfilled"]
print("ok")
'; then
  ok "S1 样本工单全履行 + 无 over_invoked"
else
  bad "S1 样本工单形状不符"
fi
if $LINT "$TMP/t16" >/dev/null 2>&1; then ok "S2 样本工单 exit0"; else bad "S2 样本工单应 exit0"; fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
