import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tools" / "icode_control.py"


def control(*args):
    proc = subprocess.run(
        [sys.executable, str(CONTROL), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def make_ticket(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    out_dir = project / ".icode_output" / ".icode_output_1"
    rc, payload = control(
        "create", "--dir", out_dir, "--ticket-id", "POLICY-1",
        "--requirement", "policy contract", "--birth", "init",
    )
    assert rc == 0, payload
    return project.resolve(), out_dir.resolve()


def test_action_policy_is_control_plane_truth_and_revision_guard(tmp_path):
    project, out_dir = make_ticket(tmp_path)
    rc, policy = control("action-policy", "--dir", out_dir)

    assert rc == 0 and policy["ok"] is True
    assert policy["schema_version"] == 1
    assert policy["ticket_id"] == "POLICY-1"
    assert policy["status"] == "init_in_progress"
    assert policy["recommended_action"] == "plan"
    assert "plan" in policy["allowed_actions"]
    assert "review" not in policy["allowed_actions"]
    assert policy["revision"]["event_count"] == 1
    assert len(policy["revision"]["token"]) == 64
    assert Path(policy["execution_root"]) == project
    assert policy["open_steps"] == {}
    assert policy["open_operations"] == {}
    assert policy["open_agents"] == {}

    rc, allowed = control(
        "action-policy", "--dir", out_dir, "--action", "plan",
        "--expected-revision", policy["revision"]["token"],
    )
    assert rc == 0 and allowed["action_allowed"] is True

    rc, denied = control(
        "action-policy", "--dir", out_dir, "--action", "review",
        "--expected-revision", policy["revision"]["token"],
    )
    assert rc == 1 and denied["gate_id"] == "action_policy"

    rc, stale = control(
        "action-policy", "--dir", out_dir, "--action", "plan",
        "--expected-revision", "0" * 64,
    )
    assert rc == 1 and stale["gate_id"] == "revision_mismatch"


def test_create_next_allocates_indexes_and_resumes_idempotently(tmp_path):
    project = tmp_path / "sample"
    project.mkdir()
    index = tmp_path / "index.json"

    rc, created = control(
        "create-next", "--workspace", project, "--index", index,
        "--requirement", "first UI ticket", "--request-id", "ui-create-1",
    )
    assert rc == 0, created
    assert created["ticket_id"] == "sample-1"
    assert created["status"] == "init_in_progress"
    assert created["indexed"] is True
    out_dir = project / ".icode_output" / ".icode_output_1"
    assert (out_dir / ".ico_metadata.json").is_file()

    rc, replay = control(
        "create-next", "--workspace", project, "--index", index,
        "--requirement", "first UI ticket", "--request-id", "ui-create-1",
    )
    assert rc == 0, replay
    assert replay["already_applied"] is True
    assert replay["ticket_id"] == "sample-1"
    entries = json.loads(index.read_text(encoding="utf-8"))["tickets"]
    assert [item["ticket_id"] for item in entries] == ["sample-1"]


def test_create_next_uses_project_hash_on_cross_project_id_collision(tmp_path):
    first = tmp_path / "one" / "same"
    second = tmp_path / "two" / "same"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    index = tmp_path / "index.json"

    rc, first_created = control(
        "create-next", "--workspace", first, "--index", index,
        "--requirement", "first", "--request-id", "ui-first",
    )
    assert rc == 0, first_created
    rc, second_created = control(
        "create-next", "--workspace", second, "--index", index,
        "--requirement", "second", "--request-id", "ui-second",
    )
    assert rc == 0, second_created
    assert first_created["ticket_id"] == "same-1"
    assert second_created["ticket_id"].startswith("same-1-")
    assert len(second_created["ticket_id"]) == len("same-1-") + 4


def test_exclusive_agent_spawn_is_cross_process_lock(tmp_path):
    _, out_dir = make_ticket(tmp_path)
    common = (
        "--dir", out_dir,
        "--task-scope", "UI /icode plan",
        "--expected-artifact", "ICODE step result",
        "--evidence-boundary", "selected ticket and project",
        "--join-condition", "host process terminal receipt",
        "--backend", "codex-cli", "--model", "host-default",
        "--capability", "text", "--capability", "tools",
        "--exclusive-key", "ui-step-run",
    )
    rc, first = control(
        "record-agent-spawn", *common, "--request-id", "ui-1:spawn")
    assert rc == 0 and first["spawn"]["exclusive_key"] == "ui-step-run"

    rc, second = control(
        "record-agent-spawn", *common, "--request-id", "ui-2:spawn")
    assert rc == 1 and second["gate_id"] == "agent_exclusive_lock"

    rc, policy = control("action-policy", "--dir", out_dir)
    assert rc == 0
    assert "plan" not in policy["allowed_actions"]
    assert policy["blocked_reason"] == "open_execution"

    rc, result = control(
        "record-agent-result", "--dir", out_dir,
        "--spawn-id", first["spawn"]["spawn_id"],
        "--result", "stopped", "--adopted", "no",
        "--adoption-reason", "test cleanup", "--evidence-ref", "test:cleanup",
        "--summary", "stopped", "--request-id", "ui-1:result",
    )
    assert rc == 0, result

    rc, third = control(
        "record-agent-spawn", *common, "--request-id", "ui-3:spawn")
    assert rc == 0, third


def test_closed_and_debug_tickets_do_not_receive_main_actions(tmp_path):
    _, out_dir = make_ticket(tmp_path)
    metadata_path = out_dir / ".ico_metadata.json"
    # 创建 debug 工单用于验证隔离；不直接篡改已有事件链。
    debug_dir = tmp_path / "debug-project" / ".icode_output" / ".debug" / ".icode_output_1"
    debug_dir.parents[2].mkdir(parents=True)
    rc, payload = control(
        "create", "--dir", debug_dir, "--ticket-id", "DEBUG-1",
        "--requirement", "debug only", "--birth", "debug-log",
    )
    assert rc == 0, payload
    rc, policy = control("action-policy", "--dir", debug_dir)
    assert rc == 0
    assert policy["allowed_actions"] == ["log", "status"]
    assert policy["recommended_action"] == "log"
    assert "code" not in policy["allowed_actions"]
    assert metadata_path.is_file()
