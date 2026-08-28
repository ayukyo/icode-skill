#!/usr/bin/env bash
# 契约测试：cheap-research 执行门（gate）机器真源 + 运行时校验器
#
# 覆盖 ICODE_CHEAP_RESEARCH_EXECUTION_GATE_OPTIMIZATION.md §12：
#   12.1 静态契约（SKILL/thinking_core/mcp_per_step 引用 trace 与 catalog；正式产物不写 MCP；
#        lint 不再搜索「MCP 调用记录」；fast 文档无 Fixed/dedup 矛盾；gate ID 一致）
#   12.2 运行时 fixture（评论数/字节数/函数数阈值、fast 跳过、audit 双 gate、
#        patch listen、缓存命中、degraded-without-attempt、旧工单兼容、敏感数据）
#   12.3 样本工单形状回归（eligible coverage = 100%）
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LINT="python3 tools/lint_mcp_coverage.py"

# ---- 工具函数 ----
make_ticket() {  # $1=dir  $2=metadata_json
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}
write_trace() {  # $1=dir  $2..=jsonl 行
  local dir="$1"; shift
  : > "$dir/.mcp_gate_trace.jsonl"
  for line in "$@"; do
    printf '%s\n' "$line" >> "$dir/.mcp_gate_trace.jsonl"
  done
}
# 生成一条合法 trace 行（evidence 用普通双引号 JSON，外层传参用单引号）
trace_line() {  # $1=gate_id $2=tool $3=step $4=eligible $5=decision $6=evidence_json
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
import json, sys, datetime
gid, tool, step, elig, dec, ev = sys.argv[1:]
print(json.dumps({
  "schema_version": 1, "workflow_version": "2.x", "ticket_id": "demo-ticket",
  "step": step, "gate_id": gid, "tool": tool,
  "eligible": elig == "true", "evidence": json.loads(ev),
  "decision": dec, "attempted": dec in ("called", "degraded_after_attempt"),
  "result": "success", "cache_key": None, "source_files": [],
  "error_class": None, "at": datetime.datetime.now(datetime.timezone.utc).isoformat()
}, ensure_ascii=False))
PY
}

echo "=== 12.1 静态契约 ==="
check_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3 ($1 缺: $2)"; fi
}
check_not_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then bad "$3 ($1 意外含: $2)"; else ok "$3"; fi
}

check_contains SKILL.md "\.mcp_gate_trace\.jsonl" "SKILL.md 引用 .mcp_gate_trace.jsonl"
check_contains SKILL.md "gates\.json" "SKILL.md 引用 gate catalog"
check_contains references/thinking_core.md "\.mcp_gate_trace\.jsonl" "thinking_core.md 引用 .mcp_gate_trace.jsonl"
check_contains references/thinking_core.md "gates\.json" "thinking_core.md 引用 gate catalog"
check_contains references/mcp_per_step.md "\.mcp_gate_trace\.jsonl" "mcp_per_step.md 引用 .mcp_gate_trace.jsonl"
check_contains references/mcp_per_step.md "gates\.json" "mcp_per_step.md 引用 gate catalog"
check_contains SKILL.md "产物文件不记录 MCP 调用信息" "SKILL.md 正式产物不写 MCP 调用信息"
check_contains references/mcp_per_step.md "产物文件不记录 MCP 调用信息" "mcp_per_step.md 正式产物不写 MCP 调用信息"
check_not_contains tools/lint_mcp_coverage.py "MCP 调用记录" "lint 不再搜索「MCP 调用记录」章节"
# fast 文档矛盾：fast 只跑 Reverse，deepcheck Fixed/dedup 记为 skipped_stage_not_reached
if grep -q "deepcheck（Fixed 预扫 scan_patterns + dedup extract）" steps/fast.md 2>/dev/null; then
  bad "fast.md 仍宣传 fast deepcheck 会执行 Fixed/dedup（与 Reverse 后终止矛盾）"
else
  ok "fast.md 不再宣传 fast deepcheck 执行 Fixed/dedup"
fi
# gate ID 拼写一致：catalog 与步骤文档
for gid in log.comments_extract log.long_log_summary review.dedup review.result_summary \
           merge.cross_round_summary deepcheck.fixed_scan deepcheck.dedup \
           audit.repo_facts audit.plan_diff patch.context_summary patch.listen_log_summary; do
  if grep -rq "$gid" steps/ references/ SKILL.md 2>/dev/null; then
    ok "gate ID $gid 在文档中一致"
  else
    bad "gate ID $gid 未在步骤文档/引用文档中出现"
  fi
done

echo ""
echo "=== 12.2 运行时 fixture ==="

# 1. TB 评论 3 条：log.comments_extract 合法 skip
META_LOG='{"mcp_gate_schema_version":1,"completed_steps":["log"],"mode":"full","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t1" "$META_LOG"
write_trace "$TMP/t1" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')"
if $LINT "$TMP/t1" >/dev/null 2>&1; then ok "T1 评论3条→合法skip exit0"; else bad "T1 评论3条应 exit0"; fi

# 2. TB 评论 8 条：缺 extract 记录时 strict lint 失败
make_ticket "$TMP/t2" "$META_LOG"
write_trace "$TMP/t2" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')"
if $LINT "$TMP/t2" >/dev/null 2>&1; then bad "T2 评论8条缺 extract 应 fail"; else ok "T2 评论8条缺 extract→fail"; fi

# 3. 候选日志 8191 bytes：long-log gate 合法 skip
make_ticket "$TMP/t3" "$META_LOG"
write_trace "$TMP/t3" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":8191,"threshold":8192,"cheap_available":true}')"
if $LINT "$TMP/t3" >/dev/null 2>&1; then ok "T3 8191B→合法skip exit0"; else bad "T3 8191B 应 exit0"; fi

# 4. 候选日志 8192 bytes：缺 summarize 失败；有 called/cache_hit 通过
make_ticket "$TMP/t4a" "$META_LOG"
write_trace "$TMP/t4a" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')"
if $LINT "$TMP/t4a" >/dev/null 2>&1; then bad "T4a 8192B缺 summarize 应 fail"; else ok "T4a 8192B缺 summarize→fail"; fi
make_ticket "$TMP/t4b" "$META_LOG"
write_trace "$TMP/t4b" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" \
  "$(trace_line log.long_log_summary summarize log true called '{"candidate_text_bytes":8192,"threshold":8192,"cheap_available":true}')"
if $LINT "$TMP/t4b" >/dev/null 2>&1; then ok "T4b 8192B有called→pass"; else bad "T4b 8192B 有 called 应 pass"; fi

# 5. 函数 49：review.dedup 合法 skip
META_REV='{"mcp_gate_schema_version":1,"completed_steps":["2"],"mode":"full","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t5" "$META_REV"
write_trace "$TMP/t5" \
  "$(trace_line review.dedup extract review false skipped_not_eligible '{"function_count":49,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"02_review.md","review_rounds":1}')"
if $LINT "$TMP/t5" >/dev/null 2>&1; then ok "T5 函数49→合法skip exit0"; else bad "T5 函数49 应 exit0"; fi

# 6. 函数 50：review.dedup 必须 fulfilled
make_ticket "$TMP/t6a" "$META_REV"
write_trace "$TMP/t6a" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"02_review.md","review_rounds":1}')"
if $LINT "$TMP/t6a" >/dev/null 2>&1; then bad "T6a 函数50缺 dedup 应 fail"; else ok "T6a 函数50缺 dedup→fail"; fi
make_ticket "$TMP/t6b" "$META_REV"
write_trace "$TMP/t6b" \
  "$(trace_line review.dedup extract review true called '{"function_count":50,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')"
if $LINT "$TMP/t6b" >/dev/null 2>&1; then ok "T6b 函数50有 called→pass"; else bad "T6b 函数50 有 called 应 pass"; fi

# 7. fast Reverse-only：deepcheck Fixed/dedup 必须 skipped_stage_not_reached
META_FAST_DC='{"mcp_gate_schema_version":1,"completed_steps":["5"],"mode":"fast","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t7a" "$META_FAST_DC"
write_trace "$TMP/t7a" \
  "$(trace_line deepcheck.fixed_scan scan_patterns deepcheck false skipped_stage_not_reached '{"mode":"fast","phase":"reverse","function_points_count":0}')"
if $LINT "$TMP/t7a" >/dev/null 2>&1; then bad "T7a fast 缺 dedup 跳过记录应 fail"; else ok "T7a fast 缺 dedup 跳过记录→fail"; fi
make_ticket "$TMP/t7b" "$META_FAST_DC"
write_trace "$TMP/t7b" \
  "$(trace_line deepcheck.fixed_scan scan_patterns deepcheck false skipped_stage_not_reached '{"mode":"fast","phase":"reverse","function_points_count":0}')" \
  "$(trace_line deepcheck.dedup extract deepcheck false skipped_stage_not_reached '{"mode":"fast","phase":"reverse","function_count":0,"threshold":50}')"
if $LINT "$TMP/t7b" >/dev/null 2>&1; then ok "T7b fast 双 gate 跳过记录→pass"; else bad "T7b fast 双 gate 跳过记录应 pass"; fi

# 8. audit 两个 gate 任缺一个均失败
META_AUDIT='{"mcp_gate_schema_version":1,"completed_steps":["6"],"mode":"full","ticket_id":"demo-ticket"}'
make_ticket "$TMP/t8a" "$META_AUDIT"
write_trace "$TMP/t8a" \
  "$(trace_line audit.repo_facts propose_repo_facts audit true called '{"affected_repo_roots":["/repo"]}')"
if $LINT "$TMP/t8a" >/dev/null 2>&1; then bad "T8a audit 缺 plan_diff 应 fail"; else ok "T8a audit 缺 plan_diff→fail"; fi
make_ticket "$TMP/t8b" "$META_AUDIT"
write_trace "$TMP/t8b" \
  "$(trace_line audit.repo_facts propose_repo_facts audit true called '{"affected_repo_roots":["/repo"]}')" \
  "$(trace_line audit.plan_diff diff_summary audit true called '{"plan_source":"03_plan_final.md","code_sources":["src/main.c"]}')"
if $LINT "$TMP/t8b" >/dev/null 2>&1; then ok "T8b audit 双 gate→pass"; else bad "T8b audit 双 gate 应 pass"; fi

# 9. patch listen 增量日志超过阈值：必须 summarize
META_PATCH='{"mcp_gate_schema_version":1,"completed_steps":[],"mode":"full","patch_scoped":true,"ticket_id":"demo-ticket"}'
make_ticket "$TMP/t9a" "$META_PATCH"
if $LINT "$TMP/t9a" >/dev/null 2>&1; then bad "T9a patch 全缺应 fail"; else ok "T9a patch 无记录→fail"; fi
make_ticket "$TMP/t9b" "$META_PATCH"
write_trace "$TMP/t9b" \
  "$(trace_line patch.context_summary summarize patch false skipped_not_eligible '{"cross_session":false,"candidate_text_bytes":0,"threshold":8192}')" \
  "$(trace_line patch.listen_log_summary summarize patch true called '{"listen_mode":true,"incremental_bytes":9000,"threshold":8192}')"
if $LINT "$TMP/t9b" >/dev/null 2>&1; then ok "T9b patch listen 有 summarize→pass"; else bad "T9b patch listen 有 summarize 应 pass"; fi

# 10. 有效缓存命中：不重复调用且 gate 通过（decision=cache_hit）
make_ticket "$TMP/t10" "$META_REV"
write_trace "$TMP/t10" \
  "$(trace_line review.dedup extract review true cache_hit '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true cache_hit '{"result_source":"review_round_1.json","review_rounds":1}')"
if $LINT "$TMP/t10" >/dev/null 2>&1; then ok "T10 缓存命中→pass（无需重复调用）"; else bad "T10 缓存命中应 pass"; fi

# 11. degraded_after_attempt 但 attempted=false：失败
make_ticket "$TMP/t11" "$META_AUDIT"
write_trace "$TMP/t11" \
  "$(trace_line audit.repo_facts propose_repo_facts audit true degraded_after_attempt '{"affected_repo_roots":["/repo"]}')" \
  "$(trace_line audit.plan_diff diff_summary audit true called '{"plan_source":"03_plan_final.md","code_sources":["src/main.c"]}')"
# 手工改 attempted=false
python3 - "$TMP/t11" <<'PY'
import sys, json
p = sys.argv[1] + "/.mcp_gate_trace.jsonl"
lines = open(p, encoding="utf-8").read().splitlines()
out = []
for ln in lines:
    o = json.loads(ln)
    if o.get("gate_id") == "audit.repo_facts":
        o["attempted"] = False
        o["result"] = "error"
    out.append(json.dumps(o, ensure_ascii=False))
open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
if $LINT "$TMP/t11" >/dev/null 2>&1; then bad "T11 degraded-but-not-attempted 应 fail"; else ok "T11 degraded 但 attempted=false→fail"; fi

# 11b. eligible=true 但 decision=skipped_not_eligible（非法跳过）：失败 + 报告计数正确
make_ticket "$TMP/t11b" "$META_REV"
write_trace "$TMP/t11b" \
  "$(trace_line review.dedup extract review true skipped_not_eligible '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')"
if $LINT "$TMP/t11b" >/dev/null 2>&1; then bad "T11b eligible=true 非法跳过应 fail"; else ok "T11b eligible=true 非法跳过→fail"; fi
$LINT "$TMP/t11b" --json | python3 -c '
import json, sys
r = json.load(sys.stdin)
want = (2, 1, 1, 0.5, 0)
got = (r["eligible"], r["fulfilled"], r["invalid_skip"], r["coverage"], r["schema_errors"])
assert got == want, ("报告计数不符", got, want)
' && ok "T11b 报告计数 eligible=2 fulfilled=1 invalid_skip=1 coverage=0.5 schema_errors=0" \
   || bad "T11b 报告计数不符（invalid_skip 必须进 coverage 分母）"

# 12. 旧工单无 schema：兼容警告；--require-trace 时失败
make_ticket "$TMP/t12" '{"completed_steps":["2"],"ticket_id":"demo-ticket"}'
if $LINT "$TMP/t12" >/dev/null 2>&1; then ok "T12 旧工单→exit0 legacy-untracked"; else bad "T12 旧工单应 exit0"; fi
if $LINT "$TMP/t12" --require-trace >/dev/null 2>&1; then bad "T12 --require-trace 应 exit1"; else ok "T12 --require-trace→exit1"; fi

# 13. trace 中出现 api_key / 大段正文：失败
make_ticket "$TMP/t13a" "$META_LOG"
write_trace "$TMP/t13a" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true,"api_key":"sk-xxxx"}')" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')"
if $LINT "$TMP/t13a" >/dev/null 2>&1; then bad "T13a trace 含 api_key 应 fail"; else ok "T13a trace 含 api_key→fail"; fi
make_ticket "$TMP/t13b" "$META_LOG"
python3 - "$TMP/t13b" <<'PY'
import sys, json
p = sys.argv[1]
body = "x" * 5000
meta = {"mcp_gate_schema_version":1,"completed_steps":["log"],"mode":"full","ticket_id":"demo-ticket"}
open(p+"/.ico_metadata.json","w",encoding="utf-8").write(json.dumps(meta))
base = {"schema_version":1,"ticket_id":"demo-ticket","step":"log","eligible":False,"attempted":False,"result":"success","cache_key":None,"source_files":[],"error_class":None,"at":"2026-01-01T00:00:00Z"}
rows = [
  dict(base, gate_id="log.comments_extract", tool="extract", evidence={"tb_comments_count":3,"threshold":8}, decision="skipped_not_eligible"),
  dict(base, gate_id="log.long_log_summary", tool="summarize", evidence={"candidate_text_bytes":len(body.encode("utf-8")),"threshold":8192,"snippet":body}, decision="skipped_not_eligible"),
]
open(p+"/.mcp_gate_trace.jsonl","w",encoding="utf-8").write("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n")
PY
if $LINT "$TMP/t13b" >/dev/null 2>&1; then bad "T13b trace 含大段正文应 fail"; else ok "T13b trace 含大段正文→fail"; fi

# 14. trace 行 step/tool 与 gates.json 定义不一致：失败
make_ticket "$TMP/t14" "$META_REV"
write_trace "$TMP/t14" \
  "$(trace_line review.dedup extract log true called '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')"
if $LINT "$TMP/t14" >/dev/null 2>&1; then bad "T14 trace step 与 catalog 不一致应 fail"; else ok "T14 trace step 与 catalog 不一致→fail"; fi

# 15. trace 单行超长（> MAX_LINE_CHARS）：失败并带行号
make_ticket "$TMP/t15" "$META_LOG"
python3 - "$TMP/t15" <<'PY'
import sys, json
p = sys.argv[1]
meta = {"mcp_gate_schema_version":1,"completed_steps":["log"],"mode":"full","ticket_id":"demo-ticket"}
open(p+"/.ico_metadata.json","w",encoding="utf-8").write(json.dumps(meta))
base = {"schema_version":1,"ticket_id":"demo-ticket","step":"log","eligible":False,"attempted":False,"result":"success","cache_key":None,"source_files":[],"error_class":None,"at":"2026-01-01T00:00:00Z"}
rows = [
  dict(base, gate_id="log.comments_extract", tool="extract", evidence={"tb_comments_count":3,"threshold":8}, decision="skipped_not_eligible"),
  dict(base, gate_id="log.long_log_summary", tool="summarize", evidence={"candidate_text_bytes":3,"threshold":8192,"junk":"x"*5000}, decision="skipped_not_eligible"),
]
open(p+"/.mcp_gate_trace.jsonl","w",encoding="utf-8").write("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n")
PY
if $LINT "$TMP/t15" >/dev/null 2>&1; then bad "T15 trace 单行超长应 fail"; else ok "T15 trace 单行超长→fail"; fi

# 16. 空 trace（有 metadata、无 trace 行）：in-scope gate 全 missing → fail
make_ticket "$TMP/t16" "$META_LOG"
: > "$TMP/t16/.mcp_gate_trace.jsonl"
if $LINT "$TMP/t16" >/dev/null 2>&1; then bad "T16 空 trace 应 fail"; else ok "T16 空 trace→fail"; fi

# 17. 同 gate 重跑追加：最后一条为准（先 skipped 后 called → fulfilled）
make_ticket "$TMP/t17" "$META_REV"
write_trace "$TMP/t17" \
  "$(trace_line review.dedup extract review true called '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')" \
  "$(trace_line review.dedup extract review false skipped_not_eligible '{"function_count":12,"threshold":50,"rg_available":true,"cheap_available":true}')"
# 最后一条 review.dedup 为 eligible=false；此时 eligible 应只剩 result_summary=1
if $LINT "$TMP/t17" --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["eligible"]==1 and r["fulfilled"]==1 and r["skipped_not_eligible"]==1 and r["coverage"]==1.0,(r["eligible"],r["fulfilled"],r["skipped_not_eligible"],r["coverage"])
'; then ok "T17 重跑追加以最后一条为准"; else bad "T17 重跑追加语义不符"; fi

# 18. trace 内坏 JSON 行：fail 且 schema_errors 计数
make_ticket "$TMP/t18" "$META_LOG"
printf '%s\n' "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" '{bad json' "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')" > "$TMP/t18/.mcp_gate_trace.jsonl"
if $LINT "$TMP/t18" --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["schema_errors"]==1, (r["schema_errors"],)
'; then ok "T18 坏 JSON 行→fail(schema_errors=1)"; else bad "T18 坏 JSON 行计数不符"; fi
if $LINT "$TMP/t18" >/dev/null 2>&1; then bad "T18 坏 JSON 行应 fail"; else ok "T18 坏 JSON 行→exit1"; fi

# 19. --step 过滤：全流程工单只校验指定 step 的 gate（工作流转换前 --step review --strict 用）
make_ticket "$TMP/t19" '{"mcp_gate_schema_version":1,"completed_steps":["log","1","2","3","4","5","6"],"mode":"full","ticket_id":"demo-ticket"}'
write_trace "$TMP/t19" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')" \
  "$(trace_line review.dedup extract review true called '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')" \
  "$(trace_line merge.cross_round_summary summarize merge true called '{"review_rounds":2,"threshold":2}')" \
  "$(trace_line deepcheck.fixed_scan scan_patterns deepcheck true called '{"function_points_count":3}')" \
  "$(trace_line deepcheck.dedup extract deepcheck true called '{"function_count":930,"threshold":50}')" \
  "$(trace_line audit.repo_facts propose_repo_facts audit true called '{"affected_repo_roots":["/repo"]}')" \
  "$(trace_line audit.plan_diff diff_summary audit true called '{"plan_source":"03_plan_final.md","code_sources":["src/main.c"]}')"
if $LINT "$TMP/t19" --step review --strict --json | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["total_gates_in_scope"]==2 and r["eligible"]==2 and r["fulfilled"]==2,(r["total_gates_in_scope"],r["eligible"],r["fulfilled"])
'; then ok "T19 --step review 只校验 review 两 gate"; else bad "T19 --step review 过滤不符"; fi
# 故意漏掉 review.result_summary 再过滤 → 应 fail
write_trace "$TMP/t19" \
  "$(trace_line review.dedup extract review true called '{"function_count":120,"threshold":50,"rg_available":true,"cheap_available":true}')"
if $LINT "$TMP/t19" --step review --strict >/dev/null 2>&1; then bad "T19b --step review 缺 result_summary 应 fail"; else ok "T19b --step review 缺 result_summary→fail"; fi

echo ""
echo "=== 12.3 样本工单形状回归（eligible coverage = 100%） ==="
SAMPLE="$TMP/sample"
make_ticket "$SAMPLE" '{"mcp_gate_schema_version":1,"completed_steps":["log","1","2","3","4","5","6"],"mode":"fast","patch_scoped":true,"ticket_id":"demo-ticket"}'
write_trace "$SAMPLE" \
  "$(trace_line log.comments_extract extract log false skipped_not_eligible '{"tb_comments_count":3,"threshold":8,"cheap_available":true}')" \
  "$(trace_line log.long_log_summary summarize log false skipped_not_eligible '{"candidate_text_bytes":2048,"threshold":8192,"cheap_available":true}')" \
  "$(trace_line review.dedup extract review true called '{"affected_repo_roots":["/repo"],"function_count":930,"threshold":50,"rg_available":true,"cheap_available":true}')" \
  "$(trace_line review.result_summary summarize review true called '{"result_source":"review_round_1.json","review_rounds":1}')" \
  "$(trace_line merge.cross_round_summary summarize merge false skipped_not_eligible '{"review_rounds":1,"threshold":2}')" \
  "$(trace_line deepcheck.fixed_scan scan_patterns deepcheck false skipped_stage_not_reached '{"mode":"fast","phase":"reverse","function_points_count":0}')" \
  "$(trace_line deepcheck.dedup extract deepcheck false skipped_stage_not_reached '{"mode":"fast","phase":"reverse","function_count":0,"threshold":50}')" \
  "$(trace_line audit.repo_facts propose_repo_facts audit true called '{"affected_repo_roots":["/repo"]}')" \
  "$(trace_line audit.plan_diff diff_summary audit true called '{"plan_source":"03_plan_final.md","code_sources":["src/main.c"]}')" \
  "$(trace_line patch.context_summary summarize patch false skipped_not_eligible '{"cross_session":false,"candidate_text_bytes":0,"threshold":8192}')" \
  "$(trace_line patch.listen_log_summary summarize patch true called '{"listen_mode":true,"incremental_bytes":12288,"threshold":8192}')"
SAMPLE_JSON="$($LINT "$SAMPLE" --json)"
# 期望：eligible = review.dedup + review.result_summary + audit.repo_facts + audit.plan_diff + patch.listen_log_summary = 5，全 fulfilled
if echo "$SAMPLE_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
assert r["eligible"]==5, ("eligible", r["eligible"])
assert r["fulfilled"]==5, ("fulfilled", r["fulfilled"])
assert r["invalid_skip"]==0 and r["missing_gate"]==0 and r["schema_errors"]==0 and r["sensitive_data"]==0
assert r["coverage"]==1.0, ("coverage", r["coverage"])
print("ok")
'; then
  ok "S1 样本工单 eligible=5 fulfilled=5 coverage=100%"
else
  bad "S1 样本工单 coverage 未达 100%"
fi
if $LINT "$SAMPLE" >/dev/null 2>&1; then ok "S2 样本工单 exit0"; else bad "S2 样本工单应 exit0"; fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
