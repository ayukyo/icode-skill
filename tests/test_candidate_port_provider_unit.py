"""B3d read-only port primitives; anonymous software fixtures, no host authority."""
import contextlib
import copy
import hashlib
import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_candidate_history_unit as history

ctl, api = history.ctl, history.api


def invoke(function, *args, provider=None, required=False, **kwargs):
    # Reach old behavior for semantic RED, never missing-keyword TypeError.
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class PortProviderTests(unittest.TestCase):
    def setUp(self):
        self.f = history.HistoryTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        (self.f.ticket / "input.txt").write_bytes(b"ticket input\n")
        self.meta = self.f.meta()

    def ports(self):
        return [
            {"id": "meta", "kind": "metadata_pointer", "value": "/code_files"},
            {"id": "file", "kind": "ticket_file", "value": "input.txt"},
            {"id": "glob", "kind": "ticket_glob", "value": "*.txt"},
            {"id": "sources", "kind": "metadata_files", "value": "/code_files", "base": "workspace"},
            {"id": "head", "kind": "workspace_git_head", "value": "."},
            {"id": "artifact", "kind": "external_artifact", "value": "<workspace>/source.py"},
        ]

    def model(self, ports):
        model = copy.deepcopy(ctl.load_execution_model())
        model["step_contracts"]["patch"]["inputs"] = [
            dict(port, required=True, protected=True) for port in ports
            if port["kind"] in model["input_kinds"]
        ]
        model["step_contracts"]["patch"]["drift_routes"] = {"default": "patch"}
        return model

    def seal(self):
        provider = self.f.seal()
        guard = patch.object(ctl.subprocess, "run", side_effect=AssertionError("B3d raw port Git"))
        guard.start()
        self.addCleanup(guard.stop)
        self.assertFalse((self.f.code / ".git").exists())
        return provider

    def resolve(self, port, provider=None, required=False, meta=None):
        return invoke(ctl.resolve_port, self.f.ticket, self.meta if meta is None else meta,
                      port, provider=provider, required=required)

    def capture(self, ports, provider=None, required=False):
        return invoke(ctl.capture_step_inputs, self.f.ticket, self.meta, self.model(ports), "patch",
                      provider=provider, required=required)

    def rejected(self, operation):
        before = self.f.frozen()
        with self.assertRaises(ctl.ControlError) as caught:
            operation()
        self.assertEqual(caught.exception.report()["gate_id"], "candidate_provider_context")
        self.assertEqual(self.f.frozen(), before)
        return str(caught.exception)

    def test_none_all_kinds_and_exact_capture_shape(self):
        facts = [self.resolve(port) for port in self.ports()]
        self.assertTrue(all(fact["exists"] for fact in facts))
        self.assertEqual(facts[1]["sha256"], hashlib.sha256(b"ticket input\n").hexdigest())
        self.assertEqual(facts[3]["items"][0]["sha256"],
                         hashlib.sha256((self.f.code / "source.py").read_bytes()).hexdigest())
        self.assertEqual(facts[4]["roots"], [{"root": self.f.code.as_posix(), "head": self.f.base}])
        model = self.model(self.ports())
        result, missing = self.capture(self.ports())
        self.assertEqual(missing, [])
        inputs = [fact for fact in facts if fact["kind"] in model["input_kinds"]]
        protected = {fact["id"]: fact for fact in inputs}
        self.assertEqual(result, {"contract_digest": ctl.canonical_digest(model["step_contracts"]["patch"]),
                                 "inputs": inputs, "protected": protected,
                                 "input_digest": ctl.canonical_digest(protected)})
        self.assertEqual(self.capture([]), ({"contract_digest": ctl.canonical_digest(
            self.model([])["step_contracts"]["patch"]), "inputs": [], "protected": {},
            "input_digest": ctl.canonical_digest({})}, []))

    def test_none_multiroot_and_legacy_nested_target(self):
        other = self.f.root / "other"
        other.mkdir()
        original = self.f.code
        try:
            self.f.code = other
            self.f.git("init", "-q")
            self.f.git("-c", "user.name=B3d", "-c", "user.email=fixture@example.invalid",
                       "commit", "--allow-empty", "-qm", "other")
            other_head = self.f.git("rev-parse", "HEAD").decode().strip()
        finally:
            self.f.code = original
        nested = self.f.code / "nested"
        nested.mkdir()
        meta = dict(self.meta, sub_worktrees=[{"path": str(other)}],
                    submission_contracts=[{"repo_path": str(self.f.code)}])
        fact = self.resolve(dict(self.ports()[4], value="nested"), meta=meta)
        expected = sorted([{"root": original.as_posix(), "head": self.f.base},
                           {"root": other.as_posix(), "head": other_head}], key=lambda item: item["root"])
        self.assertEqual(fact["roots"], expected)
        self.assertEqual(fact["digest"], ctl.canonical_digest(expected))

    def test_provider_head_uses_only_complete_canonical_facts(self):
        provider = self.seal()
        before = self.f.frozen()
        actual = self.resolve(self.ports()[4], provider, True)
        expected = [{"root": self.f.code.as_posix(), "head": provider.facts["head"]}]
        self.assertEqual(actual, {"id": "head", "kind": "workspace_git_head", "exists": True,
                                  "roots": expected, "digest": ctl.canonical_digest(expected)})
        self.assertEqual(provider.calls, ["facts"])
        self.assertEqual(self.f.frozen(), before)

    def test_provider_non_git_kinds_keep_real_bytes(self):
        expected = [self.resolve(port) for port in self.ports() if port["kind"] != "workspace_git_head"]
        provider = self.seal()
        actual = [self.resolve(port, provider, True) for port in self.ports()
                  if port["kind"] != "workspace_git_head"]
        self.assertEqual(actual, expected)
        self.assertEqual(provider.calls, [])

    def test_capture_propagates_same_canonical_kwargs_and_final_shape(self):
        expected = self.capture(self.ports())
        provider = self.seal()
        for required in (True, False):
            with self.subTest(required=required), patch.object(ctl, "resolve_port", wraps=ctl.resolve_port) as resolve:
                actual = self.capture(self.ports(), provider, required)
                self.assertEqual(actual, expected)
                self.assertEqual(resolve.call_count, 5)
                for call in resolve.call_args_list:
                    self.assertIs(call.kwargs["git_provider"], provider)
                    self.assertIs(call.kwargs["require_git_provider"], required)

    def test_missing_foreign_wrong_root_and_nonbool_all_reject(self):
        provider = self.seal()
        foreign_api = history.load("b3d_foreign", "icode_control.py").candidate_module()
        class Foreign(foreign_api.CandidateGitProvider):
            workspace = self.f.code
            def assert_context(self): return None
            def query(self, request, *, limit): raise AssertionError("foreign query")
        for supplied, required in ((None, True), (object(), True), (Foreign(), True),
                                   (provider, 0), (provider, [])):
            with self.subTest(provider=type(supplied).__name__, required=required):
                self.rejected(lambda: self.resolve(self.ports()[1], supplied, required))
                self.rejected(lambda: self.capture([], supplied, required))
        provider.workspace = self.f.control
        self.rejected(lambda: self.resolve(self.ports()[1], provider, True))

    def test_expired_before_every_kind_cannot_return_fact(self):
        provider = self.seal()
        provider.expired = True
        for port in self.ports():
            with self.subTest(kind=port["kind"]):
                self.rejected(lambda: self.resolve(port, provider, True))

    def test_expiry_after_real_digest_and_file_read_is_rejected(self):
        provider = self.seal()
        original = ctl.canonical_digest
        def digest(value):
            result = original(value)
            provider.expired = True
            return result
        for port in (self.ports()[0], self.ports()[2], self.ports()[3], self.ports()[5]):
            provider.expired = False
            with self.subTest(kind=port["kind"]), patch.object(ctl, "canonical_digest", side_effect=digest):
                self.rejected(lambda: self.resolve(port, provider, True))
        provider.expired = False
        original_file = ctl.file_fact
        def file(*args, **kwargs):
            result = original_file(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(ctl, "file_fact", side_effect=file):
            self.rejected(lambda: self.resolve(self.ports()[1], provider, True))

    def test_query_return_expiry_is_not_success(self):
        provider = self.seal()
        original = provider.query
        def query(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(provider, "query", side_effect=query):
            self.rejected(lambda: self.resolve(self.ports()[4], provider, True))

    def test_facts_return_expiry_is_not_success(self):
        provider = self.seal()
        original = api._candidate_facts
        def facts(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(api, "_candidate_facts", side_effect=facts):
            self.rejected(lambda: self.resolve(self.ports()[4], provider, True))

    def test_bad_facts_shape_identity_and_oid_reject_without_partial_fact(self):
        provider = self.seal()
        base = copy.deepcopy(provider.facts)
        mutations = [
            lambda x: x.update(extra=1),
            lambda x: x.update(head="0" * 39),
            lambda x: x.update(head_tree="X" * 40),
            lambda x: x.update(base=True),
            lambda x: x["identity"].update(object_format="sha256"),
            lambda x: x["identity"].update(checkout_realpath=str(self.f.control)),
            lambda x: x["identity"].update(common_git_dir="relative"),
            lambda x: x["identity"].update(checkout_inode=x["identity"]["checkout_inode"] + 1),
            lambda x: x["identity"].update(checkout_device=True),
            lambda x: x["identity"].update(common_git_inode=-1),
        ]
        for mutate in mutations:
            provider.facts = copy.deepcopy(base)
            mutate(provider.facts)
            with self.subTest(mutation=mutations.index(mutate)):
                self.rejected(lambda: self.resolve(self.ports()[4], provider, True))

    def test_result_error_truncation_types_and_duplicate_json_reject(self):
        provider = self.seal()
        raw = json.dumps(provider.facts).encode()
        values = [
            api.CandidateReadResult(raw, 1, False),
            api.CandidateReadResult(raw, 0, True),
            api.CandidateReadResult(raw, 0, False, "unavailable"),
            api.CandidateReadResult(raw, False, False),
            api.CandidateReadResult(bytearray(raw), 0, False),
            api.CandidateReadResult(b'{"head":1,"head":2}', 0, False),
            api.CandidateReadResult(b"\xff", 0, False),
            object(),
        ]
        for result in values:
            with self.subTest(result_type=type(result).__name__), \
                    patch.object(provider, "query", return_value=result):
                self.rejected(lambda: self.resolve(self.ports()[4], provider, True))

    def test_facts_budget_closed_request_and_no_arbitrary_query(self):
        provider = self.seal()
        original = provider.query
        requests = []
        def query(request, *, limit):
            requests.append((request, limit))
            original(request, limit=limit)
            return api.CandidateReadResult(b" " * (limit + 1), 0, False)
        with patch.object(provider, "query", side_effect=query):
            self.rejected(lambda: self.resolve(self.ports()[4], provider, True))
        self.assertEqual(len(requests), 1)
        request, limit = requests[0]
        self.assertIs(type(request), api.CandidateReadQuery)
        self.assertIs(request.operation, api.CandidateRead.FACTS)
        self.assertEqual((request.oid, request.old_oid, request.kind), ("", "", ""))
        self.assertEqual(limit, min(8192, api.DEFAULT_LIMITS["max_git_bytes"]))

    def test_facts_query_deadline_is_checked_after_return(self):
        provider = self.seal()
        clock = [0.0]
        original = provider.query
        def query(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] = api.DEFAULT_LIMITS["timeout_seconds"] + 1
            return result
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(provider, "query", side_effect=query):
            self.assertIn("deadline", self.rejected(
                lambda: self.resolve(self.ports()[4], provider, True)))

    def test_provider_target_rejects_nested_external_and_missing(self):
        nested = self.f.code / "nested"
        nested.mkdir()
        provider = self.seal()
        for target in ("nested", str(self.f.control), "missing"):
            with self.subTest(target=target):
                self.assertIn("unsupported", self.rejected(
                    lambda: self.resolve(dict(self.ports()[4], value=target), provider, True)))

        # Resolve only this target fails; binding/frozen/context paths remain real.
        unresolvable = self.f.root / "unresolvable"
        original_resolve = ctl.Path.resolve
        def resolve(path, *args, **kwargs):
            if path == unresolvable:
                raise RuntimeError("fixture symlink loop")
            return original_resolve(path, *args, **kwargs)
        cases = [
            (dict(self.ports()[4], value=str(unresolvable)), self.meta),
            (self.ports()[4], dict(self.meta, sub_worktrees=[{"path": str(unresolvable)}])),
        ]
        for port, meta in cases:
            with self.subTest(parse_site="target" if meta is self.meta else "metadata"), \
                    patch.object(ctl.Path, "resolve", autospec=True, side_effect=resolve):
                try:
                    self.rejected(lambda: self.resolve(port, provider, True, meta))
                except RuntimeError as exc:
                    self.fail("port parsing leaked RuntimeError instead of ControlError: " + str(exc))
        self.assertEqual(provider.calls, [])

    def test_same_root_declarations_are_deduplicated_without_data_loss(self):
        provider = self.seal()
        meta = dict(self.meta, sub_worktrees=[{"path": str(self.f.code)}],
                    submission_contracts=[{"repo_path": str(self.f.code)}])
        result = self.resolve(self.ports()[4], provider, True, meta)
        self.assertEqual(result["roots"], [{"root": self.f.code.as_posix(), "head": provider.facts["head"]}])

    def test_multiroot_and_malformed_declarations_never_partial_success(self):
        provider = self.seal()
        for field, key in (("sub_worktrees", "path"), ("submission_contracts", "repo_path")):
            for value in (None, {}, "bad", 0, [None], [{}], [{key: 7}], [{key: "relative"}],
                          [{key: str(self.f.code)}, {key: str(self.f.control)}],
                          [{key: str(self.f.code / "nested")}],
                          [{"worktree_path": str(self.f.control), key: str(self.f.code)}]):
                with self.subTest(field=field, value=value):
                    self.assertIn("unsupported", self.rejected(
                        lambda: self.resolve(self.ports()[4], provider, True, dict(self.meta, **{field: value}))))
        self.assertEqual(provider.calls, [])

    def test_capture_empty_inputs_and_after_last_digest_still_guard(self):
        provider = self.seal()
        original = ctl.canonical_digest
        def digest(value):
            result = original(value)
            if value == {}:
                provider.expired = True
            return result
        with patch.object(ctl, "canonical_digest", side_effect=digest):
            self.rejected(lambda: self.capture([], provider, True))
        provider.expired = True
        self.rejected(lambda: self.capture([], provider, True))

    def test_capture_one_deadline_covers_all_ports_and_final_digests(self):
        provider = self.seal()
        clock = [0.0]
        original = ctl.canonical_digest
        def digest(value):
            result = original(value)
            clock[0] += api.DEFAULT_LIMITS["timeout_seconds"] / 2
            return result
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(ctl, "canonical_digest", side_effect=digest):
            self.assertIn("deadline", self.rejected(
                lambda: self.capture([self.ports()[0], dict(self.ports()[0], id="second")], provider, True)))

    def test_deadline_covers_last_context_guard_itself(self):
        provider = self.seal()
        original = ctl._candidate_context_guard
        for operation in ("resolve", "capture"):
            clock, calls = [0.0], []
            def guard(*args, **kwargs):
                result = original(*args, **kwargs)
                calls.append(1)
                if len(calls) == 2:
                    clock[0] = api.DEFAULT_LIMITS["timeout_seconds"] + 1
                return result
            with self.subTest(operation=operation), \
                    patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(ctl, "_candidate_context_guard", side_effect=guard):
                self.assertIn("deadline", self.rejected(
                    lambda: self.resolve(self.ports()[1], provider, True) if operation == "resolve"
                    else self.capture([], provider, True)))
                self.assertEqual(len(calls), 2)

    def test_step_and_output_entrypoints_remain_unconnected(self):
        provider = self.seal()
        output = io.StringIO()
        with patch.object(ctl, "build_parser") as parser, contextlib.redirect_stdout(output):
            parser.return_value.parse_args.return_value = SimpleNamespace(cmd="step", func=ctl.cmd_step)
            result = ctl.main(git_provider=provider, require_git_provider=True)
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["gate_id"], "candidate_business_propagation_pending")
        self.assertNotIn("git_provider", inspect.signature(ctl.validate_step_outputs).parameters)


if __name__ == "__main__":
    unittest.main()
