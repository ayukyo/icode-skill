#!/usr/bin/env bash
# reasoning gate（分级思考）demo 模拟验收 —— 对应优化文档 §11.2 行为样例
#
# 用 demo 工程（C 计算器）构造 6 个模拟工单 fixture 到 demo/.icode_output/.gate_sim/，
# 按机器真源 mcp/reasoning-gate/gates.json + tools/lint_thinking_gate.py 校验：
#   S1 status      → L0：零 sequential-thinking
#   S2 readme      → L1：决策记录，无思维链日志
#   S3 单文件修复  → L1：已有 RED/GREEN，仅 merge 决策
#   S4 多线程竞态  → L2：sequential-thinking（含反证/验证计划）
#   S5 跨模块重构  → L3：sequential-thinking + 独立对抗
#   S6 fast 升级   → L2：命中跨模块状态机不因 fast 跳过
#
# fixture 生成逻辑内嵌（demo/.icode_output 被 gitignore，必须自包含、可幂等重建）。
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SIM="demo/.icode_output/.gate_sim"
LINT="python3 tools/lint_thinking_gate.py"

# 1) 生成/幂等刷新 fixture（内嵌生成器）
python3 - "$SIM" <<'PY' >/dev/null || { echo "  ❌ fixture 生成失败"; exit 1; }
import json, datetime, pathlib, sys
BASE = pathlib.Path(sys.argv[1])
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()

def tline(ticket, step, tier, default_tier, triggers, mechanism, attempted, result,
          degraded_reason=None, over_invoked=False):
    return {"schema_version": 1, "ticket_id": ticket, "step": step, "tier": tier,
            "default_tier": default_tier, "triggers": triggers, "mechanism": mechanism,
            "attempted": attempted, "result": result, "degraded_reason": degraded_reason,
            "over_invoked": over_invoked, "at": NOW}

def anchors(ticket, records):
    return {"schema_version": 1, "ticket_id": ticket, "decision_anchors": records}

def write_ticket(name, metadata, trace, anchor=None):
    d = BASE / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".ico_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if trace:
        (d / ".thinking_gate_trace.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in trace), encoding="utf-8")
    if anchor is not None:
        (d / ".decision_anchors.json").write_text(json.dumps(anchor, ensure_ascii=False, indent=2), encoding="utf-8")

# S1 status —— L0：零 sequential-thinking
write_ticket("s1_status_l0",
    {"ticket_id": "DEMO-GATE-S1", "thinking_gate_schema_version": 1, "completed_steps": [], "desc": "status 只读查询当前工单"}, [])
# S2 readme —— L1：决策记录，无思维链
write_ticket("s2_readme_l1",
    {"ticket_id": "DEMO-GATE-S2", "thinking_gate_schema_version": 1, "completed_steps": ["0","1","2","3","4","5","6"], "desc": "/icode readme 汇总已验证产物（L1 决策记录）"},
    [tline("DEMO-GATE-S2","init","L1","L1",[],"decision_record",False,"success"),
     tline("DEMO-GATE-S2","plan","L2","L2",[],"sequential-thinking",True,"success"),
     tline("DEMO-GATE-S2","review","L2","L2",[],"sequential-thinking",True,"success"),
     tline("DEMO-GATE-S2","merge","L1","L1",[],"decision_record",False,"success"),
     tline("DEMO-GATE-S2","code","L2","L2",[],"sequential-thinking",True,"success"),
     tline("DEMO-GATE-S2","deepcheck","L2","L2",[],"sequential-thinking",True,"success"),
     tline("DEMO-GATE-S2","audit","L2","L2",[],"sequential-thinking",True,"success"),
     tline("DEMO-GATE-S2","readme","L1","L1",[],"decision_record",False,"success")],
    anchors("DEMO-GATE-S2", {"init": {"objective":"需求初稿","facts":["demo/calc.c 计算逻辑","main.c 调用入口"],"assumptions":[],"risks":[]},
                             "merge": {"objective":"审查结论一致直接合并","facts":["02_review 无残留 issue"],"assumptions":[],"risks":[]},
                             "readme":{"objective":"提炼交付报告要点","facts":["04_code 编译通过","05_deepcheck 通过"],"assumptions":[],"risks":[]}}))
# S3 单文件修复、已有 RED/GREEN —— L1：仅剩 merge 决策
write_ticket("s3_simple_merge_l1",
    {"ticket_id": "DEMO-GATE-S3", "thinking_gate_schema_version": 1, "completed_steps": ["3"], "desc": "单文件修复已有 RED/GREEN 测试，仅剩 merge"},
    [tline("DEMO-GATE-S3","merge","L1","L1",[],"decision_record",False,"success")],
    anchors("DEMO-GATE-S3", {"merge": {"objective":"单文件修复合并","facts":["RED 测试先红","GREEN 测试通过"],"assumptions":[],"risks":["提交 gate 只读核验"]}}))
# S4 多线程终态竞态、两个根因候选 —— L2（log：sequential-thinking 含反证/验证计划）
write_ticket("s4_log_race_l2",
    {"ticket_id": "DEMO-GATE-S4", "thinking_gate_schema_version": 1, "completed_steps": ["log"], "desc": "多线程终态竞态 + 两个根因候选"},
    [tline("DEMO-GATE-S4","log","L2","L2",["concurrency_state_machine","multiple_candidates","evidence_conflict"],"sequential-thinking",True,"success")],
    anchors("DEMO-GATE-S4", {"log": {"objective":"定位终态竞态根因","facts":["共享全局计数在释放后仍被读","两条 trace 时间戳矛盾"],"assumptions":["候选 A 由释放后读引起"],"risks":["需重跑注入验证"]}}))
# S5 跨模块生命周期重构 + 证据冲突 —— L3（audit：L2 + 独立对抗）
write_ticket("s5_audit_l3",
    {"ticket_id": "DEMO-GATE-S5", "thinking_gate_schema_version": 1, "completed_steps": ["6"], "desc": "跨模块生命周期重构 + 证据冲突 → L3 对抗"},
    [tline("DEMO-GATE-S5","audit","L3","L2",["multi_module_multi_file","evidence_conflict","destructive_irreversible"],"sequential-thinking+adversarial",True,"success")])
# S6 fast 模式命中跨模块状态机 —— L2 自动升级，不因 fast 跳过
write_ticket("s6_fast_l2",
    {"ticket_id": "DEMO-GATE-S6", "thinking_gate_schema_version": 1, "mode": "fast", "completed_steps": ["1"], "desc": "fast 模式命中跨模块状态机 → 自动升级 L2"},
    [tline("DEMO-GATE-S6","plan","L2","L2",["multi_module_multi_file","concurrency_state_machine"],"sequential-thinking",True,"success")])
PY
ok "fixture 生成（demo/.icode_output/.gate_sim/s1..s6）"

# 2) 六个行为样例 lint 全过
for d in s1_status_l0 s2_readme_l1 s3_simple_merge_l1 s4_log_race_l2 s5_audit_l3 s6_fast_l2; do
  if $LINT "$SIM/$d" --json >/tmp/gate_sim_$d.json 2>/dev/null; then
    ok "S($d) lint exit0"
  else
    bad "S($d) lint exit非0"
  fi
done

# 3) L0/L1 零 sequential-thinking
if grep -q "sequential-thinking" "$SIM/s1_status_l0/.thinking_gate_trace.jsonl" 2>/dev/null; then
  bad "S1 status 不应有 sequential-thinking trace"
else
  ok "S1 status 零 sequential-thinking"
fi
if grep -q "sequential-thinking" "$SIM/s3_simple_merge_l1/.thinking_gate_trace.jsonl" 2>/dev/null; then
  bad "S3 简单 merge 不应有 sequential-thinking"
else
  ok "S3 简单 merge 零 sequential-thinking"
fi

# 4) L1 必须落 .decision_anchors.json 决策摘要
for d in s2_readme_l1 s3_simple_merge_l1; do
  if [ -f "$SIM/$d/.decision_anchors.json" ] && grep -q "decision_anchors" "$SIM/$d/.decision_anchors.json"; then
    ok "$d 有 .decision_anchors.json 决策摘要"
  else
    bad "$d 缺 .decision_anchors.json 决策摘要"
  fi
done

# 5) L2 履行：attempted=true + sequential-thinking 机制 + 升级触发器
python3 - "$SIM/s4_log_race_l2" <<'PY' && ok "S4 L2 attempted=true + 触发器合规" || bad "S4 L2 履行断言失败"
import json,sys
r=json.loads(open(sys.argv[1]+"/.thinking_gate_trace.jsonl").read().strip().splitlines()[-1])
assert r["tier"]=="L2" and r["attempted"] is True
assert r["mechanism"]=="sequential-thinking"
assert "concurrency_state_machine" in r["triggers"] and "multiple_candidates" in r["triggers"]
PY
python3 - "$SIM/s6_fast_l2" <<'PY' && ok "S6 fast 命中跨模块状态机升级 L2" || bad "S6 fast L2 断言失败"
import json,sys
r=json.loads(open(sys.argv[1]+"/.thinking_gate_trace.jsonl").read().strip().splitlines()[-1])
assert r["tier"]=="L2" and r["attempted"] is True
assert "concurrency_state_machine" in r["triggers"]
PY

# 6) L3 必须 sequential-thinking+adversarial（独立对抗）
python3 - "$SIM/s5_audit_l3" <<'PY' && ok "S5 L3 独立对抗机制" || bad "S5 L3 对抗机制断言失败"
import json,sys
r=json.loads(open(sys.argv[1]+"/.thinking_gate_trace.jsonl").read().strip().splitlines()[-1])
assert r["tier"]=="L3" and r["mechanism"]=="sequential-thinking+adversarial"
assert r["attempted"] is True and "destructive_irreversible" in r["triggers"]
PY

# 7) trace 无 thought 正文/敏感字段
python3 - "$SIM" <<'PY' && ok "trace 无 thought 正文/敏感字段" || bad "trace 敏感扫描失败"
import sys,pathlib
bad_kw=["thought","api_key","cookie","password","secret","authorization","bearer"]
base=pathlib.Path(sys.argv[1])
for tf in base.glob("s*/.thinking_gate_trace.jsonl"):
    if not tf.exists(): continue
    for line in tf.read_text().splitlines():
        low=line.lower()
        for kw in bad_kw:
            assert kw not in low, f"{tf} 含 {kw}"
PY

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
