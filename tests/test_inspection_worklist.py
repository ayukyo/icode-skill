"""Temporary repositories only; no ticket/control-plane writes."""
import copy
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools/inspection_worklist.py"


@pytest.fixture
def api():
    if not TOOL.is_file():
        pytest.skip("API presence assertion supplies the initial RED")
    spec = importlib.util.spec_from_file_location("inspection_worklist", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_exists_before_contract_tests():
    assert TOOL.is_file(), "inspection worklist API has not been implemented"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT).decode().strip()


def put(root, path, text="int feature(void) { return 1; }\n"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def init(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "fixture")


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    init(tmp_path)
    put(tmp_path, "src/feature.c")
    put(tmp_path, "src/feature.h", "int feature(void);\n")
    put(tmp_path, "src/test_feature.c", "void test_feature(void) {}\n")
    put(tmp_path, "src/other.py", "x = 1\n")
    put(tmp_path, "elsewhere/other.c")
    commit(tmp_path)
    return tmp_path


def build(api, repo, **kwargs):
    return api.build_worklist(repo, ["src/feature.c"], step="deepcheck", ticket_id="test-1", attempt=1, **kwargs)


def files(report):
    return {f["path"]: f for unit in report["units"] for f in unit["files"]}


def read_all(report):
    for f in files(report).values():
        f["reads"] = dict.fromkeys(report["required_phases"], f["sha256"])
    return report


def test_deterministic_associations_and_reads(api, repo):
    before = git(repo, "status", "--porcelain")
    report = build(api, repo)
    assert report == build(api, str(repo))
    assert report["workspace"] == str(repo.resolve())
    assert report["schema_version"] == 1
    assert report["required_phases"] == ["reverse", "fixed", "free"]
    assert set(files(report)) == {"src/feature.c", "src/feature.h", "src/test_feature.c"}
    assert len(report["units"]) == 1
    assert all(f["reads"] == {} and f["rules"] for f in files(report).values())
    assert api.validate_worklist(report, repo)
    assert api.validate_worklist(read_all(report), repo, code_files=["src/feature.c"], step="deepcheck", ticket_id="test-1", attempt=1) == []
    assert git(repo, "status", "--porcelain") == before


@pytest.mark.parametrize("step,mode,phases", [("code", "full", ["code_review"]), ("deepcheck", "fast", ["reverse"]), ("audit", "full", ["audit"]), ("crosscheck", "full", ["fresh"])])
def test_phases(api, repo, step, mode, phases):
    report = api.build_worklist(repo, ["src/feature.c"], step=step, ticket_id="t", attempt=2, mode=mode)
    assert report["required_phases"] == phases


def test_diff_untracked_exclusions_and_removed_manifest(api, repo):
    put(repo, "src/other.py", "x = 2\n")
    put(repo, "src/new.sh", "#!/bin/sh\nexit 0\n")
    put(repo, "elsewhere/other.c", "int changed;\n")
    report = read_all(build(api, repo))
    assert {"src/other.py", "src/new.sh"} <= files(report).keys()
    assert {"path": "elsewhere/other.c", "reason": "out_of_scope"} in report["exclusions"]
    assert "elsewhere/other.c" not in files(report)
    report["units"] = [u for u in report["units"] if all(f["path"] != "src/other.py" for f in u["files"])]
    assert api.validate_worklist(report, repo)
    report["coverage_status"] = "partial"
    report["debt_reason"] = "not reviewed yet"
    assert api.validate_worklist(report, repo, allow_incomplete=True)


def test_explicit_baseline_deleted_and_drift(api, repo):
    baseline = git(repo, "rev-parse", "HEAD")
    old = (repo / "src/feature.c").read_bytes()
    (repo / "src/feature.c").unlink()
    commit(repo)
    report = read_all(build(api, repo, baselines={".": baseline}))
    entry = files(report)["src/feature.c"]
    assert entry["kind"] == "deleted"
    assert entry["sha256"] == hashlib.sha256(old).hexdigest()
    assert api.validate_worklist(report, repo) == []
    put(repo, "src/feature.c", "int new_version;\n")
    assert api.validate_worklist(report, repo, allow_incomplete=True)


def test_invalid_baseline_does_not_fallback(api, repo):
    with pytest.raises(ValueError, match="baseline"):
        build(api, repo, baselines={".": "not-a-real-ref"})


def test_nested_repo_and_explicit_related_scope(api, repo):
    child = repo / "nested"
    init(child)
    put(child, "src/child.cpp", "int child;\n")
    put(child, "src/child.hpp", "extern int child;\n")
    commit(child)
    put(child, "src/extra.cpp", "int extra;\n")
    report = api.build_worklist(repo, ["nested/src/child.cpp"], step="code", ticket_id="t", attempt=1)
    assert set(files(report)) == {"nested/src/child.cpp", "nested/src/child.hpp", "nested/src/extra.cpp"}
    assert api.validate_worklist(read_all(report), repo) == []
    report = build(api, repo, related=["elsewhere/other.c"], scopes=["src", "elsewhere"])
    assert "elsewhere/other.c" in files(report)


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.c", "src/../feature.c", ".git/config", ".icode_output/ticket", "src/id_rsa", "src/.env", "src/private.pem"])
def test_unsafe_seeds(api, repo, path):
    with pytest.raises(ValueError):
        api.build_worklist(repo, [path], step="code", ticket_id="t", attempt=1)


def test_symlink_files_parents_and_workspace(api, repo, tmp_path):
    (repo / "src/link.c").symlink_to(repo / "src/feature.c")
    (repo / "linked").symlink_to(repo / "src", target_is_directory=True)
    for path in ["src/link.c", "linked/feature.c"]:
        with pytest.raises(ValueError, match="symlink"):
            api.build_worklist(repo, [path], step="code", ticket_id="t", attempt=1)
    report = build(api, repo)
    assert "src/link.c" not in files(report)
    assert any("symlink" in e["reason"] for e in report["exclusions"])


@pytest.mark.parametrize("content,max_bytes", [(b"\x00binary", 1024), (b"x" * 50, 10)])
def test_unreadable_seed_is_debt(api, repo, content, max_bytes):
    (repo / "src/feature.c").write_bytes(content)
    report = build(api, repo, max_bytes=max_bytes)
    assert report["coverage_status"] in {"partial", "degraded"}
    assert report["unobserved"]
    assert api.validate_worklist(report, repo)
    read_all(report)
    assert api.validate_worklist(report, repo, allow_incomplete=True) == []
    report["coverage_status"] = "complete_within_scope"
    assert api.validate_worklist(report, repo, allow_incomplete=True)


def test_file_budget_and_incomplete_read_policy(api, repo):
    report = build(api, repo, max_files=1)
    assert report["coverage_status"] == "partial" and report["unobserved"]
    assert api.validate_worklist(read_all(report), repo, allow_incomplete=True) == []
    report = build(api, repo)
    report["coverage_status"] = "partial"
    assert api.validate_worklist(report, repo, allow_incomplete=True)
    report["debt_reason"] = "Agent has not performed Read yet"
    assert api.validate_worklist(report, repo, allow_incomplete=True) == []
    files(report)["src/feature.c"]["reads"]["reverse"] = "0" * 64
    assert api.validate_worklist(report, repo, allow_incomplete=True)


@pytest.mark.parametrize("field,value", [("ticket_id", "wrong"), ("attempt", True), ("required_phases", []), ("workspace", "/tmp/wrong"), ("schema_version", 2)])
def test_identity_and_shape_fail_closed(api, repo, field, value):
    report = read_all(build(api, repo))
    report[field] = value
    assert api.validate_worklist(report, repo, ticket_id="test-1", attempt=1)


def finding(report):
    f = files(report)["src/feature.c"]
    return {"finding_id": "F1", "verification_status": "confirmed", "locations": [{"path": f["path"], "start_line": 1, "end_line": 1, "source_sha256": f["sha256"], "excerpt": "int feature(void) { return 1; }\n"}]}


def test_findings_current_exact_excerpt_and_duplicate(api, repo):
    report = read_all(build(api, repo))
    f = finding(report)
    assert api.validate_findings([f], report, repo) == []
    assert api.validate_findings([f, f], report, repo)
    put(repo, "src/feature.c", "int changed;\n")
    assert api.validate_findings([f], report, repo)


@pytest.mark.parametrize("mutation", ["status", "location_field"])
def test_finding_new_fields_match_closed_schema(api, repo, mutation):
    report = build(api, repo)
    f = finding(report)
    if mutation == "status":
        f["verification_status"] = "invented_confirmed_state"
    else:
        f["locations"][0]["invented_field"] = "ignored?"
    assert api.validate_findings([f], report, repo)


@pytest.mark.parametrize("field,value", [("start_line", True), ("start_line", 0), ("end_line", 0), ("end_line", 2), ("excerpt", "int feature(void) { return 1; }"), ("source_sha256", "0" * 64), ("path", "elsewhere/other.c")])
def test_finding_location_rejects_invalid(api, repo, field, value):
    report = build(api, repo)
    f = finding(report)
    f["locations"][0][field] = value
    assert api.validate_findings([f], report, repo)


def test_non_source_unlocated_and_resolved_findings(api, repo):
    report = build(api, repo)
    assert api.validate_findings([{"id": "D", "locations": [], "category": "design", "evidence_boundary": "Design review only; no source location"}], report, repo) == []
    assert api.validate_findings([{"id": "U", "verification_status": "needs_more_evidence", "evidence_boundary": "No source evidence yet"}], report, repo) == []
    assert api.validate_findings([{"id": "U", "verification_status": "confirmed", "evidence_boundary": "No source evidence yet"}], report, repo)
    assert api.validate_findings([{"id": "R", "status": "resolved"}], report, repo) == []
    stale = finding(report)
    stale["status"] = "resolved"
    stale["locations"][0]["source_sha256"] = "0" * 64
    assert api.validate_findings([stale], report, repo)


@pytest.mark.parametrize("report", [{}, None, {"units": [None]}])
def test_malformed_input_never_keyerror(api, repo, report):
    assert api.validate_worklist(report, repo)
    assert api.validate_findings([{}], report, repo)


def test_schema_documents_actual_report(api, repo):
    schema = json.loads((TOOL.parents[1] / "schemas/inspection-worklist.schema.json").read_text())
    report = build(api, repo)
    assert schema["properties"]["schema_version"]["const"] == 1
    assert set(schema["required"]) <= report.keys()
    assert set(report) <= schema["properties"].keys()


def test_control_prepare_shape_findings_and_hard_drift(api, repo):
    report = build(api, repo)
    assert report["findings"] == []
    report.update(coverage_status="partial", debt_reason="prepare pending")
    report["findings"] = [{"id": "free form"}]
    assert api.validate_worklist(report, repo, allow_incomplete=True) == []
    files(report)["src/feature.c"]["sha256"] = "0" * 64
    assert api.validate_worklist(report, repo, allow_incomplete=True)


def test_nongit_explicit_related_and_bounded_association(api, tmp_path):
    put(tmp_path, "src/feature.c")
    put(tmp_path, "src/feature.h", "int feature(void);\n")
    put(tmp_path, "src/feature_test.cpp", "void feature_test() {}\n")
    put(tmp_path, "src/other.py", "x = 1\n")
    report = api.build_worklist(tmp_path, ["src/feature.c"], related=["src/other.py"], step="audit", ticket_id="t", attempt="a")
    assert set(files(report)) == {"src/feature.c", "src/feature.h", "src/feature_test.cpp", "src/other.py"}
    assert report["coverage_status"] == "partial"
    assert any("Git" in str(d) or "diff" in str(d) for d in report["unobserved"])
    assert api.validate_worklist(read_all(report), tmp_path, allow_incomplete=True) == []
    report = api.build_worklist(tmp_path, ["src/feature.c"], step="audit", ticket_id="t", attempt=1, max_files=1)
    assert report["unobserved"] and report["coverage_status"] == "partial"


def test_related_outside_git_scope_is_required(api, repo):
    report = read_all(build(api, repo, related=["elsewhere/other.c"]))
    assert "elsewhere/other.c" in files(report)
    assert files(report)["elsewhere/other.c"]["reason"] == "explicit_related"
    assert api.validate_worklist(report, repo) == []


def test_validated_baseline_is_pinned_if_symbolic_ref_disappears(api, repo):
    git(repo, "branch", "inspection-base")
    report = read_all(build(api, repo, baselines={".": "inspection-base"}))
    git(repo, "branch", "-D", "inspection-base")
    assert api.validate_worklist(report, repo) == []
    # A *new* invalid ref still fails; pinned evidence is never HEAD fallback.
    with pytest.raises(ValueError, match="baseline"):
        build(api, repo, baselines={".": "inspection-base"})


def test_ticket_sidecars_do_not_exhaust_source_budget_or_drift(api, repo):
    for n in range(220):
        put(repo, f".icode_output/.icode_output_1/generated_{n}.json", "{}\n")
    report = read_all(build(api, repo, scopes=["."], max_files=20))
    assert report["coverage_status"] == "complete_within_scope"
    put(repo, ".icode_output/.icode_output_2/new.json", "{}\n")
    assert api.validate_worklist(report, repo) == []


@pytest.mark.parametrize("where", ["report", "unit", "file"])
def test_unknown_schema_fields_rejected(api, repo, where):
    report = read_all(build(api, repo))
    target = report if where == "report" else report["units"][0] if where == "unit" else next(iter(files(report).values()))
    target["invented_field"] = True
    assert api.validate_worklist(report, repo)


def test_exclusion_changes_and_control_output_do_not_block(api, repo):
    report = read_all(build(api, repo, scopes=["."]))
    put(repo, ".icode_output/ticket/report.json", "{}\n")
    assert api.validate_worklist(report, repo) == []
    assert not build(api, repo, scopes=["."])["unobserved"]
    report = read_all(build(api, repo))
    put(repo, "elsewhere/new.c")
    assert api.validate_worklist(report, repo) == []


def test_explicit_related_outside_seed_scope(api, repo):
    report = build(api, repo, related=["elsewhere/other.c"])
    assert "elsewhere/other.c" in files(report)
    put(repo, "elsewhere/extra.c")
    assert "elsewhere/extra.c" not in files(build(api, repo, related=["elsewhere/other.c"]))
    assert api.validate_worklist(read_all(report), repo) == []


def test_pending_dict_does_not_require_synthetic_reads(api, repo):
    report = build(api, repo)
    pending = dict(report, coverage_status="partial", debt_reason="pending Read")
    assert api.validate_worklist(pending, repo, allow_incomplete=True) == []
    assert api.validate_worklist(report, repo)


@pytest.mark.parametrize("level", ["report", "unit", "file"])
def test_unknown_fields_rejected(api, repo, level):
    report = read_all(build(api, repo))
    target = {"report": report, "unit": report["units"][0], "file": next(iter(files(report).values()))}[level]
    target["unexpected"] = True
    assert api.validate_worklist(report, repo)
