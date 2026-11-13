"""The production metadata reader inventory must consume logical objects."""
import copy
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent_runtime"))


def module(relative):
    name = "reader_test_" + relative.replace("/", "_").replace(".", "_")
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
    return sys.modules[name]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot():
    candidate = module("tools/icode_control.py").candidate_module()
    value = dict(version=1, identity=dict(common_git_dir=str(ROOT / ".git"),
        checkout_realpath=str(ROOT), object_format="sha1", common_git_device=1,
        common_git_inode=1, checkout_device=1, checkout_inode=1), base="1"*40,
        head="2"*40, head_tree="3"*40, code_files=["a.py"], scope_contract=None,
        inspection_scope={}, excluded_side_effects=[],
        control_artifact_exclusions=list(candidate.CONTROL_DIRS),
        manifest=[dict(path="a.py", kind="file", mode="100644", sha256="4"*64,
            size=1, git_oid="5"*40)], index_manifest=[dict(path="a.py", mode="100644",
            oid="5"*40)], untracked_manifest=[])
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    value["content_id"] = digest({k: value[k] for k in keys})
    value["candidate_id"] = digest(value)
    assert candidate.snapshot_identity_ok(value)
    return value


def fixture(tmp_path, mode="v2", fault=None):
    ctl = module("tools/icode_control.py")
    storage = ctl.storage_module()
    project = tmp_path / "project"
    ticket = project / ".icode_output" / ".debug" / ".icode_output_1"
    ticket.mkdir(parents=True)
    snap = snapshot()
    logical = dict(schema_version=3, ticket_id="reader-1", status="completed", debug=True,
        tb_source=dict(pid="p1", lib="LIB", num="1"), claims=[{"id": "claim"}],
        candidate_tracking={"version": 1, "current": snap},
        extensions={"skills": {"runs": [{"skill": "unrouted", "trigger": "gap"}]}},
        verification_runs=[{"outcome": "pass", "candidate": snap}])
    first = dict(schema_version=1, event_id=str(uuid.uuid4()), ticket_id="reader-1",
        timestamp="2026-11-14T10:00:00", actor="icode", event_type="external_note",
        payload={"candidate": snap}, previous_event_hash=ctl.GENESIS_HASH)
    first["event_hash"] = ctl.canonical_event_hash(first)
    second = copy.deepcopy(first)
    second.update(event_id=str(uuid.uuid4()), previous_event_hash=first["event_hash"])
    second["event_hash"] = ctl.canonical_event_hash(second)
    registry = storage.Registry()
    physical_first, registry = storage.encode_event(first, committed_registry=registry)
    physical_second, registry = storage.encode_event(second, committed_registry=registry)
    records = [first, second] if mode == "inline" else (
        [first, physical_second] if mode == "mixed" else [physical_first, physical_second])
    metadata = logical if mode == "inline" else storage.encode_document(
        logical, kind="metadata", committed_registry=registry)
    if fault == "unknown":
        metadata = {"storage_version": 99, "kind": "metadata", "value": logical}
    elif fault == "missing":
        records = []
    elif fault == "corrupt":
        records[0]["value"]["payload"]["candidate"]["manifest"][0]["size"] = 2
    elif fault == "wrong_kind":
        metadata["kind"] = "event"
    (ticket / ctl.METADATA_NAME).write_text(json.dumps(metadata), encoding="utf-8")
    (ticket / ctl.EVENTS_NAME).write_text("".join(json.dumps(e)+"\n" for e in records), encoding="utf-8")
    if fault == "pending":
        (ticket / ctl.TXN_NAME).write_text("{}", encoding="utf-8")
    return project, ticket, logical


def inventory(name, project, ticket):
    path = ticket / ".ico_metadata.json"
    if name == "submission":
        return module("scripts/submission_guard.py").load_metadata(path)
    if name == "debt":
        return module("tools/verification_debt.py").load_json(path, "metadata")
    if name == "debt_pointer":
        value = module("tools/verification_debt.py")._metadata_from_index_pointer(
            {"archive_path": str(ticket), "ticket_id": "reader-1"}, "archive_path")
        return {"ticket_id": "reader-1" if value == path else None}
    if name == "crosscheck":
        return module("tools/icode_crosscheck.py").load_json(path, "metadata")
    if name in {"lint_thinking_gate", "lint_mcp_coverage", "lint_workflow_contract"}:
        return module(f"tools/{name}.py").load_json(path)
    if name == "debug_catalog":
        return module("tools/debug_catalog.py")._load_object(path)
    if name == "propose":
        diagnostics = []
        rows = module("tools/propose_skill_candidates.py").load_runs(project, diagnostics)
        if diagnostics:
            raise RuntimeError(str(diagnostics))
        return {"ticket_id": rows[0][0] if rows else None}
    if name == "learn":
        result = module("tools/learn.py")._related_ticket_observations(path)
        return {"ticket_id": "reader-1" if result["claims"] == 1 else None}
    if name == "tb_watch":
        rows = module("tools/tb/scripts/tb_watch.py").scan_debug_worktickets(project)
        return rows[0][1] if rows else {"ticket_id": None}
    if name == "catalog":
        from icode_agent.ticket_catalog import _read_json
        return _read_json(path, "metadata")
    if name == "openai":
        from icode_agent.backends.openai_responses import _ticket_id
        return {"ticket_id": _ticket_id(SimpleNamespace(ticket_dir=ticket))}
    if name == "ui":
        from icode_agent.ui_server import AgentUIService
        return AgentUIService._load_json_projection(path)
    raise AssertionError(name)


READERS = ("submission", "debt", "debt_pointer", "crosscheck", "lint_thinking_gate",
    "lint_mcp_coverage", "lint_workflow_contract", "debug_catalog", "propose", "learn",
    "tb_watch", "catalog", "openai", "ui")


def state(root):
    return {str(p.relative_to(root)): ("dir", p.stat().st_mtime_ns) if p.is_dir() else
        (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in root.rglob("*")}


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("mode", ["inline", "v2", "mixed"])
def test_inventory_logical_equivalence_readonly(tmp_path, reader, mode):
    project, ticket, logical = fixture(tmp_path, mode)
    before = state(project)
    result = inventory(reader, project, ticket)
    assert result["ticket_id"] == logical["ticket_id"]
    if "candidate_tracking" in result:
        assert result == logical
    assert state(project) == before


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("fault", ["unknown", "missing", "corrupt", "wrong_kind", "pending"])
def test_inventory_rejects_storage_damage_readonly(tmp_path, reader, fault):
    project, ticket, _ = fixture(tmp_path, fault=fault)
    before = state(project)
    try:
        inventory(reader, project, ticket)
    except (Exception, SystemExit) as exc:
        assert not isinstance(exc, AssertionError), "reader assertion is not a storage diagnostic"
    else:
        pytest.fail("invalid storage returned a successful/empty observation")
    assert state(project) == before


@pytest.mark.parametrize("mode", ["inline", "v2", "v2_legacy_value"])
def test_legacy_migrate_refuses_controlled_metadata_without_writes(tmp_path, mode):
    project, ticket, _ = fixture(tmp_path, "v2" if mode == "v2_legacy_value" else mode)
    if mode == "v2_legacy_value":
        metadata_path = ticket / ".ico_metadata.json"
        physical = json.loads(metadata_path.read_text())
        physical["value"]["schema_version"] = 2
        metadata_path.write_text(json.dumps(physical))
    guard = module("scripts/submission_guard.py")
    before = state(project)
    result = guard.cmd_migrate(SimpleNamespace(metadata=str(ticket / ".ico_metadata.json"), dry_run=False))
    assert result != 0
    assert state(project) == before


@pytest.mark.parametrize("fault", ["unknown", "missing", "corrupt", "pending"])
def test_aggregate_callers_do_not_report_success_for_broken_ticket(tmp_path, fault, monkeypatch, capsys):
    project, ticket, _ = fixture(tmp_path, fault=fault)
    before = state(project)
    propose = module("tools/propose_skill_candidates.py")
    monkeypatch.setattr(sys, "argv", ["propose", "--root", str(project)])
    propose.main()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "partial" and report["errors"]
    with pytest.raises(Exception):
        propose.load_runs(project)
    learn = module("tools/learn.py").build_learning_report(project)
    assert learn["status"] == "partial" and learn["parse_failures"]
    debt = module("tools/verification_debt.py")
    monkeypatch.setattr(debt, "INDEX_PATH", tmp_path / "absent-index.json")
    pending = debt.build_pending_report(str(project))
    assert pending["status"] == "partial" and pending["errors"]
    watch = module("tools/tb/scripts/tb_watch.py")
    monkeypatch.setattr(watch, "run_probe", lambda *_: [{"uniqueId": "1"}])
    with pytest.raises(Exception):
        watch.detect_project(project, {"pid": "p1", "lib": "LIB"}, tmp_path)
    assert state(project) == before


@pytest.mark.parametrize("fault", ["unknown", "missing", "corrupt", "pending"])
def test_ui_cockpit_reports_explicit_blocked_projection(tmp_path, fault):
    project, ticket, _ = fixture(tmp_path, fault=fault)
    from icode_agent.ui_server import AgentUIService
    target = SimpleNamespace(out_dir=ticket)
    service = object.__new__(AgentUIService)
    before = state(project)
    projection = service._cockpit_projection(target)
    assert projection["delivery"]["verdict"] == "blocked"
    assert projection["verification"]["status"] == "blocked"
    assert projection["blockers"]
    assert state(project) == before


@pytest.mark.parametrize("name", ["lint_thinking_gate", "lint_mcp_coverage", "lint_workflow_contract"])
@pytest.mark.parametrize("mode", ["inline", "v2", "mixed"])
def test_linter_cli_preserves_identity_before_legacy_decision(tmp_path, name, mode, monkeypatch, capsys):
    project, ticket, _ = fixture(tmp_path, mode)
    before = state(project)
    monkeypatch.setattr(sys, "argv", [name, str(ticket), "--json"])
    assert module(f"tools/{name}.py").main() == 0
    assert json.loads(capsys.readouterr().out)["ticket_id"] == "reader-1"
    assert state(project) == before


@pytest.mark.parametrize("name", ["lint_thinking_gate", "lint_mcp_coverage", "lint_workflow_contract"])
@pytest.mark.parametrize("fault", ["unknown", "missing", "corrupt", "pending"])
def test_linter_cli_cannot_downgrade_damage_to_legacy(tmp_path, name, fault, monkeypatch, capsys):
    project, ticket, _ = fixture(tmp_path, fault=fault)
    before = state(project)
    monkeypatch.setattr(sys, "argv", [name, str(ticket), "--json"])
    assert module(f"tools/{name}.py").main() == 2
    captured = capsys.readouterr()
    assert captured.err and "legacy-untracked" not in captured.out
    assert state(project) == before


@pytest.mark.parametrize("mode", ["inline", "v2", "mixed"])
def test_catalog_resolve_and_ui_cockpit_use_logical_identity(tmp_path, mode):
    project, ticket, _ = fixture(tmp_path, mode)
    from icode_agent.ticket_catalog import TicketCatalog
    from icode_agent.ui_server import AgentUIService
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"tickets": [{"control_schema_version": 3,
        "ticket_id": "reader-1", "project_path": str(project),
        "out_dir": ticket.relative_to(project).as_posix(), "status": "completed"}]}))
    before = state(project)
    target = TicketCatalog(index).resolve("reader-1")
    assert target.ticket_id == "reader-1"
    projection = object.__new__(AgentUIService)._cockpit_projection(target)
    assert projection["verification"]["total"] == projection["verification"]["pass"] == 1
    assert state(project) == before


@pytest.mark.parametrize("command,consumer", [("cmd_g2", "run_g2"),
    ("cmd_submit_check", "build_submission_inventory"), ("cmd_handoff", "build_handoff_report")])
@pytest.mark.parametrize("mode", ["inline", "v2", "mixed"])
def test_submission_callers_receive_complete_logical_metadata(tmp_path, command, consumer, mode, monkeypatch):
    project, ticket, logical = fixture(tmp_path, mode)
    guard = module("scripts/submission_guard.py")
    original = getattr(guard, consumer)
    received = []
    def observe(metadata, *args, **kwargs):
        received.append(metadata)
        return original(metadata, *args, **kwargs)
    monkeypatch.setattr(guard, consumer, observe)
    before = state(project)
    output = ticket / "handoff.json"
    args = SimpleNamespace(metadata=str(ticket / ".ico_metadata.json"), merge=False,
        output=str(output), markdown=None)
    assert getattr(guard, command)(args) in {0, 2}
    assert received == [logical]
    after = state(project)
    if command == "cmd_handoff":
        assert output.is_file()
        assert set(after) - set(before) == {str(output.relative_to(project))}
        for name, value in before.items():
            if name != str(ticket.relative_to(project)):
                assert after[name] == value
    else:
        assert after == before


@pytest.mark.parametrize("command", ["cmd_g2", "cmd_submit_check", "cmd_handoff"])
@pytest.mark.parametrize("fault", ["unknown", "missing", "corrupt", "pending"])
def test_submission_callers_stop_before_output_on_bad_metadata(tmp_path, command, fault):
    project, ticket, _ = fixture(tmp_path, fault=fault)
    guard = module("scripts/submission_guard.py")
    output = ticket / "handoff.json"
    before = state(project)
    with pytest.raises(SystemExit) as stopped:
        getattr(guard, command)(SimpleNamespace(metadata=str(ticket / ".ico_metadata.json"),
            merge=False, output=str(output), markdown=None))
    assert stopped.value.code == (2 if fault == "pending" else 1)
    assert not output.exists()
    assert state(project) == before


@pytest.mark.parametrize("name", ["debt", "crosscheck", "lint_thinking_gate",
    "lint_mcp_coverage", "lint_workflow_contract", "debug_catalog", "catalog", "ui"])
def test_generic_resource_json_does_not_interpret_storage_markers(tmp_path, name):
    resource = tmp_path / "camera_meta.json"
    original = {"storage_version": 99, "kind": "metadata", "value": {"manifest": []}}
    resource.write_text(json.dumps(original))
    if name == "debt":
        result = module("tools/verification_debt.py").load_json(resource, "overlay")
    elif name == "crosscheck":
        result = module("tools/icode_crosscheck.py").load_json(resource, "container")
    elif name == "debug_catalog":
        result = module("tools/debug_catalog.py")._load_object(resource)
    elif name == "catalog":
        from icode_agent.ticket_catalog import _read_json
        result = _read_json(resource, "index")
    elif name == "ui":
        from icode_agent.ui_server import AgentUIService
        result = AgentUIService._load_json_projection(resource)
    else:
        result = module(f"tools/{name}.py").load_json(resource)
    assert result == original


RAW_IO_FILES = (
    "scripts/submission_guard.py", "tools/verification_debt.py", "tools/icode_crosscheck.py",
    "tools/lint_thinking_gate.py", "tools/lint_mcp_coverage.py", "tools/lint_workflow_contract.py",
    "tools/debug_catalog.py", "tools/propose_skill_candidates.py", "tools/learn.py",
    "tools/tb/scripts/tb_watch.py", "agent_runtime/icode_agent/ticket_catalog.py",
    "agent_runtime/icode_agent/backends/openai_responses.py", "agent_runtime/icode_agent/ui_server.py",
)


def raw_io_inventory(relative):
    result = []
    class Audit(ast.NodeVisitor):
        owner = "<module>"
        def visit_FunctionDef(self, node):
            before, self.owner = self.owner, node.name
            self.generic_visit(node)
            self.owner = before
        def visit_Call(self, node):
            call = ast.unparse(node.func)
            if (call in {"open", "json.load", "json.loads"} or
                    call.endswith((".read_text", ".read_bytes", ".open", ".strict_json_loads"))):
                # Structural keys are stable across 3.10/3.11 unparse formatting.
                result.append([self.owner, ast.dump(node, include_attributes=False)])
            self.generic_visit(node)
    Audit().visit(ast.parse((ROOT / relative).read_text(encoding="utf-8-sig")))
    return sorted(result)


def test_owned_raw_io_matches_reviewed_allowlist():
    """All raw IO expressions are explicit; metadata dispatch is tested above."""
    approved = json.loads((ROOT / "tests/data/candidate_reader_raw_io_allowlist.json").read_text())
    actual = {relative: raw_io_inventory(relative) for relative in RAW_IO_FILES}
    assert actual == approved
