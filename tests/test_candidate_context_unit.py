"""B1 software context only; real local ports, no production host capability."""
import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_control(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/icode_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ctl = load_control("b1_control")
api = ctl.candidate_module()
PENDING = "candidate_business_propagation_pending"


def invoke(function, *args, provider=None, required=False, **kwargs):
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class Provider(api.CandidateGitProvider):
    def __init__(self, workspace, facts, tree, index, blobs):
        self.workspace, self.facts = workspace, facts
        self.tree, self.index, self.blobs = tree, index, blobs
        self.fault = False

    def assert_context(self):
        if self.fault:
            raise api.CandidateError("fixture context expired")

    def query(self, request, *, limit):
        operation = request.operation.value
        if operation == "facts":
            data = json.dumps(self.facts).encode("utf-8")
        elif operation == "tree":
            data = self.tree
        elif operation == "index":
            data = self.index
        elif operation == "others":
            data = b""
        elif operation == "blob":
            blob = self.blobs[request.oid]
            data = str(len(blob)).encode("ascii") if request.kind == "size" else blob
        else:
            raise api.CandidateError("B1 fixture has no history proof")
        return api.CandidateReadResult(data=data, exit_code=0, truncated=False)


class CandidateContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="icode-b1-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo, self.code = self.root / "metadata", self.root / "code"
        self.repo.mkdir()
        self.code.mkdir()
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "B1 Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / ".gitignore").write_bytes(b".icode_output/\n")
        (self.repo / "a.py").write_bytes(b"value = 1\n")
        (self.code / ".gitignore").write_bytes(b".icode_output/\n")
        (self.code / "a.py").write_bytes(b"value = 2\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        self.ticket = self.repo / ".icode_output/.icode_output_1"
        self.command("create", "--dir", self.ticket, "--ticket-id", "b1",
                     "--birth", "plan", "--requirement", "B1 software fixture",
                     "--metadata-json", '{"code_files":["a.py"]}', "--request-id", "birth")
        common, code_stat = (self.repo / ".git").resolve(), self.code.stat()
        common_stat = common.stat()
        identity = {"common_git_dir": str(common), "checkout_realpath": str(self.code),
                    "object_format": "sha1", "common_git_device": common_stat.st_dev,
                    "common_git_inode": common_stat.st_ino, "checkout_device": code_stat.st_dev,
                    "checkout_inode": code_stat.st_ino}
        facts = {"identity": identity, "base": self.base, "head": self.base,
                 "head_tree": self.git("rev-parse", "HEAD^{tree}").decode().strip()}
        tree, index = self.git("ls-tree", "-rz", "HEAD"), self.git("ls-files", "--stage", "-z")
        blobs = {}
        for row in tree.split(b"\0"):
            if row:
                oid = row.split(b"\t", 1)[0].split()[2].decode("ascii")
                blobs[oid] = self.git("cat-file", "blob", oid)
        self.provider = Provider(self.code, facts, tree, index, blobs)

    def git(self, *args):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")
        result = subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=" + os.devnull,
                                 "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
                                 "-c", "gc.auto=0", *args], capture_output=True, timeout=15, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def command(self, *args):
        parsed = ctl.build_parser().parse_args(list(map(str, args)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(parsed.func(parsed), 0)

    def bind(self):
        self.command("bind-execution-root", "--dir", self.ticket, "--ticket-id", "b1",
                     "--execution-root", self.code, "--request-id", "bind")

    def meta(self):
        return ctl.load_metadata(self.ticket)

    def frozen(self):
        return tuple((self.ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))

    def test_plain_none_keeps_existing_context(self):
        meta, before = self.meta(), self.frozen()
        self.assertEqual(ctl.candidate_snapshot(self.ticket, meta),
                         invoke(ctl.candidate_snapshot, self.ticket, meta))
        first, second = ctl.verification_context(self.ticket, meta), invoke(ctl.verification_context, self.ticket, meta)
        self.assertEqual(first, second)
        self.assertNotIn("_candidate_b1_pending", first)
        self.assertFalse(ctl.verify_event_chain(self.ticket, meta)[1])
        self.assertEqual(before, self.frozen())

    def test_provider_reads_real_non_git_code(self):
        self.bind()
        before = self.frozen()
        self.assertFalse((self.code / ".git").exists())
        try:
            with patch.object(api, "_git", side_effect=AssertionError("raw Git fallback")):
                current = invoke(ctl.candidate_snapshot, self.ticket, self.meta(),
                                 provider=self.provider, required=True)
        except ctl.ControlError as exc:
            self.fail("B1 must capture current non-Git CODE: " + str(exc))
        row = next(row for row in current["manifest"] if row["path"] == "a.py")
        self.assertEqual(row["sha256"], hashlib.sha256(b"value = 2\n").hexdigest())
        self.assertEqual(current["identity"]["checkout_realpath"], str(self.code))
        self.assertEqual(before, self.frozen())

    def test_required_missing_blocks_real_git(self):
        before = self.frozen()
        with self.assertRaises(ctl.ControlError):
            invoke(ctl.candidate_snapshot, self.ticket, self.meta(), required=True)
        self.assertEqual(before, self.frozen())

    def test_wrong_provider_class_root_and_flag_reject(self):
        self.bind()
        before = self.frozen()
        wrong_root = Provider(self.repo, self.provider.facts, self.provider.tree,
                              self.provider.index, self.provider.blobs)
        for provider, required in ((object(), True), (wrong_root, True), (self.provider, 0)):
            with self.subTest(provider_type=type(provider).__name__, required=required):
                with self.assertRaises(ctl.ControlError):
                    invoke(ctl.candidate_snapshot, self.ticket, self.meta(), provider=provider, required=required)
        for field in ("code_files", "excluded_side_effects"):
            with self.subTest(falsey_field=field):
                meta = dict(self.meta(), **{field: False})
                with self.assertRaises(ctl.ControlError):
                    invoke(ctl.candidate_snapshot, self.ticket, meta, events=[], provider=self.provider, required=True)
        self.assertEqual(before, self.frozen())

    def test_context_trio_one_verify_and_business_eligible(self):
        self.bind()
        before = self.frozen()
        with patch.object(ctl, "verify_event_chain", wraps=ctl.verify_event_chain) as verify, \
                patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture:
            ctx = invoke(ctl.verification_context, self.ticket, self.meta(), provider=self.provider, required=True)
        self.assertIs(ctx["candidate_api"], api)
        self.assertIsNotNone(ctx["current"], ctx)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(ctx["reasons"], [])
        self.assertTrue(ctl.verification_evidence_module().assess(self.meta(), ctx,
                        git_provider=self.provider, require_git_provider=True)["ok"])
        self.assertEqual(before, self.frozen())

    def test_partial_or_foreign_trio_never_reloads(self):
        self.bind()
        evidence = ctl.verification_evidence_module()
        for missing in ("capture", "verify", "candidate_api"):
            trio = dict(capture=ctl.candidate_snapshot, verify=ctl.verify_event_chain, candidate_api=api)
            trio[missing] = None
            with self.subTest(missing=missing), patch.object(evidence, "control_module") as reload:
                ctx = invoke(evidence.context, self.ticket, self.meta(), provider=self.provider,
                             required=True, **trio)
                self.assertIsNone(ctx["current"])
                self.assertTrue(ctx["reasons"])
                reload.assert_not_called()
        other = load_control("b1_other_control")
        for trio in (dict(capture=other.candidate_snapshot, verify=ctl.verify_event_chain, candidate_api=api),
                     dict(capture=ctl.candidate_snapshot, verify=other.verify_event_chain, candidate_api=api),
                     dict(capture=ctl.candidate_snapshot, verify=ctl.verify_event_chain, candidate_api=other.candidate_module())):
            with self.subTest(foreign=tuple(id(value) for value in trio.values())):
                ctx = invoke(evidence.context, self.ticket, self.meta(), provider=self.provider, required=True, **trio)
                self.assertIsNone(ctx["current"])
                self.assertTrue(ctx["reasons"])

    def test_late_context_failure_after_real_compare_false(self):
        self.bind()
        current = api.capture_candidate(self.code, base=self.base, git_provider=self.provider, require_git_provider=True)
        original, observed = ctl.validate_event_semantics, []
        def expire(events, meta, **kwargs):
            self.provider.fault = True
            observed.append(api.compare_candidates(current, current, git_provider=self.provider,
                                                    require_git_provider=True)["equivalent"])
            return original(events, meta, **kwargs)
        with patch.object(ctl, "validate_event_semantics", side_effect=expire):
            _events, problems = invoke(ctl.verify_event_chain, self.ticket, self.meta(),
                                       provider=self.provider, required=True)
        self.assertEqual(observed, [False])
        self.assertTrue(any("candidate_provider_context" in issue for issue in problems), problems)

    def test_history_preserves_hash_schema_and_rejects_foreign_root(self):
        candidate = self.meta()["candidate_tracking"]["current"]["candidate_id"]
        self.command("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                     "--environment", "host", "--candidate-id", candidate, "--layer", "host", "--consumer", "test",
                     "--scenario", "unit", "--baseline", "fixture", "--evidence", "fixture.log", "--request-id", "history")
        self.bind()
        path, before = self.ticket / ctl.EVENTS_NAME, self.frozen()
        _events, problems = invoke(ctl.verify_event_chain, self.ticket, self.meta(), provider=self.provider, required=True)
        self.assertTrue(any("candidate_provider_context" in issue for issue in problems), problems)
        self.assertEqual(before, self.frozen())
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events[-1]["schema_version"] = 2
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8", newline="\n")
        _events, problems = invoke(ctl.verify_event_chain, self.ticket, self.meta(), provider=self.provider, required=True)
        self.assertTrue(any("event_schema" in issue for issue in problems), problems)
        self.assertTrue(any("event_hash" in issue for issue in problems), problems)
        self.assertTrue(any("candidate_provider_context" in issue for issue in problems), problems)
        self.assertNotIn(PENDING, problems)

    def test_command_dispatch_stays_unenabled(self):
        self.bind()
        before, output = self.frozen(), io.StringIO()
        with patch("sys.argv", ["icode_control.py", "build-start", "--dir", str(self.ticket),
                               "--build-dir", str(self.code / "build"), "--candidate-id", "0" * 64,
                               "--parameters-json", "{}", "--request-id", "unsupported"]), \
                contextlib.redirect_stdout(output):
            result = invoke(ctl.main, provider=self.provider, required=True)
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["gate_id"], PENDING)
        self.assertEqual(before, self.frozen())


if __name__ == "__main__":
    unittest.main()
