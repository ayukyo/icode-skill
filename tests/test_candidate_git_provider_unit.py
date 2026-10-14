"""Software-only typed candidate reads. No host identity/native/cleanup credit."""
import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/icode_candidate.py"
spec = importlib.util.spec_from_file_location("candidate_provider_test", TOOL)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def call(function, *args, provider=None, required=False, **kwargs):
    parameters = inspect.signature(function).parameters
    if "git_provider" in parameters:
        kwargs["git_provider"] = provider
    if "require_git_provider" in parameters:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class FixtureProvider(getattr(api, "CandidateGitProvider", object)):
    def __init__(self, root, facts, tree, index, others, blobs):
        self.workspace = root
        self.facts = facts
        self.tree = tree
        self.index = index
        self.others = others
        self.blobs = blobs
        self.proof = b""
        self.trees = {}
        self.calls = []
        self.fault = None
        self.raw_facts = None

    def assert_context(self):
        if self.fault in ("attrs_drift", "cleanup_failed", "binding_missing"):
            raise api.CandidateError(self.fault)

    def query(self, request, *, limit):
        operation = request.operation.value
        self.calls.append(operation)
        if operation == "facts":
            data = (self.raw_facts if self.raw_facts is not None
                    else json.dumps(self.facts, sort_keys=True).encode("utf-8"))
        elif operation == "tree":
            data = self.trees.get(request.oid, self.tree)
        elif operation == "index":
            data = self.index
        elif operation == "others":
            data = self.others
        elif operation == "blob":
            if request.oid not in self.blobs:
                raise api.CandidateError("missing closed blob")
            data = (str(len(self.blobs[request.oid])).encode("ascii") if request.kind == "size"
                    else self.blobs[request.oid])
        elif operation == "linear":
            data = self.proof
        else:
            raise AssertionError("nonclosed operation")
        if self.fault == "overflow":
            data = b"x" * (limit + 1)
        if self.fault == "unterminated" and operation == "index":
            data = data.rstrip(b"\0")
        if self.fault == "duplicate" and operation == "index":
            data = data + data
        if self.fault == "escape" and operation == "others":
            data = b"../escape\0"
        if self.fault == "bad_blob" and operation == "blob" and request.kind == "content":
            data = b"not the requested blob"
        result = getattr(api, "CandidateReadResult", SimpleNamespace)
        return result(data=data, exit_code=False if self.fault == "bool_exit" else 0,
                      truncated="" if self.fault == "bad_truncated" else False,
                      error="query_failed" if self.fault == "error" else None)


class CandidateProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-candidate-provider-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.metadata = self.root / "metadata"
        self.code = self.root / "code"
        self.metadata.mkdir()
        self.code.mkdir()
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "Software Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.metadata / "a.py").write_bytes(b"value = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        (self.code / "a.py").write_bytes(b"value = 2\n")
        common = (self.metadata / ".git").resolve()
        common_stat, code_stat = common.stat(), self.code.stat()
        identity = {"common_git_dir": str(common), "checkout_realpath": str(self.code),
                    "object_format": "sha1", "common_git_device": common_stat.st_dev,
                    "common_git_inode": common_stat.st_ino, "checkout_device": code_stat.st_dev,
                    "checkout_inode": code_stat.st_ino}
        facts = {"identity": identity, "base": self.base, "head": self.base,
                 "head_tree": self.git("rev-parse", "HEAD^{tree}").decode().strip()}
        tree = self.git("ls-tree", "-rz", "--full-tree", "HEAD")
        index = self.git("ls-files", "--stage", "-z")
        blob = self.git("rev-parse", "HEAD:a.py").decode().strip()
        self.provider = FixtureProvider(self.code, facts, tree, index, b"", {blob: b"value = 1\n"})

    def git(self, *args):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")
        return subprocess.check_output(["git", "-C", str(self.metadata), "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false", "-c", "gc.auto=0", *args],
            stderr=subprocess.DEVNULL, env=environment)

    def capture(self, **kwargs):
        return call(api.capture_candidate, self.code, base=self.base,
                    provider=self.provider, required=True, **kwargs)

    def software_snapshot(self):
        try:
            result = self.capture()
        except api.CandidateError as error:
            self.fail("isolated current CODE must be capturable: " + str(error))
        self.assertTrue(api.snapshot_identity_ok(result))
        return result

    def test_no_git_code_uses_real_bytes(self):
        self.assertFalse((self.code / ".git").exists())
        self.assertEqual(self.base, call(api.repository_head, self.code, provider=self.provider, required=True))
        with patch.object(api, "_git", side_effect=api.CandidateError("raw fallback")):
            snapshot = self.software_snapshot()
        row = next(row for row in snapshot["manifest"] if row["path"] == "a.py")
        self.assertEqual(hashlib.sha256(b"value = 2\n").hexdigest(), row["sha256"])
        self.assertNotEqual(hashlib.sha256(b"value = 1\n").hexdigest(), row["sha256"])
        self.assertEqual(str(self.code), snapshot["identity"]["checkout_realpath"])

    def test_required_missing_provider_never_accepts_identical(self):
        ordinary = api.capture_candidate(self.metadata, base=self.base)
        result = call(api.compare_candidates, ordinary, ordinary, provider=None, required=True)
        self.assertFalse(result["equivalent"], result)
        self.assertFalse(call(api.review_source_equivalence, ordinary, ordinary,
                              provider=None, required=True)["equivalent"])
        with self.assertRaises(api.CandidateError):
            call(api.repository_head, self.metadata, provider=None, required=True)
        with self.assertRaises(api.CandidateError):
            call(api.capture_candidate, self.metadata, base=self.base, provider=None, required=True)
        fresh = call(api.delivery_freshness, {"candidate_tracking": {"mode": "enabled"}}, ordinary,
                     provider=None, required=True)
        self.assertEqual("blocked", fresh["state"])
        self.assertIn("candidate_provider_context", fresh["reasons"])

    def test_plain_none_path_and_legacy_remain_unchanged(self):
        ordinary = api.capture_candidate(self.metadata, base=self.base)
        explicit = call(api.capture_candidate, self.metadata, base=self.base, provider=None)
        self.assertEqual(ordinary, explicit)
        self.assertEqual(api.repository_head(self.metadata),
                         call(api.repository_head, self.metadata, provider=None))
        self.assertIsNone(api.repository_head(self.code))
        self.assertTrue(call(api.compare_candidates, ordinary, explicit, provider=None)["equivalent"])
        self.assertTrue(call(api.review_source_equivalence, ordinary, explicit, provider=None)["equivalent"])
        self.assertEqual("legacy_untracked", call(api.delivery_freshness, {}, ordinary, provider=None)["state"])

    def test_provider_failure_and_drift_never_capture(self):
        for fault in ("attrs_drift", "cleanup_failed", "binding_missing", "overflow", "unterminated",
                      "duplicate", "escape", "bool_exit", "bad_truncated", "error", "bad_blob"):
            with self.subTest(fault=fault):
                self.provider.fault = fault
                with self.assertRaises(api.CandidateError):
                    self.capture()
        self.provider.fault = None
        self.provider.workspace = self.metadata
        with self.assertRaises(api.CandidateError):
            self.capture()

    def test_limits_untracked_deleted_and_none_legacy(self):
        before = self.software_snapshot()
        (self.code / "new.txt").write_bytes(b"new\n")
        self.provider.others = b"new.txt\0"
        after = self.software_snapshot()
        self.assertFalse(call(api.compare_candidates, before, after, provider=self.provider, required=True)["equivalent"])
        with self.assertRaises(api.CandidateError):
            self.capture(limits={"max_files": 1})
        with self.assertRaises(api.CandidateError):
            self.capture(limits={"max_bytes": 1})
        with self.assertRaises(api.CandidateError):
            self.capture(limits={"max_git_bytes": 1})
        (self.code / "a.py").unlink()
        self.assertNotIn("a.py", {row["path"] for row in self.software_snapshot()["manifest"]})
        original = api.capture_candidate(self.metadata, base=self.base)
        explicit = call(api.capture_candidate, self.metadata, base=self.base, provider=None)
        self.assertEqual(original, explicit)
        self.assertIsNone(api.repository_head(self.code))

    def committed_pair(self):
        old = self.software_snapshot()
        (self.metadata / "a.py").write_bytes(b"value = 2\n")
        self.git("add", "a.py")
        self.git("commit", "-qm", "reviewed bytes")
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.provider.facts["head"] = head
        self.provider.facts["head_tree"] = self.git("rev-parse", "HEAD^{tree}").decode().strip()
        self.provider.tree = self.git("ls-tree", "-rz", "--full-tree", "HEAD")
        self.provider.index = self.git("ls-files", "--stage", "-z")
        self.provider.proof = (head + " " + self.base + "\n").encode("ascii")
        self.provider.trees[head] = self.provider.tree
        return old, self.software_snapshot(), head

    def test_finite_linear_software_proof_and_internal_review_consumer(self):
        old, current, head = self.committed_pair()
        with patch.object(api, "_git", side_effect=api.CandidateError("raw fallback")):
            proof = call(api.compare_candidates, old, current, provider=self.provider, required=True)
            self.assertTrue(proof["equivalent"], proof)
            self.assertEqual("same_content_commit", proof["reason"])
            self.assertTrue(call(api.review_source_equivalence, old, current,
                                 provider=self.provider, required=True)["equivalent"])
        # Old anchor still has value=1; only (old,new] is compared to value=2.
        self.assertEqual(b"value = 1\n", self.git("show", self.base + ":a.py"))
        self.assertNotEqual(old["head_tree"], current["head_tree"])
        for data in (b"", (head + " " + "0" * 40 + "\n").encode(),
                     (head + " " + self.base + " " + self.base + "\n").encode(),
                     (head + " " + self.base + "\n").encode() * 65):
            with self.subTest(proof=data[:80]):
                self.provider.proof = data
                self.assertFalse(call(api.compare_candidates, old, current,
                                      provider=self.provider, required=True)["equivalent"])
        self.provider.proof = (head + " " + self.base + "\n").encode()
        self.provider.trees[head] = self.git("ls-tree", "-rz", "--full-tree", self.base)
        self.assertFalse(call(api.compare_candidates, old, current,
                              provider=self.provider, required=True)["equivalent"])

    def test_intermediate_tree_and_amend_cannot_reuse_review(self):
        old, reviewed, first = self.committed_pair()
        (self.metadata / "a.py").write_bytes(b"value = 3\n")
        self.git("commit", "-qam", "intermediate different content")
        (self.metadata / "a.py").write_bytes(b"value = 2\n")
        self.git("commit", "-qam", "restore final content")
        head = self.git("rev-parse", "HEAD").decode().strip()
        records = self.git("rev-list", "--parents", "--ancestry-path", self.base + ".." + head)
        self.provider.facts["head"] = head
        self.provider.proof = records
        for line in records.decode().splitlines():
            commit = line.split()[0]
            self.provider.trees[commit] = self.git("ls-tree", "-rz", "--full-tree", commit)
        current = self.software_snapshot()
        self.assertFalse(call(api.compare_candidates, old, current,
                              provider=self.provider, required=True)["equivalent"])
        # Amend replaces the reviewed head rather than extending it.
        reviewed, first = current, head
        self.git("commit", "--amend", "-qm", "amended reviewed commit")
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.provider.facts["head"] = head
        self.provider.proof = self.git("rev-list", "--parents", "--ancestry-path", first + ".." + head)
        self.provider.trees[head] = self.git("ls-tree", "-rz", "--full-tree", head)
        self.assertFalse(call(api.compare_candidates, reviewed, self.software_snapshot(),
                              provider=self.provider, required=True)["equivalent"])

    def test_real_linear_64_boundary_and_65_rejected(self):
        old = self.software_snapshot()
        (self.metadata / "a.py").write_bytes(b"value = 2\n")
        self.git("add", "a.py")
        for index in range(64):
            self.git("commit", "--allow-empty", "-qm", "linear fixture " + str(index))
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.provider.facts.update(head=head,
            head_tree=self.git("rev-parse", "HEAD^{tree}").decode().strip())
        self.provider.tree = self.git("ls-tree", "-rz", "--full-tree", "HEAD")
        self.provider.index = self.git("ls-files", "--stage", "-z")
        self.provider.proof = self.git("rev-list", "--parents", "--ancestry-path", self.base + ".." + head)
        self.assertEqual(64, len(self.provider.proof.splitlines()))
        self.assertTrue(call(api.compare_candidates, old, self.software_snapshot(),
                             provider=self.provider, required=True)["equivalent"])
        self.git("commit", "--allow-empty", "-qm", "linear fixture 65")
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.provider.facts["head"] = head
        self.provider.proof = self.git("rev-list", "--parents", "--ancestry-path", self.base + ".." + head)
        self.assertEqual(65, len(self.provider.proof.splitlines()))
        self.assertFalse(call(api.compare_candidates, old, self.software_snapshot(),
                              provider=self.provider, required=True)["equivalent"])

    def test_large_ordinary_blob_does_not_require_pointer_content(self):
        large = b"x" * 5000
        (self.metadata / "a.py").write_bytes(large)
        self.git("commit", "-qam", "large ordinary baseline")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        self.provider.facts.update(base=self.base, head=self.base,
            head_tree=self.git("rev-parse", "HEAD^{tree}").decode().strip())
        self.provider.tree = self.git("ls-tree", "-rz", "--full-tree", "HEAD")
        self.provider.index = self.git("ls-files", "--stage", "-z")
        oid = self.git("rev-parse", "HEAD:a.py").decode().strip()
        self.provider.blobs[oid] = large
        row = self.software_snapshot()["manifest"][0]
        self.assertEqual(hashlib.sha256(b"value = 2\n").hexdigest(), row["sha256"])
        self.assertNotEqual(oid, row["git_oid"])

    def test_metadata_identity_and_base_mismatch_reject(self):
        original = copy.deepcopy(self.provider.facts)
        for field, value in (("checkout_inode", True), ("checkout_realpath", str(self.metadata)),
                             ("common_git_inode", -1), ("object_format", "sha256")):
            with self.subTest(field=field):
                self.provider.facts = copy.deepcopy(original)
                self.provider.facts["identity"][field] = value
                with self.assertRaises(api.CandidateError):
                    self.capture()
        self.provider.facts = copy.deepcopy(original)
        self.provider.facts["base"] = "0" * 40
        with self.assertRaises(api.CandidateError):
            self.capture()
        self.provider.facts = original

    def capture_name(self, operation, name, **kwargs):
        # Exercise capture through each metadata source, not just the parser.
        original = self.provider.tree, self.provider.index, self.provider.others
        oid = next(iter(self.provider.blobs))
        raw = name.encode("utf-8")
        try:
            if operation == "others":
                self.provider.others = raw + b"\0"
            elif operation == "tree":
                self.provider.tree = b"100644 blob " + oid.encode() + b"\t" + raw + b"\0"
            else:
                self.provider.index = b"100644 " + oid.encode() + b" 0\t" + raw + b"\0"
            return self.capture(**kwargs)
        finally:
            self.provider.tree, self.provider.index, self.provider.others = original

    def actual_name_bytes(self, name):
        path = self.code / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ("actual POSIX bytes: " + name).encode("utf-8")
        path.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def test_provider_path_drive_root_and_ads_follow_native_rules(self):
        for operation in ("others", "tree", "index"):
            for name in ("D:outside", "/outside", "a.py:stream"):
                with self.subTest(operation=operation, name=name):
                    if os.name == "nt" or name == "/outside":
                        with self.assertRaises(api.CandidateError):
                            self.capture_name(operation, name)
                    else:
                        # Colon is an ordinary legal POSIX filename character.
                        expected = self.actual_name_bytes(name)
                        snapshot = self.capture_name(operation, name)
                        self.assertEqual(expected, next(row["sha256"] for row in snapshot["manifest"]
                                                       if row["path"] == name))

    def test_provider_win32_aliases_and_safe_posix_names(self):
        aliases = ("CON", "folder/NUL.txt", "trailing.", "trailing ", "a?.py", "bad\x01name",
                   ".GiT/config", "nested/.GIT/config")
        for operation in ("others", "tree", "index"):
            for name in aliases:
                with self.subTest(operation=operation, name=name):
                    if os.name == "nt":
                        if ".git" in name.lower().split("/"):
                            self.actual_name_bytes(name)
                        with patch.object(api.os, "open", side_effect=AssertionError("read before rejection")):
                            with self.assertRaises(api.CandidateError):
                                self.capture_name(operation, name, excluded_side_effects=["folder", "nested"])
                    else:
                        expected = self.actual_name_bytes(name)
                        snapshot = self.capture_name(operation, name)
                        self.assertEqual(expected, next(row["sha256"] for row in snapshot["manifest"]
                                                       if row["path"] == name))
            expected = self.actual_name_bytes("src/safe.py")
            snapshot = self.capture_name(operation, "src/safe.py")
            self.assertEqual(expected, next(row["sha256"] for row in snapshot["manifest"]
                                           if row["path"] == "src/safe.py"))

    def test_explicit_capture_paths_validate_before_exclusion_or_read(self):
        unsafe = ("D:outside", "/outside", "a.py:stream", "CON", "folder/NUL.txt",
                  "trailing.", "trailing ", "a?.py", "bad\x01name", ".GiT/config", "nested/.GIT/config")
        for field in ("code_files", "excluded_side_effects"):
            for name in unsafe:
                with self.subTest(field=field, name=name):
                    if os.name == "nt" or name == "/outside":
                        if os.name == "nt" and ".git" in name.lower().split("/"):
                            self.actual_name_bytes(name)
                        # Any file read before rejection is an independent failure.
                        with patch.object(api.os, "open", side_effect=AssertionError("read before rejection")):
                            with self.assertRaises(api.CandidateError):
                                self.capture(**{field: [name]})
                    else:
                        expected = self.actual_name_bytes(name)
                        snapshot = self.capture(**{field: [name]})
                        rows = {row["path"]: row for row in snapshot["manifest"]}
                        if field == "code_files":
                            self.assertEqual(expected, rows[name]["sha256"])
                        else:
                            self.assertNotIn(name, rows)
        expected = self.actual_name_bytes("src/safe.py")
        snapshot = self.capture(code_files=["src/safe.py"], excluded_side_effects=["safe-excluded"])
        self.assertEqual(expected, next(row["sha256"] for row in snapshot["manifest"]
                                       if row["path"] == "src/safe.py"))

    def test_explicit_capture_falsey_arrays_reject_without_changing_none(self):
        for field in ("code_files", "excluded_side_effects"):
            for value in (False, 0, "", {}):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(api.CandidateError):
                        self.capture(**{field: value})
            for value in (None, [], ()):
                with self.subTest(field=field, accepted=value):
                    self.assertTrue(api.snapshot_identity_ok(self.capture(**{field: value})))

    def test_explicit_ads_actual_bytes_cannot_be_captured_on_windows(self):
        name = "a.py:stream"
        payload = b"candidate ADS fixture bytes"
        (self.code / name).write_bytes(payload)
        self.assertEqual(payload, (self.code / name).read_bytes())
        if os.name == "nt":
            with self.assertRaises(api.CandidateError):
                self.capture(code_files=[name])
            with self.assertRaises(api.CandidateError):
                self.capture(excluded_side_effects=[name])
        else:
            snapshot = self.capture(code_files=[name])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), next(
                row["sha256"] for row in snapshot["manifest"] if row["path"] == name))

    def test_duplicate_facts_keys_reject_at_every_object_level(self):
        facts = self.provider.facts
        ordinary = json.dumps(facts).encode("utf-8")
        for value in ("0" * 40, facts["head"]):
            with self.subTest(level="top", same=value == facts["head"]):
                self.provider.raw_facts = b'{"head":' + json.dumps(value).encode() + b',' + ordinary[1:]
                with self.assertRaises(api.CandidateError):
                    self.capture()
        identity = json.dumps(facts["identity"]).encode("utf-8")
        for value in (True, facts["identity"]["checkout_inode"]):
            with self.subTest(level="identity", same=type(value) is int):
                repeated = (b'{"checkout_inode":' + json.dumps(value).encode() + b',' + identity[1:])
                prefix = json.dumps({key: value for key, value in facts.items() if key != "identity"}).encode()
                self.provider.raw_facts = prefix[:-1] + b',"identity":' + repeated + b'}'
                with self.assertRaises(api.CandidateError):
                    self.capture()
        self.provider.raw_facts = None
        self.assertTrue(api.snapshot_identity_ok(self.capture()))

    def test_linear_query_time_is_inside_one_fixed_deadline(self):
        old, current, head = self.committed_pair()
        clock = [0.0]
        query = self.provider.query
        def timed(request, *, limit):
            result = query(request, limit=limit)
            if request.operation.value == "linear":
                clock[0] = 31.0
            return result
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.provider, "query", side_effect=timed):
            proof = call(api.compare_candidates, old, current, provider=self.provider, required=True)
        self.assertFalse(proof["equivalent"], proof)

    def test_last_tree_query_cannot_pass_after_deadline(self):
        old, current, head = self.committed_pair()
        clock = [0.0]
        query = self.provider.query
        def timed(request, *, limit):
            result = query(request, limit=limit)
            if request.operation.value == "tree":
                clock[0] = 31.0
            return result
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.provider, "query", side_effect=timed):
            proof = call(api.compare_candidates, old, current, provider=self.provider, required=True)
        self.assertFalse(proof["equivalent"], proof)

    def test_context_time_also_blocks_identical_fastpath(self):
        current = self.software_snapshot()
        clock = [0.0]
        def slow_context():
            clock[0] = 31.0
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.provider, "assert_context", side_effect=slow_context):
            proof = call(api.compare_candidates, current, current, provider=self.provider, required=True)
        self.assertFalse(proof["equivalent"], proof)

    def test_freshness_consumer_requires_same_context_and_latest_event(self):
        candidate = self.software_snapshot()
        receipt = {"step": "audit", "attempt": "software-a1", "event_id": "software-e1",
                   "candidate_id": candidate["candidate_id"], "candidate": candidate}
        event = {"event_id": "software-e1", "event_type": "step_finished", "payload": {
            "step": "audit", "attempt": "software-a1", "outcome": "success", "candidate_receipt": receipt}}
        meta = {"candidate_tracking": {"mode": "enabled"}, "review_receipts": [receipt]}
        result = call(api.delivery_freshness, meta, candidate, [event], required_steps=("audit",),
                      provider=self.provider, required=True)
        self.assertTrue(result["ok"], result)
        self.provider.fault = "cleanup_failed"
        self.assertFalse(call(api.delivery_freshness, meta, candidate, [event], required_steps=("audit",),
                              provider=self.provider, required=True)["ok"])
        self.provider.fault = None
        retry = {"event_id": "software-e2", "event_type": "step_started", "payload": {"step": "audit"}}
        self.assertFalse(call(api.delivery_freshness, meta, candidate, [event, retry], required_steps=("audit",),
                              provider=self.provider, required=True)["ok"])


if __name__ == "__main__":
    unittest.main()
