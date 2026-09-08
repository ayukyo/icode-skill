#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

TMP="$(mktemp -d /tmp/icode-verification-debt.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
PROJECT="$TMP/project"
HOME_DIR="$TMP/home"
ROOT="$PROJECT/.icode_output"
TICKET="$ROOT/.icode_output_1"
NOT_REQUIRED="$ROOT/.icode_output_2"
LEGACY="$ROOT/.icode_output_3"
UNTRACKED="$ROOT/.icode_output_4"
INVALID="$ROOT/.icode_output_5"
BLOCKED="$ROOT/.icode_output_6"
INVALID_DUP="$ROOT/.icode_output_7"
INVALID_LAYER="$ROOT/.icode_output_8"
ARCHIVE="$TMP/archive/VERIFY-ARCH"
ARTIFACT_META="$TMP/artifacts/VERIFY-ARTIFACT/.ico_metadata.json"
BACKUP_ROOT="$TMP/backup-snapshot"
BACKUP_TICKET="$BACKUP_ROOT/.icode_output/.icode_output_406"
MISMATCH="$TMP/archive/VERIFY-MISMATCH"
ESCAPE_POINTER="$TMP/archive/VERIFY-ESCAPE"
ESCAPE_TARGET="$TMP/outside/VERIFY-ESCAPE"
REPORT_ROOT="$ROOT/reports"
mkdir -p "$TICKET" "$NOT_REQUIRED" "$LEGACY" "$UNTRACKED" "$INVALID" \
  "$BLOCKED" "$INVALID_DUP" "$INVALID_LAYER" "$ARCHIVE" "$(dirname "$ARTIFACT_META")" "$BACKUP_TICKET" \
  "$MISMATCH" "$ESCAPE_POINTER" "$ESCAPE_TARGET" "$REPORT_ROOT" "$HOME_DIR/.claude/icode_data"

cat > "$TICKET/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "VERIFY-1",
  "status": "completed",
  "delivery_verdict": "verification_pending",
  "verification_contract": {
    "required": true,
    "required_layers": ["delivery", "consumption", "physical", "closed_client", "host", "settings_preview"],
    "required_consumers": ["algorithm"],
    "required_scenarios": ["app_closed"]
  },
  "verification_runs": [
    {
      "run_id": "delivery-pass",
      "at": "2026-09-08T09:00:00+08:00",
      "kind": "listen",
      "layer": "delivery",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "pass",
      "evidence": "IPC receive id=7",
      "baseline": "device=SN1 artifact=sha256:a"
    },
    {
      "run_id": "consume-old-pass",
      "at": "2026-09-08T09:01:00+08:00",
      "kind": "device_test",
      "layer": "consumption",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "pass",
      "evidence": "consumer applied id=7",
      "baseline": "device=SN1 artifact=sha256:a"
    },
    {
      "run_id": "consume-latest-fail",
      "at": "2026-09-08T09:02:00+08:00",
      "kind": "device_test",
      "layer": "consumption",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "fail",
      "evidence": "consumer rejected id=7",
      "baseline": "device=SN1 artifact=sha256:a"
    },
    {
      "run_id": "physical-no-evidence",
      "at": "2026-09-08T09:03:00+08:00",
      "kind": "device_test",
      "layer": "physical",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "pass",
      "evidence": "  ",
      "baseline": "device=SN1 artifact=sha256:a"
    },
    {
      "run_id": "closed-client-no-baseline",
      "at": "2026-09-08T09:04:00+08:00",
      "kind": "device_test",
      "layer": "closed_client",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "pass",
      "evidence": "push received while app closed",
      "baseline": ""
    },
    {
      "run_id": "preview-inconclusive",
      "at": "2026-09-08T09:05:00+08:00",
      "kind": "device_test",
      "layer": "settings_preview",
      "consumer": "algorithm",
      "scenario": "app_closed",
      "outcome": "inconclusive",
      "evidence": "preview capture unavailable",
      "baseline": "app=3.2.1 device=SN1 artifact=sha256:a"
    }
  ]
}
JSON

cat > "$ARTIFACT_META" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "VERIFY-ARTIFACT",
  "status": "completed",
  "delivery_verdict": "verified",
  "verification_contract": {
    "required": true,
    "required_layers": ["host"],
    "required_consumers": ["tool"],
    "required_scenarios": ["nominal"]
  },
  "verification_runs": [{
    "run_id": "host-pass", "at": "2026-09-08T09:07:00+08:00",
    "kind": "device_test", "layer": "host", "consumer": "tool",
    "scenario": "nominal", "outcome": "pass",
    "evidence": "contract output", "baseline": "commit=abc123"
  }]
}
JSON

cat > "$BACKUP_TICKET/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "VERIFY-BACKUP",
  "status": "completed",
  "delivery_verdict": "verification_pending",
  "verification_contract": {
    "required": true,
    "required_layers": ["deploy"],
    "required_consumers": ["device"],
    "required_scenarios": ["restart"]
  },
  "verification_runs": []
}
JSON

cat > "$MISMATCH/.ico_metadata.json" <<'JSON'
{"schema_version":3,"ticket_id":"OTHER-TICKET","status":"completed","verification_contract":{"required":false,"required_layers":[],"required_consumers":[],"required_scenarios":[]}}
JSON

cat > "$ESCAPE_TARGET/.ico_metadata.json" <<'JSON'
{"schema_version":3,"ticket_id":"VERIFY-ESCAPE","status":"completed","verification_contract":{"required":false,"required_layers":[],"required_consumers":[],"required_scenarios":[]}}
JSON
ln -s "$ESCAPE_TARGET/.ico_metadata.json" "$ESCAPE_POINTER/.ico_metadata.json"

cat > "$NOT_REQUIRED/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "HOST-ONLY",
  "status": "completed",
  "delivery_verdict": "not_applicable",
  "verification_contract": {
    "required": false,
    "required_layers": [],
    "required_consumers": [],
    "required_scenarios": []
  },
  "verification_runs": []
}
JSON

cat > "$UNTRACKED/.ico_metadata.json" <<'JSON'
{"schema_version":3,"ticket_id":"V3-UNTRACKED","status":"completed","verification_runs":[]}
JSON

cat > "$INVALID/.ico_metadata.json" <<'JSON'
{"schema_version":3,"ticket_id":"V3-INVALID","status":"completed","verification_contract":{"required":"yes"},"verification_runs":[]}
JSON

cat > "$INVALID_DUP/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "V3-INVALID-DUP",
  "status": "completed",
  "verification_contract": {
    "required": true,
    "required_layers": ["host", "host"],
    "required_consumers": ["tool"],
    "required_scenarios": ["nominal"]
  },
  "verification_runs": []
}
JSON

cat > "$INVALID_LAYER/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "V3-INVALID-LAYER",
  "status": "completed",
  "verification_contract": {
    "required": true,
    "required_layers": ["imaginary_layer"],
    "required_consumers": ["tool"],
    "required_scenarios": ["nominal"]
  },
  "verification_runs": []
}
JSON

cat > "$BLOCKED/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "VERIFY-BLOCKED",
  "status": "completed",
  "delivery_verdict": "blocked",
  "verification_contract": {
    "required": true,
    "required_layers": ["delivery"],
    "required_consumers": ["device"],
    "required_scenarios": ["nominal"]
  },
  "verification_runs": [{
    "run_id": "blocked-pass", "at": "2026-09-08T09:06:00+08:00",
    "kind": "listen", "layer": "delivery", "consumer": "device",
    "scenario": "nominal", "outcome": "pass",
    "evidence": "delivered", "baseline": "device=SN2 artifact=sha256:b"
  }]
}
JSON

cat > "$ARCHIVE/.ico_metadata.json" <<'JSON'
{
  "schema_version": 3,
  "ticket_id": "VERIFY-ARCH",
  "status": "completed",
  "delivery_verdict": "verification_pending",
  "verification_contract": {
    "required": true,
    "required_layers": ["physical"],
    "required_consumers": ["device"],
    "required_scenarios": ["replay"]
  },
  "verification_runs": []
}
JSON

cat > "$LEGACY/.ico_metadata.json" <<'JSON'
{
  "ticket_id": "LEGACY-1",
  "status": "completed",
  "pending_verification": ["device"]
}
JSON

cat > "$HOME_DIR/.claude/icode_data/index.json" <<JSON
{
  "version": 1,
  "updated_at": "2026-09-08T09:05:00+08:00",
  "tickets": [
    {"control_schema_version": 3, "ticket_id": "VERIFY-1", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_1", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "HOST-ONLY", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_2", "status": "completed"},
    {"ticket_id": "LEGACY-1", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_3", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-ARCH", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_404", "archive_path": "$ARCHIVE", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-ARTIFACT", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_405", "artifact_root": "$ARTIFACT_META", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-BACKUP", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_406", "backup_path": "$BACKUP_ROOT", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-MISMATCH", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_407", "archive_path": "$MISMATCH", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-ESCAPE", "project_path": "$PROJECT", "out_dir": ".icode_output/.icode_output_408", "archive_path": "$ESCAPE_POINTER", "status": "completed"},
    {"control_schema_version": 3, "ticket_id": "VERIFY-PATH-ESCAPE", "project_path": "$PROJECT", "out_dir": "../outside/.icode_output_409", "archive_path": "$ESCAPE_TARGET", "status": "completed"}
  ]
}
JSON

BEFORE_PENDING="$(sha256sum "$TICKET/.ico_metadata.json" "$NOT_REQUIRED/.ico_metadata.json" "$LEGACY/.ico_metadata.json")"
printf 'stale' > "$REPORT_ROOT/debt.json"
printf 'stale' > "$REPORT_ROOT/debt.md"
if HOME="$HOME_DIR" python3 tools/verification_debt.py pending \
    --project "$PROJECT" --output "$REPORT_ROOT/debt.json" --markdown "$REPORT_ROOT/debt.md" \
    > "$TMP/pending.stdout" 2> "$TMP/pending.stderr"; then
  ok "pending 命令生成验证债务报告"
else
  bad "pending 命令执行失败: $(cat "$TMP/pending.stderr")"
fi

if python3 - "$REPORT_ROOT/debt.json" "$REPORT_ROOT/debt.md" "$PROJECT" <<'PY'
import json
import pathlib
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
markdown = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
assert report["schema_version"] == 1
assert report["command"] == "pending"
assert report["project"] == str(pathlib.Path(sys.argv[3]).resolve())
assert report["sources"]["index"]["loaded"] is True
assert report["status"] == "partial"
assert report["summary"] == {
    "tickets": 11,
    "tracked_required": 8,
    "not_required": 1,
    "legacy_untracked": 1,
    "untracked": 1,
    "blocked_tickets": 1,
    "required_units": 10,
    "satisfied_units": 3,
    "pending_units": 7,
}
tickets = {item["ticket_id"]: item for item in report["tickets"]}
tracked = tickets["VERIFY-1"]
assert tracked["tracking_status"] == "verification_pending"
states = {unit["layer"]: unit["state"] for unit in tracked["units"]}
assert states == {
    "delivery": "satisfied",
    "consumption": "latest_outcome_fail",
    "physical": "evidence_missing",
    "closed_client": "baseline_missing",
    "host": "run_missing",
    "settings_preview": "latest_outcome_inconclusive",
}
assert tracked["pending_units"] == 5
assert tickets["HOST-ONLY"]["tracking_status"] == "not_required"
assert tickets["LEGACY-1"]["tracking_status"] == "legacy_untracked"
assert tickets["V3-UNTRACKED"]["tracking_status"] == "untracked"
assert tickets["V3-INVALID"]["tracking_status"] == "invalid_contract"
assert tickets["V3-INVALID-DUP"]["tracking_status"] == "invalid_contract"
assert tickets["V3-INVALID-LAYER"]["tracking_status"] == "invalid_contract"
assert tickets["VERIFY-BLOCKED"]["tracking_status"] == "blocked"
assert tickets["VERIFY-BLOCKED"]["ticket_blocked"] is True
assert tickets["VERIFY-ARCH"]["tracking_status"] == "verification_pending"
assert tickets["VERIFY-ARCH"]["ticket_dir"].endswith("archive/VERIFY-ARCH")
assert tickets["VERIFY-ARTIFACT"]["tracking_status"] == "satisfied"
assert tickets["VERIFY-ARTIFACT"]["ticket_dir"].endswith("artifacts/VERIFY-ARTIFACT")
assert tickets["VERIFY-BACKUP"]["tracking_status"] == "verification_pending"
assert tickets["VERIFY-BACKUP"]["ticket_dir"].endswith("backup-snapshot/.icode_output/.icode_output_406")
assert "VERIFY-MISMATCH" not in tickets
assert "VERIFY-ESCAPE" not in tickets
assert "VERIFY-PATH-ESCAPE" not in tickets
assert any("VERIFY-MISMATCH" in error for error in report["errors"])
assert any("VERIFY-ESCAPE" in error for error in report["errors"])
assert any("VERIFY-PATH-ESCAPE" in error for error in report["errors"])
assert report["groups"]["by_layer"]["consumption"] == 1
assert report["groups"]["by_layer"]["settings_preview"] == 1
assert report["groups"]["by_layer"]["deploy"] == 1
assert report["groups"]["by_scenario"]["app_closed"] == 5
for token in ("VERIFY-1", "VERIFY-ARCH", "VERIFY-ARTIFACT", "VERIFY-BACKUP", "VERIFY-BLOCKED", "V3-UNTRACKED", "LEGACY-1", "latest_outcome_fail", "latest_outcome_inconclusive", "evidence_missing", "baseline_missing", "run_missing"):
    assert token in markdown, token
PY
then
  ok "pending 正确计算 latest-pass、证据、baseline 与 legacy 债务"
else
  bad "pending 报告结构或债务计算错误"
fi

# 只读报告不得覆盖控制面事件真源。
printf 'EVENT-SENTINEL\n' > "$TICKET/.ico_events.jsonl"
EVENT_BEFORE="$(sha256sum "$TICKET/.ico_events.jsonl")"
if python3 tools/verification_debt.py plan --ticket-dir "$TICKET" \
    --output "$TICKET/.ico_events.jsonl" --markdown "$TICKET/event-protected.md" \
    >/dev/null 2>&1; then
  bad "输出路径允许覆盖事件真源"
elif [[ "$EVENT_BEFORE" == "$(sha256sum "$TICKET/.ico_events.jsonl")" ]]; then
  ok "拒绝输出覆盖事件真源"
else
  bad "拒绝后事件真源仍被改写"
fi

AFTER_PENDING="$(sha256sum "$TICKET/.ico_metadata.json" "$NOT_REQUIRED/.ico_metadata.json" "$LEGACY/.ico_metadata.json")"
if [[ "$BEFORE_PENDING" == "$AFTER_PENDING" ]]; then
  ok "pending 只读 metadata"
else
  bad "pending 修改了 metadata"
fi

BEFORE_PLAN="$(sha256sum "$TICKET/.ico_metadata.json")"
printf 'stale' > "$TICKET/plan.json"
printf 'stale' > "$TICKET/plan.md"
if python3 tools/verification_debt.py plan \
    --ticket-dir "$TICKET" --output "$TICKET/plan.json" --markdown "$TICKET/plan.md" \
    > "$TMP/plan.stdout" 2> "$TMP/plan.stderr"; then
  ok "plan 命令生成单工单验证计划"
else
  bad "plan 命令执行失败: $(cat "$TMP/plan.stderr")"
fi

if python3 - "$TICKET/plan.json" "$TICKET/plan.md" "$TICKET" <<'PY'
import json
import pathlib
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
markdown = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
assert plan["schema_version"] == 1
assert plan["command"] == "plan"
assert plan["ticket_id"] == "VERIFY-1"
assert plan["ticket_dir"] == str(pathlib.Path(sys.argv[3]).resolve())
assert plan["summary"]["required_units"] == 6
assert plan["summary"]["satisfied_units"] == 1
assert plan["summary"]["pending_units"] == 5
actions = plan["actions"]
assert len(actions) == 5
assert any(action["current_state"] == "latest_outcome_inconclusive" for action in actions)
for action in actions:
    assert action["required_evidence"]
    assert action["required_baseline"]
    command = action["record_command"]
    assert isinstance(command, list) and "record-verification" in command
    assert "--layer" in command and "--consumer" in command and "--scenario" in command
assert "VERIFY-1" in markdown
assert "record-verification" in markdown
PY
then
  ok "plan 仅为未满足单元生成结构化动作"
else
  bad "plan 内容不符合合同"
fi

AFTER_PLAN="$(sha256sum "$TICKET/.ico_metadata.json")"
if [[ "$BEFORE_PLAN" == "$AFTER_PLAN" ]]; then
  ok "plan 不记录虚假 verification run"
else
  bad "plan 修改了 metadata"
fi

# required 单元已通过但 delivery_verdict=blocked 时，plan 仍须保留工单级阻塞原因。
if python3 tools/verification_debt.py plan --ticket-dir "$BLOCKED" \
    --output "$BLOCKED/blocked-plan.json" --markdown "$BLOCKED/blocked-plan.md" \
    >/dev/null 2> "$TMP/blocked-plan.stderr" \
  && python3 - "$BLOCKED/blocked-plan.json" "$BLOCKED/blocked-plan.md" <<'PY'
import json
import pathlib
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
markdown = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
assert plan["tracking_status"] == "blocked"
assert plan["ticket_blocked"] is True
assert plan["blocking_reason"] == "delivery_verdict_blocked"
assert plan["summary"] == {"required_units": 1, "satisfied_units": 1, "pending_units": 0}
assert plan["actions"] == []
assert "delivery_verdict_blocked" in markdown
PY
then
  ok "plan 保留 delivery_verdict 的工单级阻塞"
else
  bad "plan 丢失工单级阻塞原因"
fi

if ! find "$TMP" -type f -name '*.tmp' -print -quit | grep -q . \
    && python3 -m json.tool "$REPORT_ROOT/debt.json" >/dev/null \
    && python3 -m json.tool "$TICKET/plan.json" >/dev/null; then
  ok "JSON/Markdown 采用无残留的原子替换"
else
  bad "输出存在临时文件残留或 JSON 非法"
fi

PROTECTED="$ROOT/.icode_output_4"
mkdir -p "$PROTECTED"
cp "$TICKET/.ico_metadata.json" "$PROTECTED/.ico_metadata.json"
BEFORE_PROTECTED="$(sha256sum "$PROTECTED/.ico_metadata.json")"
if python3 tools/verification_debt.py plan --ticket-dir "$PROTECTED" \
    --output "$PROTECTED/.ico_metadata.json" --markdown "$PROTECTED/protected.md" \
    >/dev/null 2>&1; then
  bad "输出路径允许覆盖 metadata"
else
  AFTER_PROTECTED="$(sha256sum "$PROTECTED/.ico_metadata.json")"
  if [[ "$BEFORE_PROTECTED" == "$AFTER_PROTECTED" ]]; then
    ok "拒绝输出覆盖 metadata/index 真源"
  else
    bad "拒绝后 metadata 仍被改写"
  fi
fi

# 输出根、源文件别名与双文件事务边界。
if python3 - "$PWD" "$PROJECT" "$TICKET" "$REPORT_ROOT" "$HOME_DIR" <<'PY'
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
project = pathlib.Path(sys.argv[2])
ticket = pathlib.Path(sys.argv[3])
report_root = pathlib.Path(sys.argv[4])
home = pathlib.Path(sys.argv[5])
tool = repo / "tools/verification_debt.py"
env = dict(os.environ, HOME=str(home))

def run(*args):
    return subprocess.run(
        [sys.executable, str(tool), *map(str, args)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

# 根目录不是可接受的 project，即使显式输出可写也必须先拒绝。
root_output = report_root / "root-project.json"
root_output.write_text("ROOT-SENTINEL\n", encoding="utf-8")
assert run("pending", "--project", "/", "--output", root_output,
           "--markdown", report_root / "root-project.md").returncode != 0
assert root_output.read_text(encoding="utf-8") == "ROOT-SENTINEL\n"

# pending 只能写 project/.icode_output；plan 只能写 ticket-dir。
outside_pending = project / "outside-pending.json"
outside_pending.write_text("PENDING-SENTINEL\n", encoding="utf-8")
assert run("pending", "--project", project, "--output", outside_pending,
           "--markdown", report_root / "inside.md").returncode != 0
assert outside_pending.read_text(encoding="utf-8") == "PENDING-SENTINEL\n"
outside_plan = project / ".icode_output" / "outside-plan.json"
outside_plan.write_text("PLAN-SENTINEL\n", encoding="utf-8")
assert run("plan", "--ticket-dir", ticket, "--output", outside_plan,
           "--markdown", ticket / "inside-plan.md").returncode != 0
assert outside_plan.read_text(encoding="utf-8") == "PLAN-SENTINEL\n"

# 任一目标预检失败，另一目标不得先被替换。
preflight_json = ticket / "preflight.json"
preflight_json.write_text("PREFLIGHT-SENTINEL\n", encoding="utf-8")
blocker = ticket / "not-a-directory"
blocker.write_text("BLOCKER\n", encoding="utf-8")
assert run("plan", "--ticket-dir", ticket, "--output", preflight_json,
           "--markdown", blocker / "plan.md").returncode != 0
assert preflight_json.read_text(encoding="utf-8") == "PREFLIGHT-SENTINEL\n"

# .git 路径和指向控制面源文件的 symlink/hardlink alias 一律拒绝。
git_dir = ticket / ".git"
git_dir.mkdir()
assert run("plan", "--ticket-dir", ticket, "--output", git_dir / "plan.json",
           "--markdown", ticket / "git-reject.md").returncode != 0

metadata = ticket / ".ico_metadata.json"
metadata_hash = __import__("hashlib").sha256(metadata.read_bytes()).hexdigest()
metadata_hardlink = ticket / "metadata-hardlink.json"
os.link(metadata, metadata_hardlink)
assert run("plan", "--ticket-dir", ticket, "--output", metadata_hardlink,
           "--markdown", ticket / "metadata-hardlink.md").returncode != 0
assert __import__("hashlib").sha256(metadata.read_bytes()).hexdigest() == metadata_hash

metadata_symlink = ticket / "metadata-symlink.json"
metadata_symlink.symlink_to(metadata)
assert run("plan", "--ticket-dir", ticket, "--output", metadata_symlink,
           "--markdown", ticket / "metadata-symlink.md").returncode != 0

index = home / ".claude/icode_data/index.json"
index_hardlink = report_root / "index-hardlink.json"
os.link(index, index_hardlink)
assert run("pending", "--project", project, "--output", index_hardlink,
           "--markdown", report_root / "index-hardlink.md").returncode != 0

manifest = ticket / "evidence_manifest.json"
manifest.write_text(json.dumps({"schema_version": 1}) + "\n", encoding="utf-8")
manifest_hardlink = ticket / "manifest-hardlink.json"
os.link(manifest, manifest_hardlink)
assert run("plan", "--ticket-dir", ticket, "--output", manifest_hardlink,
           "--markdown", ticket / "manifest-hardlink.md").returncode != 0

# 故障注入第二次 replace；第一份目标必须回滚到原内容。
spec = importlib.util.spec_from_file_location("verification_debt_txn", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
pair_json = ticket / "pair.json"
pair_md = ticket / "pair.md"
pair_json.write_text("PAIR-JSON-OLD\n", encoding="utf-8")
pair_md.write_text("PAIR-MD-OLD\n", encoding="utf-8")
real_replace = module.os.replace
replace_calls = 0

def fail_second_replace(source, target):
    global replace_calls
    replace_calls += 1
    if replace_calls == 2:
        raise OSError("injected second replace failure")
    return real_replace(source, target)

module.os.replace = fail_second_replace
try:
    module.write_reports({"schema_version": 1}, str(pair_json), str(pair_md),
                         lambda _report: "PAIR-MD-NEW\n")
except OSError:
    pass
else:
    raise AssertionError("second replace failure was not propagated")
finally:
    module.os.replace = real_replace
assert pair_json.read_text(encoding="utf-8") == "PAIR-JSON-OLD\n"
assert pair_md.read_text(encoding="utf-8") == "PAIR-MD-OLD\n"
PY
then
  ok "输出 root、源文件别名与双文件事务边界"
else
  bad "输出 root、源文件别名或双文件事务边界失守"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
