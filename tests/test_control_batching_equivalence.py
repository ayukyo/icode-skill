"""Batched control-plane writes must reproduce the single-call receipts exactly.

Guard for the planned consolidations (``artifact --paths-json`` and a
``closeout`` subcommand). The acceptance contract these tests encode:

* a batched registration emits **one ``artifact_written`` event per member**,
  with the same ``step``/``attempt``/``output``/``sha256``/``size``/relative
  path as the equivalent single call, in the same order;
* ``closeout`` emits the same event sequence as the manual calls it replaces
  (artifact registration, ``before_transition`` check, ``step finish``).

Nothing here relaxes a gate: the comparison runs on the real event ledger, so a
batch that skips a hash, an output port, or a boundary check fails.

``artifact --paths-json`` is implemented, so its cases run; ``closeout`` is not,
and its case stays gated behind an option probe so it activates by itself when
the command lands. The fixture terminates the step as ``blocked`` on purpose -
that keeps the receipt set deterministic without having to synthesise a complete
inspection report, and the comparison target is the ledger sequence, not the
outcome.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = ("src/a.py", "src/b.py", "src/c.py")
STEP = "code"
ATTEMPT = "batch-equivalence"


def control_module():
    spec = importlib.util.spec_from_file_location("batched_equivalence_control",
                                                  ROOT / "tools/icode_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subcommand_options(name):
    """Option strings of one subcommand, or None when it does not exist yet."""
    parser = control_module().build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return {option for inner in action.choices[name]._actions
                    for option in inner.option_strings}
    return None


BATCH_ARTIFACT = "--paths-json" in (subcommand_options("artifact") or set())
CLOSEOUT_AVAILABLE = subcommand_options("closeout") is not None


def run(out_dir, *argv):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("ICODE_CONTROL_TEST_MODE", None)
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/icode_control.py"), *argv, "--dir", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=180, check=False)
    assert result.returncode == 0, f"{argv} failed: {result.stdout}{result.stderr}"
    return json.loads(result.stdout)


def ledger(out_dir):
    rows = []
    for line in (Path(out_dir) / ".ico_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(row["value"] if "value" in row and "kind" in row else row)
    return rows


def normalize(payload, workspace):
    path = Path(payload.get("path", ""))
    try:
        relative = path.resolve().relative_to(Path(workspace).resolve()).as_posix()
    except (OSError, ValueError):
        relative = path.name
    normalized = {key: payload.get(key)
                  for key in ("step", "attempt", "output", "scope", "sha256", "size")}
    normalized["path"] = relative
    return normalized


def receipts(out_dir, workspace):
    """Normalized ``artifact_written`` receipts in ledger order."""
    rows = []
    for event in ledger(out_dir):
        if event.get("event_type") != "artifact_written":
            continue
        payload = event.get("payload") or {}
        if payload.get("step") != STEP or payload.get("attempt") != ATTEMPT:
            continue
        rows.append(normalize(payload, workspace))
    return rows


def event_sequence(out_dir):
    names = {"artifact_written", "gate_checked", "step_finished"}
    return [(event["event_type"], (event.get("payload") or {}).get("boundary"))
            for event in ledger(out_dir) if event.get("event_type") in names]


def compare(expected, actual):
    """Raise with a readable diff when two receipt sequences disagree."""
    problems = []
    if len(expected) != len(actual):
        problems.append(f"receipt count {len(expected)} != {len(actual)}")
    for index, (left, right) in enumerate(zip(expected, actual)):
        changed = {key: (left.get(key), right.get(key))
                   for key in set(left) | set(right) if left.get(key) != right.get(key)}
        if changed:
            problems.append(f"receipt[{index}] differs: {changed}")
    assert not problems, "batched writes are not equivalent to single calls: " + "; ".join(problems)


def build_ticket(root, workspace_name="ws", ticket_id="batch-fixture-1", acting=None):
    """Create a ticket with ``FILES`` staged, then let ``acting`` drive the step."""
    workspace = Path(root) / workspace_name
    out_dir = workspace / ".icode_output/.icode_output_1"
    out_dir.mkdir(parents=True)
    for name in FILES:
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"value = {name!r}\n", encoding="utf-8")
    seed = {"mode": "full", "code_files": list(FILES),
            "semantic_decisions": [], "requirement_deltas": []}
    run(out_dir, "create", "--birth", "plan", "--ticket-id", ticket_id,
        "--requirement", "batch equivalence fixture", "--metadata-json", json.dumps(seed))
    # Required input port for the code step; must be written after creation.
    (out_dir / "03_plan_final.md").write_text("# fixture final plan\n", encoding="utf-8")
    run(out_dir, "step", "--step", STEP, "--phase", "start", "--attempt", ATTEMPT,
        "--request", f"start-{ticket_id}")
    run(out_dir, "step", "--step", STEP, "--phase", "check", "--attempt", ATTEMPT,
        "--boundary", "before_write", "--request", f"check-{ticket_id}")
    if acting is not None:
        acting(out_dir, workspace)
    return out_dir, workspace


def register_single(out_dir, workspace):
    for name in FILES:
        run(out_dir, "artifact", "--step", STEP, "--attempt", ATTEMPT, "--scope", "workspace",
            "--path", name, "--request", f"art-{name.replace('/', '-')}")


def register_batch(out_dir, workspace):
    run(out_dir, "artifact", "--step", STEP, "--attempt", ATTEMPT, "--scope", "workspace",
        "--paths-json", json.dumps(list(FILES)), "--request", "art-batch")


def finish_step(out_dir, workspace):
    run(out_dir, "step", "--step", STEP, "--phase", "check", "--attempt", ATTEMPT,
        "--boundary", "before_transition", "--request", "check-transition")
    run(out_dir, "step", "--step", STEP, "--phase", "finish", "--attempt", ATTEMPT,
        "--outcome", "blocked", "--evidence", "fixture termination", "--request", "finish")


def manual_sequence(out_dir, workspace):
    register_single(out_dir, workspace)
    finish_step(out_dir, workspace)


def batched_sequence(out_dir, workspace):
    register_batch(out_dir, workspace)
    finish_step(out_dir, workspace)


def run_expect_failure(out_dir, *argv):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("ICODE_CONTROL_TEST_MODE", None)
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/icode_control.py"), *argv, "--dir", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=180, check=False)
    assert result.returncode != 0, f"expected a refusal for {argv}: {result.stdout}"
    return json.loads(result.stdout)


def artifact_args(*extra):
    return ("artifact", "--step", STEP, "--attempt", ATTEMPT, "--scope", "workspace", *extra)


def test_compare_detects_a_changed_receipt():
    """The comparator must be able to fail, otherwise the guard is decorative."""
    left = [{"output": "code_files", "sha256": "a" * 64, "size": 1}]
    with pytest.raises(AssertionError) as error:
        compare(left, [{"output": "code_files", "sha256": "b" * 64, "size": 1}])
    assert "sha256" in str(error.value)
    with pytest.raises(AssertionError) as error:
        compare(left, [])
    assert "receipt count" in str(error.value)


def test_single_path_registration_is_deterministic(tmp_path):
    """Keeps the fixture honest while the batched cases are still dormant."""
    first, first_workspace = build_ticket(tmp_path / "one", acting=manual_sequence)
    second, second_workspace = build_ticket(tmp_path / "two", acting=manual_sequence)
    first_receipts = receipts(first, first_workspace)
    assert len(first_receipts) == len(FILES)
    compare(first_receipts, receipts(second, second_workspace))
    assert event_sequence(first) == event_sequence(second)


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
def test_artifact_batch_matches_single_path_calls(tmp_path):
    single, single_workspace = build_ticket(tmp_path / "single", acting=manual_sequence)
    batched, batched_workspace = build_ticket(tmp_path / "batch", acting=batched_sequence)
    compare(receipts(single, single_workspace), receipts(batched, batched_workspace))
    assert event_sequence(single) == event_sequence(batched)


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
def test_batch_refuses_mixed_path_and_paths_json(tmp_path):
    out_dir, _ = build_ticket(tmp_path / "mixed")
    result = run_expect_failure(out_dir, *artifact_args(
        "--path", FILES[0], "--paths-json", json.dumps(list(FILES))))
    assert result["gate_id"] == "artifact_batch"


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
@pytest.mark.parametrize("members", [
    ["src/a.py", "src/a.py"],
    ["src/../src/a.py"],
    ["/etc/passwd"],
    ["src\\a.py"],
    ["src/.git/config"],
    [],
    [f"src/f{i}.py" for i in range(257)],
])
def test_batch_refuses_non_canonical_members(tmp_path, members):
    out_dir, workspace = build_ticket(tmp_path / "canonical")
    result = run_expect_failure(out_dir, *artifact_args("--paths-json", json.dumps(members)))
    assert result["gate_id"] == "artifact_batch"
    assert receipts(out_dir, workspace) == []


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
def test_batch_is_all_or_nothing_on_a_missing_member(tmp_path):
    out_dir, workspace = build_ticket(tmp_path / "atomic")
    result = run_expect_failure(out_dir, *artifact_args(
        "--paths-json", json.dumps([FILES[0], "src/missing.py"])))
    assert result["gate_id"] == "artifact_exists"
    assert receipts(out_dir, workspace) == []


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
def test_batch_conflict_aborts_before_any_append(tmp_path):
    out_dir, workspace = build_ticket(tmp_path / "conflict")
    run(out_dir, *artifact_args("--path", FILES[1], "--request", "batch#1"))
    (workspace / FILES[1]).write_text("value = 'changed'\n", encoding="utf-8")
    result = run_expect_failure(out_dir, *artifact_args(
        "--paths-json", json.dumps(list(FILES)), "--request", "batch"))
    assert result["gate_id"] == "idempotency_conflict"
    assert [row["path"] for row in receipts(out_dir, workspace)] == [FILES[1]]


@pytest.mark.skipif(not BATCH_ARTIFACT,
                    reason="artifact --paths-json not implemented yet; activates with the option")
def test_batch_replay_is_idempotent(tmp_path):
    out_dir, workspace = build_ticket(tmp_path / "replay")
    payload = json.dumps(list(FILES))
    first = run(out_dir, *artifact_args("--paths-json", payload, "--request", "batch"))
    assert first["count"] == len(FILES)
    assert all(item["already_applied"] is False for item in first["artifacts"])
    before = len(receipts(out_dir, workspace))
    second = run(out_dir, *artifact_args("--paths-json", payload, "--request", "batch"))
    assert all(item["already_applied"] is True for item in second["artifacts"])
    assert len(receipts(out_dir, workspace)) == before == len(FILES)


def test_manual_closeout_sequence_is_repeatable(tmp_path):
    """Documents the sequence ``closeout`` has to reproduce, with no closeout present."""
    first, _ = build_ticket(tmp_path / "manual-one", acting=manual_sequence)
    second, _ = build_ticket(tmp_path / "manual-two", acting=manual_sequence)
    assert event_sequence(first) == event_sequence(second)


@pytest.mark.skipif(not CLOSEOUT_AVAILABLE,
                    reason="closeout subcommand not implemented yet; activates with the command")
def test_closeout_matches_the_manual_sequence(tmp_path):
    def consolidated(out_dir, workspace):
        run(out_dir, "closeout", "--step", STEP, "--attempt", ATTEMPT,
            "--paths-json", json.dumps(list(FILES)), "--scope", "workspace",
            "--outcome", "blocked", "--evidence", "fixture termination",
            "--request", "closeout")

    reference, _ = build_ticket(tmp_path / "reference", acting=manual_sequence)
    candidate, _ = build_ticket(tmp_path / "candidate", acting=consolidated)
    assert event_sequence(reference) == event_sequence(candidate)