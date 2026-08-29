#!/usr/bin/env bash
# workflow gate（工作流硬门禁）demo 模拟验收
#
# 用 demo 工程（C 计算器）构造 6 个模拟工单 fixture 到 demo/.icode_output/.workflow_sim/，
# 按机器真源 mcp/workflow-gate/gates.json + tools/lint_workflow_contract.py 校验：
#   S1 纯计算单文件   → 无语义决策/身份变化，fast 允许
#   S2 策略不唯一未确认 → plan/code 阻断（语义决策门禁）
#   S3 用户确认 + 身份变化 9 维完整 → 允许 code/deploy
#   S4 身份变化影响清单不完整 → code/deploy 阻断
#   S5 fast 命中异步/恢复风险未升级 → 违规（fast_risk 门禁）
#   S6 重大增量未回流 → patch/merge 阻断（需求增量强制升级）
#
# 同时验证 demo 工程（C 计算器）可构建（真实基线，证明模拟对象可运行）。
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SIM="demo/.icode_output/.workflow_sim"
LINT="python3 tools/lint_workflow_contract.py"

# 0) demo 工程真实基线：可构建、可运行（make clean && make && 基本运算）
if (cd demo && make clean >/dev/null 2>&1 && make >/dev/null 2>&1 && \
    echo "calc 1 2 +" | ./calc_demo >/dev/null 2>&1); then
  ok "demo 工程可构建可运行（C 计算器基线）"
else
  bad "demo 工程构建/运行失败（基线不可用）"
fi

# 1) 生成/幂等刷新 fixture（内嵌生成器）
python3 - "$SIM" <<'PY' >/dev/null || { echo "  ❌ fixture 生成失败"; exit 1; }
import json, pathlib, sys
BASE = pathlib.Path(sys.argv[1])

def write(name, metadata):
    d = BASE / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".ico_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

# S1 纯计算单文件、无语义决策 → full scan pass，fast 允许
write("s1_pure_compute", {
    "ticket_id": "DEMO-WF-S1", "workflow_gate_schema_version": 1, "mode": "fast",
    "semantic_decisions": [],
    "risk_profile": {"requested_mode": "fast", "effective_mode": "fast",
                     "triggers": [], "risk_flags": {"cross_component": False}, "override": False},
    "desc": "calc_power 溢出检查单文件纯计算，无外部状态变化"
})
# S2 策略不唯一未确认 → plan/code 阻断
write("s2_unresolved", {
    "ticket_id": "DEMO-WF-S2", "workflow_gate_schema_version": 1,
    "semantic_decisions": [
        {"dimension": "除零失败语义", "alternatives": ["返回错误码", "饱和到0"],
         "selected": None, "user_confirmed": False, "status": "open"}
    ],
    "desc": "除法错误码语义存在两个外部行为不同选项，用户未确认"
})
# S3 用户确认 + 身份变化 9 维完整 → 允许 code/deploy
write("s3_identity_full", {
    "ticket_id": "DEMO-WF-S3", "workflow_gate_schema_version": 1,
    "semantic_decisions": [
        {"dimension": "运算结果身份", "alternatives": ["保留原值", "收敛为新值"],
         "selected": "收敛为新值", "evidence": "user-confirmed", "user_confirmed": True, "status": "resolved"}
    ],
    "impact_contract": {
        "identity_change": True, "completeness": "complete",
        "authoritative_writer": {"status": "affected", "evidence": "calc.c:42"},
        "persistent_references": {"status": "affected", "evidence": "test-ref-mig"},
        "derived_metadata": {"status": "affected", "evidence": "test-recalc"},
        "runtime_indexes": {"status": "affected", "evidence": "test-prune"},
        "queries_and_selectors": {"status": "affected", "evidence": "test-select"},
        "async_links": {"status": "not-affected", "evidence": "no-observer"},
        "recovery_paths": {"status": "affected", "evidence": "test-restart"},
        "external_projections": {"status": "not-affected", "evidence": "protocol-preserves"},
        "rollback_and_failure": {"status": "affected", "evidence": "test-rollback"}
    },
    "acceptance_contract": {"requires_lifecycle": True, "matrix": [
        {"scenario": "收敛", "phase": p, "consumer": c, "expected": "only-surviving",
         "evidence": "t", "status": "passed"}
        for p in ("immediate", "converged", "restart", "replay")
        for c in ("direct_query", "aggregate_selector", "persistent_reference", "external_projection")
    ]}
})
# S4 身份变化影响清单不完整 → code/deploy 阻断
write("s4_identity_incomplete", {
    "ticket_id": "DEMO-WF-S4", "workflow_gate_schema_version": 1,
    "impact_contract": {"identity_change": True, "completeness": "incomplete",
                        "authoritative_writer": {"status": "affected", "evidence": "calc.h:10"}},
    "desc": "去重运算实体但聚合选择器/恢复路径未评估"
})
# S5 fast 命中异步/恢复风险未升级 → 违规
write("s5_fast_risk", {
    "ticket_id": "DEMO-WF-S5", "workflow_gate_schema_version": 1, "mode": "fast",
    "risk_profile": {"requested_mode": "fast", "effective_mode": "fast",
                     "triggers": ["async_observer_or_cache", "restart_replay_semantics"],
                     "risk_flags": {"async_observer_or_cache": True, "restart_replay_semantics": True},
                     "override": False},
    "desc": "改动涉及缓存注册表与重启恢复语义，fast 未升级"
})
# S6 重大增量未回流 → patch/merge 阻断
write("s6_major_delta", {
    "ticket_id": "DEMO-WF-S6", "workflow_gate_schema_version": 1,
    "requirement_deltas": [
        {"id": "delta-001", "summary": "新增多实体传递收敛语义", "severity": "major",
         "needs_replan": True, "status": "open"}
    ],
    "desc": "轻量修补中新增重大语义"
})
PY
ok "fixture 生成（demo/.icode_output/.workflow_sim/s1..s6）"

# 2) S1 纯计算 fast 允许
for st in fast plan code; do
  if $LINT "$SIM/s1_pure_compute" --step "$st" --json >/dev/null 2>&1; then
    ok "S1 --step $st 允许（纯计算）"
  else
    bad "S1 --step $st 不应阻断"
  fi
done

# 3) S2 策略不唯一未确认 → plan/code 阻断
for st in plan code patch; do
  if $LINT "$SIM/s2_unresolved" --step "$st" --json >/dev/null 2>&1; then
    bad "S2 --step $st 应阻断（未解决语义决策）"
  else
    ok "S2 --step $st 阻断（未解决语义决策）"
  fi
done

# 4) S3 用户确认 + 身份变化 9 维完整 → code/deploy 允许
for st in code deploy audit-verified; do
  if $LINT "$SIM/s3_identity_full" --step "$st" --json >/dev/null 2>&1; then
    ok "S3 --step $st 允许（合同完整）"
  else
    bad "S3 --step $st 不应阻断"
  fi
done

# 5) S4 身份变化影响清单不完整 → code/deploy 阻断
for st in code deploy audit-verified; do
  if $LINT "$SIM/s4_identity_incomplete" --step "$st" --json >/dev/null 2>&1; then
    bad "S4 --step $st 应阻断（身份变化影响清单不完整）"
  else
    ok "S4 --step $st 阻断"
  fi
done

# 6) S5 fast 命中风险未升级 → 违规
if $LINT "$SIM/s5_fast_risk" --step fast --json >/dev/null 2>&1; then
  bad "S5 fast 命中风险未升级应判违规"
else
  ok "S5 fast 命中风险未升级 → 违规"
fi

# 7) S6 重大增量未回流 → patch/merge 阻断
for st in patch merge; do
  if $LINT "$SIM/s6_major_delta" --step "$st" --json >/dev/null 2>&1; then
    bad "S6 --step $st 应阻断（重大增量未回流）"
  else
    ok "S6 --step $st 阻断（重大增量未回流）"
  fi
done

# 8) 敏感数据：合同字段无 api_key/cookie/password 等
python3 - "$SIM" <<'PY' && ok "合同字段无敏感数据" || bad "合同字段含敏感数据"
import sys, pathlib
bad_kw = ["api_key", "apikey", "cookie", "password", "passwd", "secret", "authorization", "bearer"]
base = pathlib.Path(sys.argv[1])
for md in base.glob("s*/.ico_metadata.json"):
    low = md.read_text().lower()
    for kw in bad_kw:
        assert kw not in low, f"{md} 含 {kw}"
PY

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
