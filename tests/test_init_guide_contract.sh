#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }
require_text() {
  local file="$1" text="$2" label="$3"
  if [[ -f "$file" ]] && rg -qF -- "$text" "$file"; then ok "$label"; else bad "$label"; fi
}

require_text SKILL.md '/icode init [--guide]' 'SKILL exposes init --guide'
require_text steps/00_init.md '不创建新工单' 'guide mode reuses an init ticket'
require_text steps/00_init.md 'deliverables/guide.md' 'guide output path is canonical'
require_text steps/00_init.md 'deliverables/guide.audit.json' 'guide audit path is canonical'
require_text steps/00_init.md '--require-init-ready' 'guide source requires a complete init analysis'
require_text references/guide_contract.md 'acceptance_threshold' 'quantitative acceptance thresholds are classified'
require_text references/guide_contract.md 'observed_result' 'quantitative observed results are classified'
require_text references/guide_contract.md 'defect_rate' 'defect reproduction rates are classified'
require_text references/guide_contract.md 'tool_default' 'tool defaults are classified'
require_text references/guide_contract.md '工作目录' 'tool working-directory assumptions are audited'
require_text references/guide_contract.md '覆盖还是追加' 'tool output write mode is audited'
require_text references/guide_contract.md '术语首次出现' 'beginner jargon has a readability gate'
require_text references/guide_contract.md '架构图和流程图' 'guide requires both diagrams with explanations'
require_text references/guide_contract.md '两轮' 'guide audit requires two clean rounds'
require_text README.zh-CN.md '/icode init --guide' 'Chinese README documents guide mode'
require_text README.md '/icode init --guide' 'English README documents guide mode'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/project/.icode_output/.icode_output_1" \
         "$TMP/project/.icode_output/.icode_output_2"
cat >"$TMP/project/.icode_output/.icode_output_1/.ico_metadata.json" <<'JSON'
{"schema_version":3,"requirement":"guide","created_at":"2026-01-01T00:00:00Z","status":"init_in_progress","completed_steps":["0"],"ticket_id":"guide-1"}
JSON
cat >"$TMP/project/.icode_output/.icode_output_1/00_init.md" <<'MARKDOWN'
# 需求初稿：新人指南
## 1. 背景与目标
帮助新人理解项目。
## 2. 现状盘点
当前工程包含接口、测试和部署脚本。
## 3. 新增需求点
生成一份可独立阅读的指南。
## 4. 影响面与关联模块
只新增派生文档，不改变工程代码。
## 5. 关键问题与待决策项
当前运行结果仍需目标环境确认。
## 6. 链路图（最终改动总览）
调用方 -> 封装层 -> SDK -> 设备。
## 7. 4 维度验证清单
检查来源、工具、边界和可读性。
MARKDOWN
cat >"$TMP/project/.icode_output/.icode_output_2/.ico_metadata.json" <<'JSON'
{"schema_version":3,"requirement":"other","created_at":"2026-01-02T00:00:00Z","status":"completed","completed_steps":["1","2","3","4","5","6"],"ticket_id":"other-2"}
JSON
mkdir -p "$TMP/project/.icode_output/.icode_output_4"
cat >"$TMP/project/.icode_output/.icode_output_4/.ico_metadata.json" <<'JSON'
{"schema_version":3,"requirement":"empty","created_at":"2026-01-03T00:00:00Z","status":"init_in_progress","completed_steps":["0"],"ticket_id":"empty-4"}
JSON
: >"$TMP/project/.icode_output/.icode_output_4/00_init.md"

RESOLVED="$TMP/resolved.json"
if python3 tools/icode_control.py resolve-ticket --latest --workspace "$TMP/project" \
     --require-artifact 00_init.md --require-status init_in_progress \
     --require-init-ready >"$RESOLVED" 2>/dev/null \
  && python3 - "$RESOLVED" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["out_dir"].endswith(".icode_output_1")
assert data["status"] == "init_in_progress"
PY
then
  ok 'resolver skips a newer empty init and selects the latest complete source'
else
  bad 'resolver skips a newer empty init and selects the latest complete source'
fi

if python3 tools/icode_control.py resolve-ticket --latest --workspace "$TMP/project" \
     2>/dev/null | rg -q '"ticket_id": "other-2"'; then
  bad 'unfiltered latest resolver keeps its original behavior'
else
  if python3 tools/icode_control.py resolve-ticket --latest --workspace "$TMP/project" \
       2>/dev/null | rg -q '"ticket_id": "empty-4"'; then
    ok 'unfiltered latest resolver keeps its original behavior'
  else
    bad 'unfiltered latest resolver keeps its original behavior'
  fi
fi

mkdir -p "$TMP/outside-ticket"
cat >"$TMP/outside-ticket/.ico_metadata.json" <<'JSON'
{"schema_version":3,"status":"init_in_progress","ticket_id":"outside-3"}
JSON
printf '# Outside\n' >"$TMP/outside-ticket/00_init.md"
ln -s "$TMP/outside-ticket" "$TMP/project/.icode_output/.icode_output_3"
if python3 tools/icode_control.py resolve-ticket --latest --workspace "$TMP/project" \
     --require-artifact 00_init.md --require-status init_in_progress \
     --require-init-ready 2>/dev/null \
     | rg -q '"ticket_id": "guide-1"'; then
  ok 'filtered latest resolver rejects workspace-escaping ticket symlinks'
else
  bad 'filtered latest resolver rejects workspace-escaping ticket symlinks'
fi

MISMATCH_OUTPUT="$TMP/mismatch.out"
if python3 tools/icode_control.py resolve-ticket \
     --dir "$TMP/project/.icode_output/.icode_output_2" \
     --require-status init_in_progress >"$MISMATCH_OUTPUT" 2>&1; then
  bad 'explicit ticket violating guide filters is rejected'
elif rg -q 'status=' "$MISMATCH_OUTPUT"; then
  ok 'explicit ticket violating guide filters is rejected'
else
  bad 'filter rejection reports the mismatched status'
fi

set +e
python3 tools/icode_control.py resolve-ticket --latest --workspace "$TMP/project" \
  --require-artifact ../outside >"$TMP/unsafe.out" 2>&1
UNSAFE_RC=$?
set -e
if [[ "$UNSAFE_RC" -eq 2 ]] && rg -q '安全相对路径' "$TMP/unsafe.out"; then
  ok 'artifact filter rejects paths outside the ticket root'
else
  bad 'artifact filter rejects paths outside the ticket root'
fi

mkdir -p "$TMP/project/.icode_output/.icode_output_1/deliverables"
mkdir -p "$TMP/project/tools"
printf '# test tool\n' >"$TMP/project/tools/example.py"
printf 'historical workbook fixture\n' >"$TMP/project/report.xlsx"
GUIDE="$TMP/project/.icode_output/.icode_output_1/deliverables/guide.md"
AUDIT="$TMP/project/.icode_output/.icode_output_1/deliverables/guide.audit.json"
cat >"$GUIDE" <<'MARKDOWN'
# 新人指南

## 术语速查

ROS2 是模块间传递消息和调用服务的通信框架。

## 架构

```text
客户程序 -> ROS2 封装 -> SDK -> 设备
```

怎么看这张图：从左向右看请求，返回数据沿相反方向交付给客户。

## 接口

接口说明输入、输出和失败返回。

## 流程与交付

接口冻结后依次自测、提测、回归并打包交付。

```text
接口确认 -> 开发自测 -> 提测 -> 回归 -> 交付
```

怎么看这张图：从左向右推进，每一段都要把输入、输出和失败证据交给下一段。

## 测试

历史样例曾连续运行 12 小时。

建议每次提测前重新定义准入阈值。

测试工具 `example_tool` 的静态调用形式为 `python3 tools/example.py --help`，工作目录是 `workspace root`。

## 环境部署

先确认依赖和设备权限，再安装构建产物并核对启动日志。

## 端到端示例

客户请求经封装层进入 SDK 和设备，返回数据再由测试工具核对。

## 边界

当前是否通过仍需实机验证。
MARKDOWN
cat >"$AUDIT" <<JSON
{
  "schema_version": 1,
  "profile": "beginner_guide",
  "source_init": "$TMP/project/.icode_output/.icode_output_1/00_init.md",
  "guide": "guide.md",
  "required_sections": ["术语速查", "架构", "接口", "流程与交付", "测试", "环境部署", "端到端示例", "边界"],
  "claims": [
    {
      "text": "历史样例曾连续运行 12 小时。",
      "kind": "historical_evidence",
      "source_refs": ["report.xlsx#Sheet1!A1"],
      "scope": "仅对应历史版本和环境",
      "quantitative": {"category": "observed_result", "value": "12", "unit": "hour"}
    },
    {
      "text": "建议每次提测前重新定义准入阈值。",
      "kind": "engineering_recommendation",
      "source_refs": [],
      "scope": "通用工程建议"
    },
    {
      "text": "当前是否通过仍需实机验证。",
      "kind": "unverified",
      "source_refs": [],
      "scope": "未执行目标环境和设备验证"
    }
  ],
  "tool_checks": [
    {
      "name": "example_tool",
      "source_path": "tools/example.py",
      "run": "python3 tools/example.py --help",
      "cwd": "workspace root",
      "prerequisites": [],
      "config": [],
      "outputs": [],
      "write_mode": "none",
      "hardcoded_constraints": [],
      "verification": "static"
    }
  ],
  "manual_checks": {
    "jargon_explained": true,
    "diagrams_explained": true,
    "end_to_end_example": true,
    "fact_recommendation_boundary": true
  },
  "clean_rounds": []
}
JSON

VALID_OUTPUT="$TMP/valid.out"
: >"$VALID_OUTPUT"
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 1 >>"$VALID_OUTPUT" 2>&1 \
  && python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 2 >>"$VALID_OUTPUT" 2>&1 \
  && python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 >>"$VALID_OUTPUT" 2>&1; then
  ok 'valid beginner guide and evidence audit pass'
else
  bad 'valid beginner guide and evidence audit pass'
  sed -n '1,30p' "$VALID_OUTPUT" >&2
fi

python3 - "$AUDIT" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["clean_rounds"] = [{
    "round": 1,
    "result": "pass",
    "focus": "forged",
    "recorded_by": "lint_guide_contract.py",
    "recorded_at": "manual",
    "guide_sha256": "0" * 64,
    "audit_basis_sha256": "0" * 64
}]
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 2 >"$TMP/forged-round.out" 2>&1; then
  bad 'forged first-round receipt cannot unlock round two'
elif rg -q 'receipt|回执|round 1' "$TMP/forged-round.out"; then
  ok 'forged first-round receipt cannot unlock round two'
else
  bad 'forged round rejection reports a useful reason'
fi
python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
  --forbid-term 邮件 --record-round 1 >/dev/null 2>&1
python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
  --forbid-term 邮件 --record-round 2 >/dev/null 2>&1

python3 - "$GUIDE" "$AUDIT" <<'PY'
import json, sys
guide_path, audit_path = sys.argv[1:]
old = "历史样例曾连续运行 12 小时。"
new = "该系统支持 12 路并发。"
text = open(guide_path, encoding="utf-8").read().replace(old, new)
open(guide_path, "w", encoding="utf-8").write(text)
data = json.load(open(audit_path, encoding="utf-8"))
data["claims"][0]["text"] = new
data["claims"][0]["quantitative"] = {
    "category": "observed_result", "value": "12", "unit": "channel"
}
data["clean_rounds"] = []
with open(audit_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 1 >"$TMP/kind-language.out" 2>&1; then
  bad 'historical evidence must be labeled historical in reader-facing text'
elif rg -q 'historical_evidence|历史' "$TMP/kind-language.out"; then
  ok 'historical evidence must be labeled historical in reader-facing text'
else
  bad 'claim-language rejection reports the mismatched kind'
fi
python3 - "$GUIDE" "$AUDIT" <<'PY'
import json, sys
guide_path, audit_path = sys.argv[1:]
old = "该系统支持 12 路并发。"
new = "历史样例曾连续运行 12 小时。"
text = open(guide_path, encoding="utf-8").read().replace(old, new)
open(guide_path, "w", encoding="utf-8").write(text)
data = json.load(open(audit_path, encoding="utf-8"))
data["claims"][0]["text"] = new
data["claims"][0]["quantitative"] = {
    "category": "observed_result", "value": "12", "unit": "hour"
}
data["clean_rounds"] = []
with open(audit_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY

printf '\n历史样例执行 6 万次。status=completed；参见步骤 6。\n' >>"$GUIDE"
python3 - "$AUDIT" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["claims"].append({
    "text": "历史样例执行 6 万次。status=completed；参见步骤 6。",
    "kind": "historical_evidence",
    "source_refs": ["report.xlsx#Sheet1!A1"],
    "scope": "历史样例"
})
data["clean_rounds"] = []
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 1 >"$TMP/chinese-number.out" 2>&1; then
  bad 'Chinese-scale counts and workflow status/step leakage are rejected'
elif rg -q '6 万次' "$TMP/chinese-number.out" \
  && rg -q 'status|步骤' "$TMP/chinese-number.out"; then
  ok 'Chinese-scale counts and workflow status/step leakage are rejected'
else
  bad 'Chinese count/internal leakage reports every offending form'
fi
python3 - "$GUIDE" "$AUDIT" <<'PY'
import json, sys
guide_path, audit_path = sys.argv[1:]
line = "\n历史样例执行 6 万次。status=completed；参见步骤 6。\n"
text = open(guide_path, encoding="utf-8").read().replace(line, "\n")
open(guide_path, "w", encoding="utf-8").write(text)
data = json.load(open(audit_path, encoding="utf-8"))
data["claims"] = data["claims"][:-1]
data["clean_rounds"] = []
with open(audit_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
  --forbid-term 邮件 --record-round 1 >/dev/null 2>&1
python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
  --forbid-term 邮件 --record-round 2 >/dev/null 2>&1

python3 - "$AUDIT" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["clean_rounds"] = data["clean_rounds"][:1]
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     >"$TMP/rounds.out" 2>&1; then
  bad 'a single clean audit round is rejected'
elif rg -q '至少记录两轮' "$TMP/rounds.out"; then
  ok 'a single clean audit round is rejected'
else
  bad 'clean-round rejection reports a useful reason'
fi
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     --forbid-term 邮件 --record-round 2 >"$TMP/restore.out" 2>&1; then
  ok 'second clean round can be restored'
else
  bad 'second clean round can be restored'
  sed -n '1,20p' "$TMP/restore.out" >&2
fi

printf '\n内部来源见 00_init.md。\n' >>"$GUIDE"
INVALID_OUTPUT="$TMP/invalid.out"
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     >"$INVALID_OUTPUT" 2>&1; then
  bad 'internal workflow leakage is rejected'
elif rg -q '00_init.md' "$INVALID_OUTPUT"; then
  ok 'internal workflow leakage is rejected'
else
  bad 'internal workflow leakage reports a useful reason'
fi

BAD_ROOT="$TMP/project/.icode_output/.icode_output_9"
BAD_DIR="$BAD_ROOT/deliverables"
mkdir -p "$BAD_DIR"
cp "$TMP/project/.icode_output/.icode_output_1/00_init.md" "$BAD_ROOT/00_init.md"
BAD_GUIDE="$BAD_DIR/guide.md"
BAD_AUDIT="$BAD_DIR/guide.audit.json"
cat >"$BAD_GUIDE" <<'MARKDOWN'
# 伪指南
## 自报章节
当前正式支持 999 路并发，运行 magic_tool 即可通过。
MARKDOWN
cat >"$BAD_AUDIT" <<JSON
{
  "schema_version": 1,
  "profile": "beginner_guide",
  "source_init": "$BAD_ROOT/00_init.md",
  "guide": "guide.md",
  "required_sections": ["自报章节"],
  "claims": [
    {
      "text": "当前正式支持 999 路并发，运行 magic_tool 即可通过。",
      "kind": "current_fact",
      "source_refs": ["missing-proof.txt"],
      "scope": "current"
    }
  ],
  "tool_checks": [],
  "manual_checks": {
    "jargon_explained": true,
    "diagrams_explained": true,
    "end_to_end_example": true,
    "fact_recommendation_boundary": true
  },
  "clean_rounds": []
}
JSON
if python3 tools/lint_guide_contract.py --guide "$BAD_GUIDE" --audit "$BAD_AUDIT" \
     --record-round 1 >"$TMP/false-green.out" 2>&1; then
  bad 'fabricated numbers, tools, and self-declared sections are rejected'
elif rg -q '999|source_refs|tool_checks|架构' "$TMP/false-green.out"; then
  ok 'fabricated numbers, tools, and self-declared sections are rejected'
else
  bad 'false-green rejection reports actionable reasons'
fi

printf '\nticket_id=secret；completed_steps=[0]；审查轮次见 guide.audit.json。\n' >>"$GUIDE"
if python3 tools/lint_guide_contract.py --guide "$GUIDE" --audit "$AUDIT" \
     >"$TMP/internal-terms.out" 2>&1; then
  bad 'extended internal workflow vocabulary is rejected'
elif rg -q 'ticket_id|completed_steps|guide.audit.json' "$TMP/internal-terms.out"; then
  ok 'extended internal workflow vocabulary is rejected'
else
  bad 'extended internal vocabulary rejection reports offending terms'
fi

printf '\nRESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
