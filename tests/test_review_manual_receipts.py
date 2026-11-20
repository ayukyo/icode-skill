import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("receipt_coverage", ROOT / "tools/lint_mcp_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


def cli(out, *args):
    result = subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"), *args,
                             "--dir", str(out)], capture_output=True, text=True,
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"))
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize(("planned_path", "document_path"), [
    ("src/app.py", "src/app.py"), ("outside.py", "outside.py"),
    ("src/app.py", "outside.py"),
])
def test_manual_clearance_requires_bound_plan_and_report(tmp_path, planned_path, document_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    source = workspace / "src/app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(50)), encoding="utf-8")
    (workspace / "outside.py").write_text("def changed(): pass\n", encoding="utf-8")
    out = workspace / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "t", "--requirement", "r", "--birth", "plan")
    cli(out, "step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    plan_candidates = dict(schema_version=1, ticket_id="t", attempt="plan-a", files=[planned_path])
    document_candidates = dict(plan_candidates, files=[document_path])
    (out / "01_plan.md").write_text(
        f"# 计划\n修改 {document_path}\n<!-- icode:plan-candidates -->\n```json\n"
        + json.dumps(document_candidates) + "\n```\n<!-- /icode:plan-candidates -->\n", encoding="utf-8")
    candidates = out / "plan_candidates.json"
    candidates.write_text(json.dumps(plan_candidates), encoding="utf-8")
    for name in ("01_plan.md", "plan_candidates.json"):
        cli(out, "artifact", "--step", "plan", "--attempt", "plan-a", "--path", name)
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", "plan-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "plan")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="review-a", review_scope=["src"],
                  unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                  catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                  family="all") for i in range(50)])],
                  families=[dict(name="all", members=[f"src/app.py:{i+1}:f{i}" for i in range(50)],
                  conclusion="different", reason="逐项检查", evidence=[f"src/app.py:{i+1}" for i in range(50)])])
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    meta = coverage.load_json(out / ".ico_metadata.json")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                threshold=50, rg_available=False, cheap_available=False,
                enumeration_unverified=True,
                repo_review_scopes={str(workspace): ["src"]}))
    redo = dict(base, attempt="review-a", evidence=dict(affected_repo_roots=[str(workspace)], function_count=50,
                threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={str(workspace): ["src"]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref=report_path.name))
    def balance():
        return coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out)
    assert balance() == 1  # 尚无受控产物回执
    cli(out, "artifact", "--step", "review", "--attempt", "review-a", "--path", report_path.name)
    assert balance() == (0 if planned_path == document_path == "src/app.py" else 1)
    # 相同文件内容不能被另一条不存在的 review attempt 借用。
    redo["attempt"] = "not-started"
    assert balance() == 1
    redo["attempt"] = "review-a"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert balance() == 1  # 替换报告后旧回执不可复用
    report_path.write_text(json.dumps(report), encoding="utf-8")
    candidates.write_text(json.dumps(dict(schema_version=1, ticket_id="t", attempt="plan-a",
                                          files=["outside.py"])), encoding="utf-8")
    assert balance() == 1  # 计划清单更改不得复用旧回执


def test_cleared_debt_survives_code_phase_changes(tmp_path):
    """P1 回归：review 时的清偿不随后续 code 阶段正常源码修改失效。

    绑定报告摘要的清偿在源码修改后保持清偿；未绑定的历史清偿回退为未清偿，
    此时 deepcheck.dedup 针对修改后源码的有效复检按相同仓库范围清偿。
    """
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    source = workspace / "src/app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(50)), encoding="utf-8")
    out = workspace / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "t", "--requirement", "r", "--birth", "plan")
    cli(out, "step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    plan_candidates = dict(schema_version=1, ticket_id="t", attempt="plan-a", files=["src/app.py"])
    (out / "01_plan.md").write_text(
        "# 计划\n修改 src/app.py\n<!-- icode:plan-candidates -->\n```json\n"
        + json.dumps(plan_candidates) + "\n```\n<!-- /icode:plan-candidates -->\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(json.dumps(plan_candidates), encoding="utf-8")
    for name in ("01_plan.md", "plan_candidates.json"):
        cli(out, "artifact", "--step", "plan", "--attempt", "plan-a", "--path", name)
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", "plan-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "plan")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="review-a", review_scope=["src"],
                  unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                  catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                  family="all") for i in range(50)])],
                  families=[dict(name="all", members=[f"src/app.py:{i+1}:f{i}" for i in range(50)],
                  conclusion="different", reason="逐项检查", evidence=[f"src/app.py:{i+1}" for i in range(50)])])
    report_path = out / "review_dedup_evidence.json"
    report_bytes = json.dumps(report).encode("utf-8")
    report_path.write_bytes(report_bytes)
    cli(out, "artifact", "--step", "review", "--attempt", "review-a",
        "--path", report_path.name)
    meta = coverage.load_json(out / ".ico_metadata.json")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                threshold=50, rg_available=False, cheap_available=False,
                enumeration_unverified=True,
                repo_review_scopes={str(workspace): ["src"]}))
    bound_redo = dict(base, attempt="review-a", evidence=dict(affected_repo_roots=[str(workspace)],
                function_count=50, threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={str(workspace): ["src"]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref=report_path.name,
                                  evidence_sha256=hashlib.sha256(report_bytes).hexdigest()))
    unbound_redo = dict(bound_redo,
                        availability=dict(bound_redo["availability"]))
    del unbound_redo["availability"]["evidence_sha256"]
    # 无 trace 摘要但存在受控回执：源码修改后仍由回执摘要完成绑定（文档只需 evidence_ref）。

    deepcheck = dict(schema_version=2, ticket_id="t", step="deepcheck", gate_id="deepcheck.dedup",
                tool="extract", at="2026-11-17T08:00:00Z", eligible=True, attempted=True,
                result="success", decision="called",
                evidence=dict(mode="full", phase="dedup", affected_repo_roots=[str(workspace)],
                              function_count=51, threshold=50,
                              repo_review_scopes={str(workspace): ["."]}))

    def balance(rows):
        return coverage.ticket_enumeration_debt(rows, meta, out_dir=out)

    # 绑定摘要的清偿：清偿成立。
    assert balance([debt, bound_redo]) == 0
    # code 阶段正常修改源码（新增函数）后：绑定清偿保持有效，不回退为债务。
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(51)), encoding="utf-8")
    assert balance([debt, bound_redo]) == 0
    # 未填 trace 摘要但已受控登记：源码修改后仍维持清偿（回执提供摘要绑定）。
    assert balance([debt, unbound_redo]) == 0
    # 报告在受控登记后被改写 → 回执摘要不匹配，清偿失效。
    replaced = json.dumps(dict(report, families=[])).encode("utf-8")
    report_path.write_bytes(replaced)
    assert balance([debt, unbound_redo]) == 1
    report_path.write_bytes(report_bytes)
    # 完全没有受控回执的清偿行：源码修改后无法核实 → 保留债务。
    no_receipt = dict(bound_redo,
                      availability=dict(bound_redo["availability"],
                                        evidence_ref="unregistered_evidence.json"))
    assert balance([debt, no_receipt]) == 1
    # 经过验证的后续复检（deepcheck.dedup 有效记录，针对修改后源码、相同仓库范围）
    # 覆盖未清偿债务 → 清偿。
    assert balance([debt, no_receipt, deepcheck]) == 0
    # 复检范围不覆盖仓库 → 不清偿。
    partial = dict(deepcheck, evidence=dict(mode="full", phase="dedup",
                   affected_repo_roots=["/other-repo"], function_count=60, threshold=50,
                   repo_review_scopes={"/other-repo": ["."]}))
    assert balance([debt, no_receipt, partial]) == 1


def test_plan_and_review_ports_allow_documented_registration(tmp_path):
    """P1 回归：文档要求登记的 plan_candidates.json / review_dedup_evidence.json
    必须有对应 output 端口，且登记时完成受控完整校验（源码变更前的验证回执）。"""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    source = workspace / "src/app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(50)), encoding="utf-8")
    out = workspace / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "t", "--requirement", "r", "--birth", "plan")
    cli(out, "step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    candidates = dict(schema_version=1, ticket_id="t", attempt="plan-a", files=["src/app.py"])
    (out / "01_plan.md").write_text(
        "# 计划\n<!-- icode:plan-candidates -->\n```json\n" + json.dumps(candidates)
        + "\n```\n<!-- /icode:plan-candidates -->\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
    for name in ("01_plan.md", "plan_candidates.json"):
        payload = cli(out, "artifact", "--step", "plan", "--attempt", "plan-a", "--path", name)
        assert payload["output"] == ("plan" if name == "01_plan.md" else "plan_candidates")
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", "plan-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "plan")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="review-a", review_scope=["src"],
                  unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                  catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                  family="all") for i in range(50)])],
                  families=[dict(name="all", members=[f"src/app.py:{i+1}:f{i}" for i in range(50)],
                  conclusion="different", reason="逐项检查",
                  evidence=[f"src/app.py:{i+1}" for i in range(50)])])
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    payload = cli(out, "artifact", "--step", "review", "--attempt", "review-a",
                  "--path", report_path.name)
    assert payload["output"] == "review_dedup_evidence"
    # 受控完整校验回执已写入事件：code 阶段修改源码后 review 清偿仍然成立。
    meta = coverage.load_json(out / ".ico_metadata.json")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    receipts = coverage.review_manual_verification_receipt(out, "review-a", report_sha, meta)
    # 返回该 attempt 下所有合规回执组（每组为条目列表）：当前摘要对应一组。
    assert receipts and receipts[0][0]["function_count"] == 50
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                threshold=50, rg_available=False, cheap_available=False,
                enumeration_unverified=True,
                repo_review_scopes={str(workspace): ["src"]}))
    redo = dict(base, attempt="review-a", evidence=dict(affected_repo_roots=[str(workspace)],
                function_count=50, threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={str(workspace): ["src"]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref=report_path.name,
                                  evidence_sha256=report_sha))
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(51)), encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0


def test_incomplete_manual_report_is_rejected_at_registration(tmp_path):
    """P1 回归：不完整的人工报告（缺去重结论）不得登记为受控证据。"""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    (workspace / "src/app.py").write_text("".join(f"def f{i}(): pass\n" for i in range(50)),
                                          encoding="utf-8")
    out = workspace / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "t", "--requirement", "r", "--birth", "plan")
    cli(out, "step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    candidates = dict(schema_version=1, ticket_id="t", attempt="plan-a", files=["src/app.py"])
    (out / "01_plan.md").write_text(
        "# 计划\n<!-- icode:plan-candidates -->\n```json\n" + json.dumps(candidates)
        + "\n```\n<!-- /icode:plan-candidates -->\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
    for name in ("01_plan.md", "plan_candidates.json"):
        cli(out, "artifact", "--step", "plan", "--attempt", "plan-a", "--path", name)
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", "plan-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "plan")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")
    digest = hashlib.sha256((workspace / "src/app.py").read_bytes()).hexdigest()
    incomplete = dict(schema_version=1, ticket_id="t", attempt="review-a", review_scope=["src"],
                      unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                      catalog_method="python_ast", functions=[dict(name="f0", line=1,
                      family="all")])])  # 函数目录不完整且缺 families 去重结论
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps(incomplete), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"), "artifact",
                             "--step", "review", "--attempt", "review-a",
                             "--path", report_path.name, "--dir", str(out)],
                            capture_output=True, text=True,
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"))
    assert result.returncode != 0, result.stdout
    assert "review_dedup_evidence" in result.stdout


def _plan_and_review(out, workspace, source, *, candidate_files=("src/app.py",)):
    """创建并推进到 review attempt（含 plan 回执与计划清单登记）。"""
    cli(out, "create", "--ticket-id", "t", "--requirement", "r", "--birth", "plan")
    cli(out, "step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    candidates = dict(schema_version=1, ticket_id="t", attempt="plan-a",
                      files=list(candidate_files))
    (out / "01_plan.md").write_text(
        "# 计划\n<!-- icode:plan-candidates -->\n```json\n" + json.dumps(candidates)
        + "\n```\n<!-- /icode:plan-candidates -->\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
    for name in ("01_plan.md", "plan_candidates.json"):
        cli(out, "artifact", "--step", "plan", "--attempt", "plan-a", "--path", name)
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", "plan-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "plan")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")


def _full_report(source, *, attempt="review-a", scope=("src",)):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return dict(schema_version=1, ticket_id="t", attempt=attempt, review_scope=list(scope),
                unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                catalog_method="python_ast",
                functions=[dict(name=f"f{i}", line=i + 1, family="all") for i in range(50)])],
                families=[dict(name="all", members=[f"src/app.py:{i+1}:f{i}" for i in range(50)],
                conclusion="different", reason="逐项检查",
                evidence=[f"src/app.py:{i+1}" for i in range(50)])])


def _repo(tmp_path, count=50):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    source = workspace / "src/app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(count)), encoding="utf-8")
    return workspace, source


def _debt_row(workspace):
    return dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(workspace): ["src"]}))


def _review_clearing_row(workspace, report_path, report_sha):
    return dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:30:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call", attempt="review-a",
                evidence=dict(affected_repo_roots=[str(workspace)], function_count=50,
                              threshold=50, rg_available=True, cheap_available=False,
                              repo_review_scopes={str(workspace): ["src"]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref=report_path.name,
                                  evidence_sha256=report_sha))


def test_single_repo_manual_dedup_wrapper_registers(tmp_path):
    """P1 回归：文档允许单仓 {"manual_dedup": 报告} 格式，登记必须能读出内层 review_scope。"""
    workspace, source = _repo(tmp_path)
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source)
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps({"manual_dedup": _full_report(source)}), encoding="utf-8")
    payload = cli(out, "artifact", "--step", "review", "--attempt", "review-a",
                  "--path", report_path.name)
    assert payload["output"] == "review_dedup_evidence"


def test_multi_repo_manual_dedup_wrapper_registers(tmp_path):
    """P1 回归：多仓 {"repositories": [{repo_root, manual_dedup}]} 格式同样可登记。"""
    workspace, source = _repo(tmp_path)
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source)
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps({"repositories": [
        {"repo_root": str(workspace), "manual_dedup": _full_report(source)}]}), encoding="utf-8")
    payload = cli(out, "artifact", "--step", "review", "--attempt", "review-a",
                  "--path", report_path.name)
    assert payload["output"] == "review_dedup_evidence"


def test_planned_source_deletion_keeps_cleared_debt(tmp_path):
    """P2 回归：计划允许删除源码，code 阶段按计划删除后仍维持 review 时的清偿。"""
    workspace, source = _repo(tmp_path)
    extra = workspace / "src/legacy.py"
    extra.write_text("def legacy(): pass\n", encoding="utf-8")
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source, candidate_files=("src/app.py", "src/legacy.py"))
    # 报告覆盖两个源码文件（审查时都还在）。
    report = _full_report(source)
    digest = hashlib.sha256(extra.read_bytes()).hexdigest()
    report["sources"].append(dict(path="src/legacy.py", sha256=digest,
                                  catalog_method="python_ast",
                                  functions=[dict(name="legacy", line=1, family="all")]))
    report["families"][0]["members"].append("src/legacy.py:1:legacy")
    report["families"][0]["evidence"].append("src/legacy.py:1")
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    cli(out, "artifact", "--step", "review", "--attempt", "review-a", "--path", report_path.name)
    meta = coverage.load_json(out / ".ico_metadata.json")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    debt = _debt_row(workspace)
    clearing = _review_clearing_row(workspace, report_path, report_sha)
    clearing["evidence"]["function_count"] = 51
    assert coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out) == 0
    # code 阶段按计划删除 legacy.py：仍维持清偿（计划清单已声明删除）。
    extra.unlink()
    assert coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out) == 0


def test_relative_repo_root_matches_absolute_receipt(tmp_path):
    """P2 回归：trace 用相对仓库根（"."）时，回执的绝对根必须按工程根规范化后匹配。"""
    workspace, source = _repo(tmp_path)
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source)
    report_path = out / "review_dedup_evidence.json"
    report_path.write_text(json.dumps(_full_report(source)), encoding="utf-8")
    cli(out, "artifact", "--step", "review", "--attempt", "review-a", "--path", report_path.name)
    meta = coverage.load_json(out / ".ico_metadata.json")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    debt = dict(_debt_row(workspace),
                evidence=dict(affected_repo_roots=["."], function_count=0, threshold=50,
                              rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={".": ["src"]}))
    clearing = _review_clearing_row(workspace, report_path, report_sha)
    clearing["evidence"] = dict(affected_repo_roots=["."], function_count=50, threshold=50,
                                rg_available=True, cheap_available=False,
                                repo_review_scopes={".": ["src"]})
    assert coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out) == 0
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(51)), encoding="utf-8")
    result = coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out)
    assert result == 0


def test_unfinished_deepcheck_recheck_does_not_clear_debt(tmp_path):
    """P1 回归：deepcheck 复检必须真实成功结束且报告已受控登记；
    仅启动（未 finish）时不得清偿 review 债务。"""
    workspace, source = _repo(tmp_path)
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source)
    meta = coverage.load_json(out / ".ico_metadata.json")
    debt = _debt_row(workspace)
    (out / "02_review.md").write_text("# review\n", encoding="utf-8")
    detail = dict(new_issues=[], refuted_issues=[], pending_verification=[])
    (out / "review_round_1.json").write_text(json.dumps(detail), encoding="utf-8")
    (out / "review_manifest.json").write_text(json.dumps(dict(
        schema_version=1, ticket_id="t", review_run="review-a", rounds=[
            dict(round=1, origin_attempt="review-a", new_issues=0, refuted_issues=0,
                 pending_verification=0, detail_path="review_round_1.json",
                 detail_sha256=hashlib.sha256(
                     (out / "review_round_1.json").read_bytes()).hexdigest())])),
        encoding="utf-8")
    for name in ("02_review.md", "review_round_1.json", "review_manifest.json"):
        cli(out, "artifact", "--step", "review", "--attempt", "review-a", "--path", name)
    for boundary in ("before_write", "after_wait", "before_transition"):
        cli(out, "step", "--step", "review", "--phase", "check", "--attempt", "review-a",
            "--boundary", boundary)
    cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-a",
        "--outcome", "success", "--evidence", "review")
    cli(out, "transition", "--to", "review_done", "--skip-gates")
    (out / "03_plan_final.md").write_text("# final plan\n", encoding="utf-8")
    cli(out, "transition", "--to", "plan_finalized", "--skip-gates")
    cli(out, "transition", "--to", "code_in_progress", "--skip-gates")
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(51)), encoding="utf-8")
    (out / "04_code_review_fix.md").write_text("# code review\n", encoding="utf-8")
    cli(out, "metadata-update", "--set-json", json.dumps({"code_files": ["src/app.py"]}))
    cli(out, "step", "--step", "deepcheck", "--phase", "start", "--attempt", "deepcheck-a")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="deepcheck-a",
                  review_scope=["src"], unobserved=[],
                  sources=[dict(path="src/app.py", sha256=digest,
                                catalog_method="python_ast",
                                functions=[dict(name=f"f{i}", line=i + 1, family="all")
                                           for i in range(51)])],
                  families=[dict(name="all",
                                 members=[f"src/app.py:{i+1}:f{i}" for i in range(51)],
                                 conclusion="different", reason="逐项复检",
                                 evidence=[f"src/app.py:{i+1}" for i in range(51)])])
    report_path = out / "deepcheck_dedup_evidence.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    cli(out, "artifact", "--step", "deepcheck", "--attempt", "deepcheck-a",
        "--path", report_path.name)
    recheck = dict(schema_version=2, ticket_id="t", step="deepcheck",
                   gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T09:00:00Z",
                   eligible=True, attempted=False, result="unavailable",
                   decision="unavailable_before_call", attempt="deepcheck-a",
                   evidence=dict(mode="full", phase="dedup",
                                 affected_repo_roots=[str(workspace)],
                                 function_count=51, threshold=50,
                                 repo_review_scopes={str(workspace): ["src"]}),
                   availability=dict(tool_visible=False, reason="not_exposed",
                                     discovery_ref="s", fallback="manual",
                                     evidence_ref=report_path.name))
    meta = coverage.load_json(out / ".ico_metadata.json")
    # 报告已登记但步骤未结束 → 不得清偿。
    assert coverage.ticket_enumeration_debt([debt, recheck], meta, out_dir=out) == 1


def test_receipt_matches_current_digest_after_reregistration(tmp_path):
    """P2 回归：同一 attempt 多次登记报告时，按**当前报告摘要**匹配回执；
    源码修改后不得沿用更早回执的摘要而误判债务重现。"""
    workspace, source = _repo(tmp_path)
    out = workspace / ".icode_output/.icode_output_1"
    _plan_and_review(out, workspace, source)
    report = _full_report(source)
    report_path = out / "review_dedup_evidence.json"
    # 第一次登记（较早、不完整的版本）。
    older = dict(report, families=[])
    report_path.write_text(json.dumps(older), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"), "artifact",
                             "--step", "review", "--attempt", "review-a",
                             "--path", report_path.name, "--dir", str(out)],
                            capture_output=True, text=True,
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"))
    assert result.returncode != 0  # 不完整报告被拒绝登记（不会留下旧回执）
    # 完善后登记（当前文件对应最新回执）。
    report_path.write_text(json.dumps(report), encoding="utf-8")
    cli(out, "artifact", "--step", "review", "--attempt", "review-a",
        "--path", report_path.name)
    meta = coverage.load_json(out / ".ico_metadata.json")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    debt = dict(_debt_row(workspace),
                evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(workspace): ["src"]}))
    clearing = _review_clearing_row(workspace, report_path, report_sha)
    assert coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out) == 0
    # code 阶段修改源码：仍应按当前报告的新回执维持清偿。
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(51)), encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, clearing], meta, out_dir=out) == 0
