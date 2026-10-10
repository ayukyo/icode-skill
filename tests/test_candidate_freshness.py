"""Final-source candidates: real Git fixtures and control-plane receipts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("candidate_control", ROOT / "tools/icode_control.py")
ctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ctl)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-candidate-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Candidate Test")
        (self.repo / ".gitignore").write_text(".icode_output/\n", encoding="utf-8")
        (self.repo / "source.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.ticket = self.repo / ".icode_output/.icode_output_1"

    def git(self, *args, cwd=None):
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def call(self, *args, ok=True):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"),
                               *map(str, args)], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode == 0, ok, (proc.stdout, proc.stderr))
        return json.loads(proc.stdout)

    def create(self):
        self.call("create", "--dir", self.ticket, "--ticket-id", "candidate-1",
                  "--birth", "plan", "--requirement", "final source",
                  "--metadata-json", '{"code_files":["source.py"]}', "--request-id", "birth")

    def module(self):
        self.assertTrue(callable(getattr(ctl, "candidate_module", None)),
                        "共享候选模块必须由control复用")
        return ctl.candidate_module()

    def capture(self, **kwargs):
        defaults = dict(base=self.base, code_files=["source.py"], scope_contract=None,
                        excluded_side_effects=[], inspection_scope={})
        defaults.update(kwargs)
        return self.module().capture_candidate(self.repo, **defaults)

    def bytes(self):
        return tuple((self.ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))

    def finish_review(self, step, attempt=None, *, scope=".", after_reads=None, ok=True):
        """Real start/check/inspection/artifact/finish ports, no gate mocking."""
        attempt = attempt or step + "-1"
        (self.ticket / "03_plan_final.md").write_text("fixture plan\n", encoding="utf-8")
        self.call("step", "--dir", self.ticket, "--step", step, "--phase", "start",
                  "--attempt", attempt, "--request", attempt + ":start")
        self.call("step", "--dir", self.ticket, "--step", step, "--phase", "check",
                  "--attempt", attempt, "--boundary", "before_write")
        self.call("inspection", "--dir", self.ticket, "--step", step, "--phase", "prepare",
                  "--attempt", attempt, "--scope", scope, "--baselines-json", json.dumps({".": self.base}))
        report = json.loads((self.ticket / (step + "_worklist.json")).read_text())
        for phase in report["required_phases"]:
            for unit in report["units"]:
                for row in unit["files"]:
                    self.call("inspection", "--dir", self.ticket, "--step", step, "--phase", "read",
                              "--attempt", attempt, "--read-phase", phase, "--path", row["path"])
        if after_reads:
            after_reads()
        report_name = {"code": "04_code_review_fix.md", "deepcheck": "05_deepcheck.md", "audit": "06_audit.md"}[step]
        (self.ticket / report_name).write_text("fixture review report\n", encoding="utf-8")
        self.call("artifact", "--dir", self.ticket, "--step", step, "--attempt", attempt, "--path", report_name)
        if step == "code":
            self.call("artifact", "--dir", self.ticket, "--step", step, "--attempt", attempt,
                      "--path", "source.py", "--scope", "workspace")
        else:
            phases = ["audit"] if step == "audit" else ["reverse", "fixed", "free"]
            coverage = {"schema_version": 1, "ticket_id": "candidate-1", "attempt": attempt,
                        "review_scope": ["."], "coverage_status": "complete_within_scope",
                        "unobserved": [], "dedup_unobserved": [], "dedup_status": "not_eligible",
                        "dedup_reason": "single fixture", "read_phases": {
                            phase: {"source.py": ctl.file_sha256(self.repo / "source.py")} for phase in phases}}
            name = step + "_coverage.json"
            (self.ticket / name).write_text(json.dumps(coverage), encoding="utf-8")
            self.call("artifact", "--dir", self.ticket, "--step", step, "--attempt", attempt, "--path", name)
            self.call("step", "--dir", self.ticket, "--step", step, "--phase", "check",
                      "--attempt", attempt, "--boundary", "after_wait")
        self.call("step", "--dir", self.ticket, "--step", step, "--phase", "check",
                  "--attempt", attempt, "--boundary", "before_transition")
        return self.call("step", "--dir", self.ticket, "--step", step, "--phase", "finish",
                         "--attempt", attempt, "--outcome", "success", "--evidence", report_name,
                         "--request", attempt + ":finish", ok=ok)

    def test_real_code_deepcheck_audit_receipts_same_candidate_then_patch_stale(self):
        self.create()
        ids = [self.finish_review(step)["event_id"] for step in ("code", "deepcheck", "audit")]
        fresh = ctl.delivery_freshness(self.ticket)
        self.assertTrue(fresh["ok"], fresh)
        receipts = ctl.load_metadata(self.ticket)["review_receipts"]
        self.assertEqual([r["event_id"] for r in receipts], ids)
        self.assertEqual(len({r["candidate_id"] for r in receipts}), 1)
        frozen = ctl.load_metadata(self.ticket)
        reports = (self.ticket / "06_audit.md").read_bytes()
        self.call("step", "--dir", self.ticket, "--step", "patch", "--phase", "start", "--attempt", "patch-1")
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.call("step", "--dir", self.ticket, "--step", "patch", "--phase", "finish",
                  "--attempt", "patch-1", "--outcome", "blocked", "--evidence", "fixture changed")
        stale = ctl.delivery_freshness(self.ticket)
        self.assertEqual(stale["state"], "stale")
        current = ctl.load_metadata(self.ticket)
        self.assertEqual(current["completed_steps"], frozen["completed_steps"])
        self.assertEqual(current["review_receipts"], frozen["review_receipts"])
        self.assertEqual(reports, (self.ticket / "06_audit.md").read_bytes())

    def test_latest_failure_or_open_retry_invalidates_prior_success(self):
        self.create()
        self.finish_review("audit")
        fresh = ctl.delivery_freshness(self.ticket, required_steps=("audit",))
        self.assertTrue(fresh["ok"], fresh)
        self.call("step", "--dir", self.ticket, "--step", "audit", "--phase", "start", "--attempt", "audit-2")
        self.assertFalse(ctl.delivery_freshness(self.ticket, required_steps=("audit",))["ok"])
        self.call("step", "--dir", self.ticket, "--step", "audit", "--phase", "finish",
                  "--attempt", "audit-2", "--outcome", "failure", "--evidence", "fixture failure")
        result = ctl.delivery_freshness(self.ticket, required_steps=("audit",))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["applicable_receipts"], [])

    def test_inspection_scope_contract_is_bound_and_natural_phase_growth_is_stable(self):
        self.create()
        report = {"scopes": ["src"], "related": ["tests"], "resolved_baselines": {".": self.base}}
        (self.ticket / "code_worklist.json").write_text(json.dumps(report), encoding="utf-8")
        old = ctl.candidate_snapshot(self.ticket, ctl.load_metadata(self.ticket))
        (self.ticket / "deepcheck_worklist.json").write_text(json.dumps(report), encoding="utf-8")
        same = ctl.candidate_snapshot(self.ticket, ctl.load_metadata(self.ticket))
        self.assertEqual(old["candidate_id"], same["candidate_id"])
        report["related"] = ["other"]
        (self.ticket / "deepcheck_worklist.json").write_text(json.dumps(report), encoding="utf-8")
        different = ctl.candidate_snapshot(self.ticket, ctl.load_metadata(self.ticket))
        self.assertNotEqual(same["candidate_id"], different["candidate_id"])

    def test_lfs_hydrated_work_content_and_git_read_only_without_filters(self):
        import hashlib
        data = b"\x00raw-lfs-payload"
        digest = hashlib.sha256(data).hexdigest()
        (self.repo / "large.bin").write_text(f"version https://git-lfs.github.com/spec/v1\noid sha256:{digest}\nsize {len(data)}\n")
        self.git("add", "large.bin")
        self.git("commit", "-qm", "LFS pointer")
        (self.repo / ".gitattributes").write_text("large.bin filter=trap diff=trap\n")
        marker = self.repo / ".git/filter-called"
        self.git("config", "filter.trap.clean", "touch " + str(marker))
        self.git("config", "filter.trap.smudge", "touch " + str(marker))
        self.git("config", "diff.trap.command", "touch " + str(marker))
        (self.repo / "large.bin").write_bytes(data)
        before = (self.repo / ".git/index").read_bytes()
        candidate = self.capture()
        fact = next(x for x in candidate["manifest"] if x["path"] == "large.bin")
        self.assertEqual(fact["sha256"], digest)
        self.assertFalse(marker.exists())
        self.assertEqual(before, (self.repo / ".git/index").read_bytes())
        (self.repo / "large.bin").write_bytes(data + b"changed")
        self.assertFalse(self.module().compare_candidates(candidate, self.capture())["equivalent"])

    def test_same_content_commit_reuses_real_receipt_but_intermediate_other_content_does_not(self):
        self.create()
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.finish_review("audit")
        self.git("add", "source.py")
        self.git("commit", "-qm", "reviewed final source")
        fresh = ctl.delivery_freshness(self.ticket, required_steps=("audit",))
        self.assertTrue(fresh["ok"], fresh)
        (self.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
        self.git("commit", "-qam", "other source")
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("commit", "-qam", "restore reviewed source")
        result = ctl.delivery_freshness(self.ticket, required_steps=("audit",))
        self.assertFalse(result["ok"], result)

    def test_snapshot_digest_and_event_markers_cannot_be_forged(self):
        import copy
        old = self.capture()
        forged = copy.deepcopy(old)
        forged["manifest"][0]["sha256"] = "0" * 64
        self.assertFalse(self.module().compare_candidates(forged, old)["equivalent"])
        forged = copy.deepcopy(old)
        forged["manifest"][0]["path"] = "../escape"
        keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
                "excluded_side_effects", "control_artifact_exclusions", "manifest")
        forged["content_id"] = self.module()._digest({k: forged[k] for k in keys})
        forged["candidate_id"] = self.module()._digest({k: v for k, v in forged.items() if k != "candidate_id"})
        self.assertFalse(self.module().snapshot_identity_ok(forged))
        self.create()
        meta = ctl.load_metadata(self.ticket)
        events = ctl.read_events(self.ticket)[0]
        self.call("candidate", "--dir", self.ticket, "--phase", "capture", "--request-id", "capture")
        meta = ctl.load_metadata(self.ticket)
        events = ctl.read_events(self.ticket)[0]
        bad = copy.deepcopy(events)
        del bad[-1]["payload"]["candidate_control"]
        self.assertIn("candidate_control_event_shape", ctl.candidate_event_issues(bad, meta))
        bad = copy.deepcopy(events)
        bad[-1]["payload"]["set"]["candidate_tracking"]["current"]["manifest"][0]["sha256"] = "0" * 64
        forged_meta = copy.deepcopy(meta)
        forged_meta["candidate_tracking"] = bad[-1]["payload"]["set"]["candidate_tracking"]
        self.assertTrue(ctl.candidate_event_issues(bad, forged_meta))
        for invalid in ("not-an-object", None, {"version": 1, "current": old}):
            bad = copy.deepcopy(events)
            bad[-1]["payload"]["set"]["candidate_tracking"] = invalid
            malformed = copy.deepcopy(meta)
            malformed["candidate_tracking"] = invalid
            try:
                issues = ctl.candidate_event_issues(bad, malformed)
            except (TypeError, AttributeError) as exc:
                self.fail(f"候选非法事件须结构化失败而非崩溃: {exc}")
            self.assertTrue(issues)

    def test_scan_truncation_and_sidecars_never_silently_change_candidate(self):
        module = self.module()
        before = self.capture()
        (self.repo / ".icode_output/nested").mkdir(parents=True)
        (self.repo / ".icode_output/nested/report.md").write_text("sidecar")
        (self.repo / "demo/.icode_output/nested").mkdir(parents=True)
        (self.repo / "demo/.icode_output/nested/report.md").write_text("nested sidecar")
        self.assertEqual(before["candidate_id"], self.capture()["candidate_id"])
        with self.assertRaisesRegex(module.CandidateError, "truncation"):
            self.capture(limits={"max_git_bytes": 10})

    def test_existing_v3_capture_explicitly_enables_without_fake_review(self):
        nongit = Path(self.temp.name) / "control"
        nongit.mkdir()
        ticket = nongit / ".icode_output/.icode_output_1"
        self.call("create", "--dir", ticket, "--ticket-id", "old-v3", "--birth", "plan", "--requirement", "old")
        meta = ctl.load_metadata(ticket)
        self.assertNotIn("candidate_tracking", meta)
        self.call("bind-execution-root", "--dir", ticket, "--ticket-id", "old-v3",
                  "--execution-root", self.repo, "--request-id", "bind")
        frozen = (ticket / ctl.METADATA_NAME).read_bytes()
        self.assertEqual(self.call("candidate", "--dir", ticket, "--phase", "inspect")["state"], "legacy_untracked")
        self.assertEqual(frozen, (ticket / ctl.METADATA_NAME).read_bytes())
        self.call("candidate", "--dir", ticket, "--phase", "capture", "--request-id", "enable")
        enabled = ctl.load_metadata(ticket)
        self.assertEqual(enabled["candidate_tracking"]["mode"], "enabled")
        self.assertEqual(enabled.get("review_receipts", []), [])
        self.assertFalse(ctl.verify_event_chain(ticket, enabled)[1])

    def test_explicit_scan_budgets_are_persisted_and_do_not_change_source_identity(self):
        self.create()
        before = ctl.load_metadata(self.ticket)["candidate_tracking"]["current"]
        self.call("candidate", "--dir", self.ticket, "--phase", "capture", "--request-id", "limits",
                  "--limits-json", '{"max_bytes":1048576,"max_files":100}')
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["candidate_tracking"]["limits"]["max_files"], 100)
        self.assertEqual(meta["candidate_tracking"]["current"]["candidate_id"], before["candidate_id"])
        frozen = self.bytes()
        self.call("candidate", "--dir", self.ticket, "--phase", "capture", "--request-id", "too-small",
                  "--limits-json", '{"max_bytes":1}', ok=False)
        self.assertEqual(frozen, self.bytes())
        other = self.repo / ".icode_output/.icode_output_2"
        result = self.call("create", "--dir", other, "--ticket-id", "candidate-2", "--birth", "plan",
                          "--requirement", "bounded", "--candidate-limits-json", '{"max_bytes":1}', ok=False)
        self.assertEqual(result["gate_id"], "candidate_capture")
        self.assertFalse((other / ctl.METADATA_NAME).exists())

    def test_existing_git_access_failure_is_not_downgraded_to_legacy(self):
        module = self.module()
        with patch.object(module, "_git", side_effect=module.CandidateError("git unavailable")):
            with self.assertRaises(module.CandidateError):
                module.repository_head(self.repo)

    def test_checkout_replacement_changes_repository_identity(self):
        old = self.capture()
        saved = Path(self.temp.name) / "saved"
        self.repo.rename(saved)
        proc = subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(saved), str(self.repo)],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        new = self.capture()
        self.assertNotEqual(old["identity"], new["identity"])
        self.assertFalse(self.module().compare_candidates(old, new)["equivalent"])

    def test_source_outside_review_scope_cannot_drift_during_audit(self):
        (self.repo / "elsewhere.txt").write_text("initial", encoding="utf-8")
        self.git("add", "elsewhere.txt")
        self.git("commit", "-qm", "outside scope")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.create()
        result = self.finish_review("audit", scope="source.py", after_reads=lambda:
                   (self.repo / "elsewhere.txt").write_text("changed during audit", encoding="utf-8"), ok=False)
        self.assertEqual(result["gate_id"], "candidate_review_drift")
        self.assertEqual(ctl.load_metadata(self.ticket)["review_receipts"], [])

    def test_new_git_ticket_auto_enables_and_query_is_read_only(self):
        self.create()
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual((meta.get("candidate_tracking") or {}).get("mode"), "enabled",
                         "新Git工单必须默认绑定最终源码")
        before = self.bytes()
        result = self.call("candidate", "--dir", self.ticket, "--phase", "inspect")
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(before, self.bytes())

    def test_binary_mode_rename_delete_staged_and_unstaged_are_bound(self):
        old = self.capture()
        (self.repo / "source.py").write_bytes(b"\x00\xff staged")
        self.git("add", "source.py")
        staged = self.capture()
        (self.repo / "source.py").write_bytes(b"\x00\xfe unstaged")
        unstaged = self.capture()
        for other in (staged, unstaged):
            self.assertFalse(self.module().compare_candidates(old, other)["equivalent"])
        self.assertNotEqual(staged["candidate_id"], unstaged["candidate_id"])
        os.chmod(self.repo / "source.py", 0o755)
        mode = self.capture()
        self.assertNotEqual(mode["candidate_id"], unstaged["candidate_id"])
        self.git("mv", "source.py", "renamed.py")
        rename = self.capture()
        self.assertNotEqual(rename["candidate_id"], mode["candidate_id"])
        (self.repo / "renamed.py").unlink()
        self.assertNotEqual(self.capture()["candidate_id"], rename["candidate_id"])

    def test_untracked_scope_exclusions_checkout_and_base_drift(self):
        old = self.capture()
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        new = self.capture()
        self.assertFalse(self.module().compare_candidates(old, new)["equivalent"])
        excluded = self.capture(excluded_side_effects=["new.txt"])
        self.assertFalse(self.module().compare_candidates(new, excluded)["equivalent"])
        (self.repo / "new.txt").write_text("changed exclusion", encoding="utf-8")
        self.assertTrue(self.module().compare_candidates(excluded, self.capture(excluded_side_effects=["new.txt"]))["equivalent"])
        for kwargs in ({"code_files": ["renamed.py"]}, {"scope_contract": {"scopes": ["src"]}},
                       {"inspection_scope": {"audit": {"scopes": ["src"], "related": ["test.py"]}}}):
            self.assertFalse(self.module().compare_candidates(new, self.capture(**kwargs))["equivalent"])
        worktree = Path(self.temp.name) / "other"
        self.git("worktree", "add", "-q", "-b", "other", str(worktree))
        other = self.module().capture_candidate(worktree, base=self.base, code_files=["source.py"],
                     scope_contract=None, excluded_side_effects=[], inspection_scope={})
        self.assertFalse(self.module().compare_candidates(old, other)["equivalent"])

    def test_same_content_commit_equivalence_and_rebase_merge_rejection(self):
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        old = self.capture()
        self.git("add", "source.py")
        self.git("commit", "-qm", "same final source")
        current = self.capture()
        proof = self.module().compare_candidates(old, current)
        self.assertTrue(proof["equivalent"], proof)
        self.assertEqual(proof["reason"], "same_content_commit")
        self.git("commit", "--amend", "-qm", "rewritten history")
        self.assertFalse(self.module().compare_candidates(current, self.capture())["equivalent"])
        self.assertFalse(self.module().compare_candidates(old, self.capture(base=self.git("rev-parse", "HEAD").strip()))["equivalent"])

    def test_bounds_and_submodule_dirty_fail_closed(self):
        module = self.module()
        with self.assertRaises(module.CandidateError):
            self.capture(limits={"max_files": 1})
        with self.assertRaises(module.CandidateError):
            self.capture(limits={"max_bytes": 1})
        nested = Path(self.temp.name) / "nested"
        nested.mkdir()
        self.git("init", "-q", cwd=nested)
        self.git("config", "user.email", "test@example.invalid", cwd=nested)
        self.git("config", "user.name", "Candidate Test", cwd=nested)
        (nested / "x").write_text("x", encoding="utf-8")
        self.git("add", ".", cwd=nested)
        self.git("commit", "-qm", "sub", cwd=nested)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(nested), "sub")
        self.git("commit", "-qam", "add submodule")
        self.capture()
        (self.repo / "sub/x").write_text("dirty", encoding="utf-8")
        with self.assertRaisesRegex(module.CandidateError, "submodule"):
            self.capture()

    def test_legacy_read_has_no_migration_or_fake_receipts(self):
        self.ticket.mkdir(parents=True)
        old = {"ticket_id": "old", "status": "completed", "completed_steps": ["4", "5", "6"],
               "project_path": str(self.repo)}
        (self.ticket / ctl.METADATA_NAME).write_text(json.dumps(old), encoding="utf-8")
        before = (self.ticket / ctl.METADATA_NAME).read_bytes()
        result = self.call("candidate", "--dir", self.ticket, "--phase", "inspect")
        self.assertEqual(result["state"], "legacy_untracked")
        self.assertEqual(result["applicable_receipts"], [])
        self.assertEqual(before, (self.ticket / ctl.METADATA_NAME).read_bytes())
        self.assertFalse((self.ticket / ctl.EVENTS_NAME).exists())

    def test_explicit_capture_protected_fields_replay_and_transaction_rollback(self):
        self.create()
        self.call("candidate", "--dir", self.ticket, "--phase", "capture",
                  "--exclude", "build", "--request-id", "capture")
        first = self.bytes()
        replay = self.call("candidate", "--dir", self.ticket, "--phase", "capture",
                  "--exclude", "build", "--request-id", "capture")
        self.assertTrue(replay["already_applied"])
        self.assertEqual(first, self.bytes())
        for field in ("candidate_tracking", "review_receipts", "excluded_side_effects"):
            result = self.call("metadata-update", "--dir", self.ticket, "--set-json",
                               json.dumps({field: []}), ok=False)
            self.assertEqual(result["gate_id"], "metadata_update_protected")
        from argparse import Namespace
        args = Namespace(dir=str(self.ticket), phase="capture", base=None,
                         exclude=["new-build"], request_id="next", require_step=[])
        with patch.object(ctl, "append_prepared_event", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                ctl.cmd_candidate(args)
        self.assertEqual(first, self.bytes())


if __name__ == "__main__":
    unittest.main()
