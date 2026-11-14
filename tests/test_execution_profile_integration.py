"""Production admission and frozen source bindings for optional instruction views."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from test_fast_execution_contract import PHASES, REQUIREMENT_SOURCES

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "mcp/workflow-gate/gates.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog(root=ROOT):
    return json.loads((root / CATALOG).read_text(encoding="utf-8"))


def fast_metadata():
    return {"mode": "fast", "risk_profile": {"policy_version": 2,
        "requested_mode": "fast", "effective_mode": "fast"}}


def build(root, data=None, phase="plan"):
    return load_module("integration_profile", root / "tools/fast_policy.py").build_execution_profile(
        fast_metadata(), phase, data if data is not None else catalog(root), root)


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    base = tmp_path_factory.mktemp("execution-profile-core")
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/build_skill_distribution.py"),
        "--source", str(ROOT), "--output", str(base / "icode"), "--profile", "core",
        "--components-output", str(base / "components")], capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return base / "icode/skills/icode"


@pytest.mark.parametrize("source", ["checkout", "core"])
def test_frozen_catalog_matches_actual_sources_and_all_responsibilities(source, core):
    root = ROOT if source == "checkout" else core
    data = catalog(root)
    config = data["execution_profiles"]
    assert set(config["phases"]) == set(PHASES)
    pins = config["source_sha256"]
    assert len(pins) <= 64 and sum((root / p).stat().st_size for p in pins) <= 4 * 1024 * 1024
    assert "steps/fast.md" in pins
    # Literal reviewed catalog hashes are authoritative; never rewrite expected
    # pins from current source bytes in a fixture to conceal a stale binding.
    for path, expected in pins.items():
        text = (root / path).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == expected, path
    for phase, filename in PHASES.items():
        record = config["phases"][phase]
        assert record["document"] == "steps/fast.md" and record["anchor"] == f"fast-{phase}"
        assert record["requirements"] == [dict(id=identity, source=f"steps/{filename}", anchor=anchor)
                                            for identity, anchor in REQUIREMENT_SOURCES[phase]]
        assert build(root, data, phase)["state"] == "compact", phase
    if source == "core":
        assert sum(path.is_file() for path in root.rglob("*")) <= 200
        assert config == catalog()["execution_profiles"]


def test_fast_shared_and_conditional_core_markdown_dependencies_are_bound(core):
    pins = catalog()["execution_profiles"]["source_sha256"]
    text = (ROOT / "steps/fast.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)]+)\)", text):
        target = target.split("#", 1)[0]
        if not target:
            continue
        path = (ROOT / "steps" / target).resolve()
        if path.is_relative_to(ROOT) and path.suffix == ".md":
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith(("steps/", "references/")) and (core / relative).is_file():
                assert relative in pins, relative
    for phase, record in catalog()["execution_profiles"]["phases"].items():
        refs = {(r["source"], r["anchor"]) for r in record["shared_refs"]}
        assert ("steps/fast.md", "fast-shared-contract") in refs
        assert any(path == "references/execution_model.md" and anchor.startswith("2-") for path, anchor in refs)
        assert any(path == "references/thinking_core.md" for path, _ in refs)
        if phase in {"review", "code", "deepcheck", "audit"}:
            assert any(path == "references/inspection_worklist.md" for path, _ in refs)
            assert any(path == "references/inspection_evidence.md" for path, _ in refs)


@pytest.mark.parametrize("mutation", ["version", "path", "type", "missing", "anchor", "hash"])
def test_real_catalog_rejects_invalid_optional_configuration(mutation):
    data = catalog()
    config = data["execution_profiles"]
    if mutation == "version":
        config["schema_version"] = 2
    elif mutation == "path":
        config["source_sha256"]["../steps/fast.md"] = "0" * 64
    elif mutation == "type":
        config["phases"]["plan"]["requirements"] = "not-a-list"
    elif mutation == "missing":
        del config["source_sha256"]["steps/01_plan.md"]
    elif mutation == "anchor":
        config["phases"]["plan"]["requirements"][0]["anchor"] = "missing-original-section"
    else:
        config["source_sha256"]["steps/01_plan.md"] = "0" * 64
    result = build(ROOT, data)
    assert result["state"] == "unavailable" and result["effective_mode"] == "fast"
    assert "rules" not in result


@pytest.mark.parametrize("mutation", ["changed-source", "missing-marker", "duplicate-marker"])
def test_actual_pinned_source_and_markers_fail_closed(core, tmp_path, mutation):
    root = tmp_path / "skill"
    shutil.copytree(core, root)
    data = catalog(root)
    path = root / ("steps/01_plan.md" if mutation == "changed-source" else "steps/fast.md")
    text = path.read_text(encoding="utf-8")
    if mutation == "changed-source":
        text += "\nA changed normative responsibility.\n"
    elif mutation == "missing-marker":
        text = text.replace("<!-- ICODE:FAST:plan:BEGIN -->", "")
    else:
        text += "\n<!-- ICODE:FAST:plan:BEGIN -->\n"
    path.write_text(text, encoding="utf-8")
    # Keep frozen pins for drift; deliberately update the marker mutation pin
    # only to prove the structural marker guard independently of versioning.
    if mutation != "changed-source":
        data["execution_profiles"]["source_sha256"]["steps/fast.md"] = hashlib.sha256(text.encode()).hexdigest()
    assert build(root, data)["state"] == "unavailable"


@pytest.fixture
def native(tmp_path):
    def create(mode="fast", root=ROOT):
        out = tmp_path / mode / ".icode_output/.icode_output_1"
        out.parents[1].mkdir(parents=True)
        content = "# Complete isolated plan\n"
        seed = {"mode": "fast" if mode == "legacy" else mode, "semantic_decisions": [], "requirement_deltas": []}
        if mode == "fast":
            flags = list(catalog()["fast_risk_upgrade"]["risk_flags"]) + catalog()["fast_risk_upgrade"]["policy_v2"]["full_flags"]
            scope = {"summary": "Bounded profile fixture", "source_step": "plan", "at": "2026-11-14T00:00:00Z"}
            seed.update(scope_contract=scope, risk_profile={"policy_version": 2, "requested_mode": "fast",
                "effective_mode": "fast", "override": False, "triggers": [], "risk_flags": dict.fromkeys(flags, False),
                "classification": {"scope": "bounded", "scope_digest": hashlib.sha256(json.dumps(scope,
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
                    "evidence_refs": [{"scope": "ticket", "path": "01_plan.md", "sha256": hashlib.sha256(content.encode()).hexdigest()}]}})
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        env.pop("ICODE_CONTROL_TEST_MODE", None)

        def run(*args, ok=True):
            proc = subprocess.run([sys.executable, "-B", str(root / "tools/icode_control.py"),
                *map(str, args), "--dir", str(out)], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            if ok:
                assert proc.returncode == 0, proc.stdout + proc.stderr
            return proc

        if mode == "legacy":
            # Authenticated historical vNext fixture, not a new legacy birth:
            # production create correctly requires policy2 for every new run.
            ctl = load_module("historical_profile_control", root / "tools/icode_control.py")
            out.mkdir(parents=True)
            seed.update(schema_version=3, ticket_id="profile-integration", requirement="Historical fixture",
                created_at="2026-11-13T00:00:00Z", status="init_in_progress", completed_steps=[], indexed=False,
                project_path=str(out.parents[1]), workflow_gate_schema_version=1, thinking_gate_schema_version=1,
                mcp_gate_schema_version=1)
            seed["risk_profile"] = {"requested_mode": "fast", "effective_mode": "fast",
                "risk_flags": dict.fromkeys(catalog()["fast_risk_upgrade"]["risk_flags"], False),
                "override": False, "triggers": []}
            ctl.commit_metadata_and_event(out, None, seed, "ticket_created", {"birth_kind": "plan",
                "execution_contract_version": 1, "requirement_summary": "Historical fixture"})
        else:
            run("create", "--ticket-id", "profile-integration", "--birth", "plan", "--requirement", "Isolated production gate fixture",
                "--metadata-json", json.dumps(seed))
        (out / "01_plan.md").write_bytes(content.encode())
        return out, run
    return create


@pytest.mark.parametrize("mode,state", [("fast", "compact"), ("full", "standard"), ("legacy", "standard")])
def test_native_start_and_exact_replay_project_only_after_real_admission(native, mode, state):
    out, run = native(mode)
    args = ("step", "--step", "plan", "--phase", "start", "--request", "start-profile")
    first = json.loads(run(*args).stdout)
    profile = first["execution_profile"]
    assert profile["state"] == state
    assert profile["effective_mode"] == ("full" if mode == "full" else "fast")
    before = (out / ".ico_events.jsonl").read_bytes()
    replay = json.loads(run(*args).stdout)
    assert replay["execution_profile"] == profile and replay["already_applied"]
    assert (out / ".ico_events.jsonl").read_bytes() == before
    rows = [json.loads(line) for line in before.splitlines()]
    rows = [row["value"] if row.get("storage_version") == 2 else row for row in rows]
    assert len([row for row in rows if row["event_type"] == "step_started"]) == 1
    assert all("execution_profile" not in row["payload"] and "rules" not in row["payload"] for row in rows)
    run("validate")


def test_unavailable_optional_profile_keeps_effective_fast_and_standard_rule_route(core, tmp_path, native):
    root = tmp_path / "stale-core"
    shutil.copytree(core, root)
    (root / "steps/01_plan.md").write_text("# Changed source\n", encoding="utf-8")
    _, run = native(root=root)
    reply = json.loads(run("step", "--step", "plan", "--phase", "start", "--request", "stale-profile").stdout)
    profile = reply["execution_profile"]
    assert profile["state"] == "unavailable" and profile["effective_mode"] == "fast"
    assert "rules" not in profile
    assert profile["fallback"]["document"] == "steps/01_plan.md"
    assert profile["fallback"]["read_applicable_body_and_shared_rules"] is True
    assert profile["fallback"]["ignore_compact_navigation"] is True


def test_failed_ports_return_no_instruction_view_and_no_step_event(native):
    out, run = native()
    (out / "01_plan.md").unlink()
    proc = run("step", "--step", "review", "--phase", "start", ok=False)
    assert proc.returncode != 0 and "execution_profile" not in proc.stdout
    assert b'"step_started"' not in (out / ".ico_events.jsonl").read_bytes()


def enter_review(out, run):
    trace = dict(schema_version=1, ticket_id="profile-integration", step="plan", tier="L2", default_tier="L2",
        triggers=[], mechanism="sequential-thinking", attempted=True, result="success", degraded_reason=None,
        over_invoked=False, at="2026-11-14T00:00:00Z")
    (out / ".thinking_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    run("step", "--step", "plan", "--phase", "start", "--attempt", "prepare-review")
    run("step", "--step", "plan", "--phase", "check", "--attempt", "prepare-review", "--boundary", "before_write")
    run("artifact", "--step", "plan", "--path", "01_plan.md", "--attempt", "prepare-review")
    run("step", "--step", "plan", "--phase", "check", "--attempt", "prepare-review", "--boundary", "before_transition")
    run("step", "--step", "plan", "--phase", "finish", "--attempt", "prepare-review", "--outcome", "success", "--evidence", "01_plan.md")
    run("transition", "--to", "plan_done")
    run("transition", "--to", "review_in_progress")


def test_classified_fast_review_start_uses_real_admission_and_rejects_evidence_drift(native):
    out, run = native()
    enter_review(out, run)
    reply = json.loads(run("step", "--step", "review", "--phase", "start", "--request", "classified-review").stdout)
    assert reply["execution_profile"]["state"] == "compact"
    before = (out / ".ico_events.jsonl").read_bytes()
    (out / "01_plan.md").write_text("# Evidence drift after classification\n", encoding="utf-8")
    proc = run("step", "--step", "review", "--phase", "start", "--request", "classified-review", ok=False)
    assert proc.returncode != 0 and "fast_risk_admission" in proc.stdout + proc.stderr
    assert "execution_profile" not in proc.stdout
    assert (out / ".ico_events.jsonl").read_bytes() == before


def test_inspection_ci_runs_all_profile_and_recipe_contracts():
    text = (ROOT / ".github/workflows/inspection-software.yml").read_text(encoding="utf-8")
    for filename in ("test_execution_profile.py", "test_execution_profile_integration.py", "test_fast_execution_contract.py", "test_execution_closeout_recipe.py"):
        assert f"tests/{filename}" in text
    assert "tests/test_execution*" in text


@pytest.mark.parametrize("phase", ["init", "readme", "patch", "deploy"])
def test_unsupported_profile_fallback_does_not_invent_step_documents(phase):
    ctl = load_module("unsupported_profile_control", ROOT / "tools/icode_control.py")
    result = ctl.step_execution_profile({"mode": "full"}, phase)
    assert result["state"] == "unavailable" and result["effective_mode"] == "full"
    fallback = result["fallback"]
    assert fallback["route"] == "existing_step_rules"
    if fallback.get("document"):
        assert (ROOT / fallback["document"]).is_file()


def test_exact_start_replay_revalidates_optional_source_without_persisting_it(core, tmp_path, native):
    root = tmp_path / "replay-core"
    shutil.copytree(core, root)
    out, run = native(root=root)
    args = ("step", "--step", "plan", "--phase", "start", "--request", "optional-replay")
    assert json.loads(run(*args).stdout)["execution_profile"]["state"] == "compact"
    events = (out / ".ico_events.jsonl").read_bytes()
    (root / "references/thinking_core.md").write_text("# Changed shared contract\n", encoding="utf-8")
    replay = json.loads(run(*args).stdout)
    assert replay["already_applied"] and replay["execution_profile"]["state"] == "unavailable"
    assert (out / ".ico_events.jsonl").read_bytes() == events


@pytest.mark.parametrize("phase,filename", [("readme", "07_readme.md"), ("patch", "08_patch.md")])
def test_auxiliary_native_start_and_replay_keep_real_authoritative_document(native, phase, filename):
    out, run = native("full")
    for name in ("03_plan_final.md", "06_audit.md"):
        (out / name).write_text("# Real isolated input\n", encoding="utf-8")
    args = ("step", "--step", phase, "--phase", "start", "--request", "aux-start")
    first = json.loads(run(*args).stdout)
    fallback = first["execution_profile"]["fallback"]
    assert fallback["route"] == "existing_step_rules" and fallback["document"] == f"steps/{filename}"
    assert (ROOT / fallback["document"]).is_file()
    before = (out / ".ico_events.jsonl").read_bytes()
    replay = json.loads(run(*args).stdout)
    assert replay["already_applied"] and replay["execution_profile"] == first["execution_profile"]
    assert (out / ".ico_events.jsonl").read_bytes() == before
