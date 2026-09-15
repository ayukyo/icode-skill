#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
cp -a "$ROOT/demo" "$TMP_ROOT/demo"

HOME="$TMP_ROOT/home" PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT" "$TMP_ROOT/demo" <<'PY'
import hashlib
import json
import os
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
demo = pathlib.Path(sys.argv[2])
tool = root / "tools/icode_crosscheck.py"
ticket = demo / ".icode_output/.icode_output_4"
index = pathlib.Path(os.environ["HOME"]) / ".claude/icode_data/index.json"
index.parent.mkdir(parents=True)
index.write_text('{"schema_version":3,"tickets":[]}\n', encoding="utf-8")

def digest_tree(path):
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest.update(str(item.relative_to(path)).encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()

def run(*args, expected=0):
    proc = subprocess.run(
        [sys.executable, str(tool), *map(str, args)], cwd=root,
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr
    return json.loads(proc.stdout)

def payload(number, status="new"):
    return {
        "schema_version": 1,
        "target_ticket_id": "demo-4",
        "round": number,
        "reviewed_at": f"2026-09-14T09:{number:02d}:00+08:00",
        "verdict": "pass_with_suggestions",
        "evidence_boundary": "demo legacy 工单静态模拟；未执行实机验证",
        "summary": "设计与代码链路可读，建议补充一个边界说明",
        "findings": [{
            "finding_id": "DEMO-CC-1",
            "title": "补充边界说明",
            "severity": "suggestion",
            "status": status,
            "category": "documentation",
            "evidence": ["03_plan_final.md"],
            "analysis": "边界可进一步显式化",
            "recommendation": "在后续 patch 中按需补充，不自动修改",
            "requires_change": False,
        }],
    }

target_before = digest_tree(ticket)
index_before = index.read_bytes()

# Round 1: legacy completed ticket, explicit artifact path, full success.
started = run("start", "--workspace", demo, ticket / "03_plan_final.md")
directory = pathlib.Path(started["crosscheck_dir"])
assert directory.parent == demo / ".icode_output/.crosscheck"
(directory / "crosscheck_round_1.fresh.json").write_text(
    json.dumps(payload(1), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("freeze", "--dir", directory, "--round", 1)
(directory / "crosscheck_round_1.json").write_text(
    json.dumps(payload(1), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("finish", "--dir", directory, "--round", 1)
assert digest_tree(ticket) == target_before
assert index.read_bytes() == index_before

# Round 2: modify the copied ticket during review; finish must preserve stale_input.
run("start", "--workspace", demo, "--ticket", "demo-4")
(directory / "crosscheck_round_2.fresh.json").write_text(
    json.dumps(payload(2), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("freeze", "--dir", directory, "--round", 2)
(directory / "crosscheck_round_2.json").write_text(
    json.dumps(payload(2, "still_present"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
(ticket / "03_plan_final.md").write_text(
    (ticket / "03_plan_final.md").read_text(encoding="utf-8") + "\nDemo drift.\n",
    encoding="utf-8",
)
stale_baseline = digest_tree(ticket)
stale = run("finish", "--dir", directory, "--round", 2, expected=1)
assert stale["state"] == "stale_input"
assert digest_tree(ticket) == stale_baseline

# Round 3: new stable review compares against the most recent completed round (Round 1).
third = run("start", "--workspace", demo, "--ticket", "demo-4")
assert third["round"] == 3
(directory / "crosscheck_round_3.fresh.json").write_text(
    json.dumps(payload(3), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
frozen = run("freeze", "--dir", directory, "--round", 3)
assert frozen["previous_round"].endswith("crosscheck_round_1.json")
(directory / "crosscheck_round_3.json").write_text(
    json.dumps(payload(3, "still_present"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("finish", "--dir", directory, "--round", 3)
validated = run("validate", "--dir", directory)
assert validated["rounds"] == 3 and validated["completed_rounds"] == 2
assert not list(directory.rglob(".ico_metadata.json"))
assert index.read_bytes() == index_before
print("PASS demo legacy multi-round/stale/resume/zero-write simulation")
PY
