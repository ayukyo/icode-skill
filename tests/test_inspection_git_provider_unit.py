"""Software-only provider fixtures; no host identity or native isolation claim."""
import copy
import hashlib
import importlib.util
import inspect
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/inspection_worklist.py"
spec = importlib.util.spec_from_file_location("inspection_provider_unit", TOOL)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
OID = "1" * 40
OLD = b"int timestamp_old(void) { return 1; }\n"

def invoke(function, *args, provider, **kwargs):
    # Before SOURCE: run actual existing semantics, not TypeError/import RED.
    if "git_provider" in inspect.signature(function).parameters:
        kwargs["git_provider"] = provider
    return function(*args, **kwargs)

ProviderBase = getattr(api, "InspectionGitProvider", object)

class FixtureProvider(ProviderBase):
    def __init__(self, root):
        self.workspace = root
        self.calls = []
        self.fault = None
    def query(self, request, *, limit):
        op = request.operation.value
        self.calls.append(request)
        if op == "resolve_commit":
            data = (OID + "\n").encode("ascii")
        elif op == "diff_names":
            data = b"src/current.c\0src/old.c\0outside.c\0"
        elif op == "list_files":
            data = (b"src/current.c\0src/old.c\0src/test_current.c\0"
                    if request.kind == "association" else b"src/added.c\0")
        elif op == "historical_blob":
            data = OLD
        elif op == "trigger_diff":
            data = b"@@ -1 +1 @@\n-int64_t clock_ns = -1;\n+return 1;\n"
        else:
            raise AssertionError("unexpected provider operation")
        error, exit_code, truncated = None, 0, False
        if self.fault == "exit":
            exit_code = 2
        elif self.fault == "error":
            error = "query_failed"
        elif self.fault == "truncated":
            truncated = True
        elif self.fault == "malformed_names" and op == "diff_names":
            data = b"../escape.c\0"
        elif self.fault == "non_utf8" and op == "historical_blob":
            data = b"\xff"
        elif self.fault == "overflow":
            data = b"x" * (limit + 1)
        elif self.fault == "bad_result":
            return SimpleNamespace(data=data, exit_code=0, truncated=False, error=None)
        elif self.fault == "missing_nul" and op in ("diff_names", "list_files"):
            data = b"src/current.c"
        elif self.fault == "non_utf8_names" and op in ("diff_names", "list_files"):
            data = b"\xff\0"
        elif self.fault == "bool_exit":
            exit_code = False
        elif self.fault == "nonbool_truncated":
            truncated = "false"
        Result = getattr(api, "GitQueryResult", SimpleNamespace)
        return Result(data=data, exit_code=exit_code, truncated=truncated,
                      error=error)

class NestedRelatedProvider(FixtureProvider):
    def query(self, request, *, limit):
        op = request.operation.value
        if op not in ("diff_names", "list_files"):
            return super().query(request, limit=limit)
        self.calls.append(request)
        if op == "diff_names":
            data = b"src/nested/current.c\0"
        elif request.kind == "association":
            data = b"src/nested/current.c\0"
            if "related/nested" in request.scopes:
                data += b"related/nested/related.c\0related/nested/test_related.c\0"
        else:
            data = b""
        return api.GitQueryResult(data=data, exit_code=0, truncated=False, error=None)

def entries(report):
    return {f["path"]: f for u in report["units"] for f in u["files"]}

def reads(report):
    for f in entries(report).values():
        f["reads"] = {phase: f["sha256"] for phase in report["required_phases"]}

def assessment(check, report):
    old = entries(report)["src/old.c"]
    evidence = {"kind": "source", "path": "src/old.c", "start_line": 1,
                "end_line": 1, "source_sha256": old["sha256"],
                "excerpt": OLD.decode("utf-8")}
    return {"cells": {item: {"status": "handled",
        "reason": "Software fixture validates located evidence; no device judgment.",
        "evidence": [copy.deepcopy(evidence)]} for item in check["required_items"]}}

class ProviderUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src/current.c").write_bytes(b"int current(void) { return 1; }\n")
        (self.root / "src/test_current.c").write_bytes(b"int test_current(void) { return 1; }\n")
        (self.root / "src/added.c").write_bytes(b"int added(void) { return 1; }\n")
        (self.root / "outside.c").write_bytes(b"int outside(void) { return 1; }\n")
        self.provider = FixtureProvider(self.root)
    def build(self):
        return invoke(api.build_worklist, self.root, ["src/current.c"],
                      step="code", ticket_id="software-unit", attempt="a1",
                      scopes=["src"], provider=self.provider)
    def test_current_changed_deleted_and_full_scope(self):
        with patch.object(api, "_git", side_effect=AssertionError("raw Git fallback")):
            report = self.build()
        self.assertEqual("complete_within_scope", report["coverage_status"])
        files = entries(report)
        self.assertTrue({"src/current.c", "src/old.c", "src/added.c",
                         "src/test_current.c"}.issubset(files))
        self.assertEqual("deleted", files["src/old.c"]["kind"])
        self.assertEqual(hashlib.sha256(OLD).hexdigest(), files["src/old.c"]["sha256"])
        self.assertEqual(hashlib.sha256((self.root / "src/current.c").read_bytes()).hexdigest(),
                         files["src/current.c"]["sha256"])
        self.assertIn({"path": "outside.c", "reason": "out_of_scope"}, report["exclusions"])
        self.assertTrue(any(c.operation.value == "diff_names" and c.scopes == ()
                            for c in self.provider.calls))
        self.assertTrue(any(c.operation.value == "trigger_diff" for c in self.provider.calls))
    def test_rebuild_assess_deleted_evidence_and_findings(self):
        report = self.build()
        self.assertEqual("complete_within_scope", report["coverage_status"])
        reads(report)
        with patch.object(api, "_git", side_effect=AssertionError("raw Git fallback")):
            for check in report["checks"]:
                invoke(api.apply_assessment, report, self.root, check["check_id"],
                       "code_review", assessment(check, report), provider=self.provider)
            issues = invoke(api.validate_worklist, report, self.root,
                            provider=self.provider)
            self.assertEqual([], issues)
            location = {k: v for k, v in assessment(report["checks"][0], report)
                        ["cells"][report["checks"][0]["required_items"][0]]
                        ["evidence"][0].items() if k != "kind"}
            findings = [{"id": "f1", "verification_status": "confirmed",
                         "locations": [location], "evidence_boundary": "software fixture"}]
            self.assertEqual([], invoke(api.validate_findings, findings, report,
                                        self.root, provider=self.provider))
        self.assertTrue(any(c.ref == OID for c in self.provider.calls
                            if c.operation.value == "resolve_commit"))
        broken = copy.deepcopy(report)
        check = broken["checks"][0]
        item = check["required_items"][0]
        check["results"]["code_review"]["cells"][item]["evidence"][0]["excerpt"] = "wrong\n"
        self.assertTrue(invoke(api.validate_worklist, broken, self.root,
                               provider=self.provider))
        self.assertTrue(api.validate_worklist(report, self.root))
    def test_query_failures_never_complete(self):
        for fault in ("exit", "error", "truncated", "malformed_names", "non_utf8", "overflow", "bad_result"):
            with self.subTest(fault=fault):
                self.provider.fault = fault
                try:
                    report = self.build()
                except (ValueError, OSError):
                    continue
                self.assertNotEqual("complete_within_scope", report["coverage_status"])
                self.assertTrue(report["unobserved"])
    def test_nested_posix_scopes_and_scope_external_related(self):
        (self.root / "src/nested").mkdir()
        (self.root / "related/nested").mkdir(parents=True)
        for relative in ("src/nested/current.c", "related/nested/related.c",
                         "related/nested/test_related.c"):
            (self.root / relative).write_bytes(b"int value(void) { return 1; }\n")
        provider = NestedRelatedProvider(self.root)
        with patch.object(api, "_git", side_effect=AssertionError("raw Git fallback")):
            report = invoke(api.build_worklist, self.root, ["src/nested/current.c"],
                step="code", ticket_id="software-unit", attempt="a1",
                scopes=["src/nested"], related=["related/nested/related.c"],
                provider=provider)
        self.assertEqual("complete_within_scope", report["coverage_status"])
        self.assertIn("related/nested/test_related.c", entries(report))
        self.assertTrue(any(c.operation.value == "list_files" and c.kind == "association"
                            and "related/nested" in c.scopes for c in provider.calls))
        self.assertTrue(any(c.operation.value == "diff_names" and c.scopes == ("src/nested",)
                            for c in provider.calls))
        self.assertTrue(all("\\" not in scope for c in provider.calls for scope in c.scopes))
    def test_direct_result_validator_rejects_invalid_outputs(self):
        self.assertTrue(hasattr(api, "_provider_query"), "typed result validator is required")
        request = api.GitQuery(api.GitOperation.LIST_FILES, kind="others")
        for fault in ("missing_nul", "non_utf8_names", "bool_exit", "nonbool_truncated", "bad_result"):
            with self.subTest(fault=fault):
                self.provider.fault = fault
                with self.assertRaises(ValueError):
                    api._provider_query(self.root, self.provider, request, 8192)
    def test_request_string_fields_reject_falsey_non_strings(self):
        for field in ("ref", "oid", "path", "kind"):
            for value in (0, False, None, 0.0, [], {}, ()):
                with self.subTest(field=field, value_type=type(value).__name__):
                    if field == "kind":
                        operation = api.GitOperation.RESOLVE_COMMIT
                        fields = {"ref": "HEAD"}
                    else:
                        operation = api.GitOperation.LIST_FILES
                        fields = {"kind": "others"}
                    fields[field] = value
                    request = api.GitQuery(operation, **fields)
                    with self.assertRaises(ValueError):
                        api._provider_query(self.root, self.provider, request, 8192)
    def test_assessment_failure_is_atomic(self):
        report = self.build()
        self.assertEqual("complete_within_scope", report["coverage_status"])
        reads(report)
        check = report["checks"][0]
        cells = assessment(check, report)
        saved = copy.deepcopy(report)
        self.provider.fault = "non_utf8"
        with self.assertRaises(ValueError):
            invoke(api.apply_assessment, report, self.root, check["check_id"],
                   "code_review", cells, provider=self.provider)
        self.assertEqual(saved, report)
    def test_workspace_nested_and_names_budget_reject(self):
        self.provider.workspace = self.root / "src"
        with self.assertRaises(ValueError):
            invoke(api.build_worklist, self.root, ["src/current.c"], step="code",
                ticket_id="software-unit", attempt="a1", scopes=["src"],
                provider=self.provider)
        self.provider.workspace = self.root
        (self.root / "src/.git").mkdir()
        with self.assertRaises(ValueError):
            self.build()
        (self.root / "src/.git").rmdir()
        with self.assertRaises(ValueError):
            invoke(api.build_worklist, self.root, ["src/current.c"], step="code",
                ticket_id="software-unit", attempt="a1", scopes=["src"],
                max_files=1, provider=self.provider)
    def test_legacy_real_git_none_is_unchanged(self):
        self.assertIsNotNone(shutil.which("git"), "fixture needs actual Git")
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git("init", "-q")
        git("config", "user.name", "Software Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", ".")
        git("commit", "-qm", "baseline")
        (self.root / "src/current.c").write_bytes(b"int current(void) { return 2; }\n")
        options = dict(step="code", ticket_id="legacy-unit", attempt="a1", scopes=["src"])
        old = api.build_worklist(self.root, ["src/current.c"], **options)
        explicit = invoke(api.build_worklist, self.root, ["src/current.c"],
                          provider=None, **options)
        self.assertEqual(old, explicit)
        self.assertEqual("complete_within_scope", old["coverage_status"])

if __name__ == "__main__":
    unittest.main()
