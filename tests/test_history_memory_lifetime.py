"""History lifetime regression; generic fixture, no Git writes."""
import contextlib, io, json, sys, weakref, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_stage_acceptance import StageAcceptanceTests as Fixture

class HistoryLifetimeTests(unittest.TestCase):
    setUp = Fixture.setUp
    cli = Fixture.cli
    enable = Fixture.enable
    provider = Fixture.provider
    ready = Fixture.ready
    class History(list): pass
    class State(dict): pass

    @contextlib.contextmanager
    def track_history(self):
        refs = []
        original = self.ctl.verify_event_chain
        def verified(*args, **kw):
            rows, issues = original(*args, **kw)
            rows = self.History(rows)
            refs.append(weakref.ref(rows))
            return rows, issues
        with mock.patch.object(self.ctl, "verify_event_chain", side_effect=verified):
            yield refs

    def spawn(self):
        return self.cli("record-agent-spawn", "--dir", str(self.first), "--task-scope", "read generic bytes",
            "--expected-artifact", "report.json", "--evidence-boundary", "fixture only", "--join-condition", "actual report",
            "--backend", "fixture", "--model", "fixture", "--capability", "text", "--request-id", "spawn-one")

    def test_spawn_releases_history_before_commit(self):
        original = self.ctl.commit_metadata_and_event
        with self.track_history() as refs:
            def commit(*a, **kw):
                self.assertFalse(any(ref() is not None for ref in refs), "outer history retained across commit")
                return original(*a, **kw)
            with mock.patch.object(self.ctl, "commit_metadata_and_event", side_effect=commit): self.spawn()

    def test_result_releases_history_before_commit(self):
        spawned = self.spawn()
        original = self.ctl.commit_metadata_and_event
        with self.track_history() as refs:
            def commit(*a, **kw):
                self.assertFalse(any(ref() is not None for ref in refs), "outer history retained across commit")
                return original(*a, **kw)
            with mock.patch.object(self.ctl, "commit_metadata_and_event", side_effect=commit):
                self.cli("record-agent-result", "--dir", str(self.first), "--spawn-id", spawned["spawn"]["spawn_id"],
                    "--result", "joined", "--adopted", "yes", "--adoption-reason", "read checked", "--evidence-ref", "report.json",
                    "--summary", "bounded result", "--request-id", "result-one")

    def test_step_check_releases_history_before_append(self):
        started = self.cli("step", "--dir", str(self.first), "--step", "plan", "--phase", "start", "--request", "plan-start")
        original = self.ctl.append_event
        with self.track_history() as refs:
            def append(*a, **kw):
                self.assertFalse(any(ref() is not None for ref in refs), "outer history retained across append")
                return original(*a, **kw)
            with mock.patch.object(self.ctl, "append_event", side_effect=append):
                self.cli("step", "--dir", str(self.first), "--step", "plan", "--phase", "check", "--attempt", started["attempt"],
                         "--boundary", "before_write", "--request", "check-one")

    def test_bound_commit_releases_resolved_state_before_prepare(self):
        self.enable()
        api = self.ctl.stage_module()
        original_resolve, original_prepare = api._resolve, self.ctl.prepare_event
        refs = []
        def resolve(*a, **kw):
            state = original_resolve(*a, **kw)
            if state is not None:
                state = self.State(state); refs.append(weakref.ref(state))
            return state
        def prepare(*a, **kw):
            self.assertFalse(any(ref() is not None for ref in refs), "resolved state retained across prepare")
            return original_prepare(*a, **kw)
        meta = self.ctl.load_metadata(self.first)
        with mock.patch.object(api, "_resolve", side_effect=resolve), mock.patch.object(self.ctl, "prepare_event", side_effect=prepare):
            with self.ctl.DirLock(self.first):
                self.ctl.commit_metadata_and_event(self.first, meta, dict(meta), "external_note", {"note":"generic"}, request_id="commit-one")


    def test_provider_history_released_before_stage_guard(self):
        access, _, _, _, _ = self.ready()
        meta = self.ctl.load_metadata(self.first)
        original = self.ctl._stage_write_guard
        with self.track_history() as refs:
            def guard(*a, **kw):
                self.assertFalse(any(ref() is not None for ref in refs), "provider history retained")
                return original(*a, **kw)
            with mock.patch.object(self.ctl, "_stage_write_guard", side_effect=guard):
                with self.ctl.DirLock(self.first):
                    self.ctl.commit_metadata_and_event(self.first, meta, dict(meta), "external_note", {"note":"generic"},
                        request_id="provider-commit", before_publish=lambda: self.ctl._candidate_context_guard(self.first, meta, access["git_provider"], access["require_git_provider"]), **access)

    def test_draft_boolean_releases_history_before_action_guard(self):
        refs = []
        def read(*a, **kw):
            rows = self.History([{"event_type":"step_started", "payload":{"step":"code"}}])
            refs.append(weakref.ref(rows)); return rows, None
        def action(*a, **kw):
            self.assertFalse(any(ref() is not None for ref in refs), "draft history retained")
            return {"enabled":False}
        with mock.patch.object(self.ctl, "_stage_retirement_guard", return_value=None), mock.patch.object(self.ctl, "read_events", side_effect=read), mock.patch.object(self.ctl, "require_stage_action", side_effect=action):
            self.ctl._stage_write_guard(self.first, "metadata_updated", {"set":{"requirement":"generic"},"append":{}})

    def result_args(self, spawn_id, summary="bounded result"):
        return ("record-agent-result", "--dir", str(self.first), "--spawn-id", spawn_id,
            "--result", "joined", "--adopted", "yes", "--adoption-reason", "read checked", "--evidence-ref", "report.json",
            "--summary", summary, "--request-id", "result-one")

    def bytes_pair(self):
        return tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))

    def test_real_result_replay_conflict_and_full_chain(self):
        sid=self.spawn()["spawn"]["spawn_id"]
        self.cli(*self.result_args(sid)); before=self.bytes_pair()
        self.assertTrue(self.cli(*self.result_args(sid))["already_applied"])
        self.assertEqual(before,self.bytes_pair())
        with self.assertRaises(self.ctl.ControlError): self.cli(*self.result_args(sid,"changed"))
        self.assertEqual(before,self.bytes_pair())
        rows,issues=self.ctl.verify_event_chain(self.first,self.ctl.load_metadata(self.first))
        self.assertFalse(issues);self.assertEqual(rows[-1]["event_type"],"agent_result")

    def test_real_corrupt_history_refuses_without_mutation(self):
        sid=self.spawn()["spawn"]["spawn_id"]; golden=self.bytes_pair()
        for kind in ("hash","broken_chain","duplicate_id","schema","semantic"):
            with self.subTest(kind=kind):
                meta=json.loads(golden[0]); rows=[json.loads(x) for x in golden[1].decode().splitlines()]
                if kind=="hash": rows[-1]["payload"]["unexpected"]="tampered"
                elif kind=="semantic": meta["requirement"]="outside writer"
                else:
                    if kind=="broken_chain": rows[-1]["previous_event_hash"]="a"*64
                    elif kind=="duplicate_id": rows[-1]["event_id"]=rows[0]["event_id"]
                    elif kind=="schema": rows[-1]["actor"]="invalid"
                    rows[-1]["event_hash"]=self.ctl.canonical_event_hash(rows[-1])
                (self.first/self.ctl.METADATA_NAME).write_text(json.dumps(meta),encoding="utf-8")
                (self.first/self.ctl.EVENTS_NAME).write_text("".join(json.dumps(x)+"\n" for x in rows),encoding="utf-8")
                damaged=self.bytes_pair()
                with self.assertRaises(self.ctl.ControlError): self.cli(*self.result_args(sid))
                self.assertEqual(damaged,self.bytes_pair())
                for name,raw in zip((self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME),golden):(self.first/name).write_bytes(raw)

    def test_real_append_failure_rolls_back_then_retry_and_postappend_recovers(self):
        sid=self.spawn()["spawn"]["spawn_id"]; before=self.bytes_pair()
        with mock.patch.object(self.ctl,"append_prepared_event",side_effect=OSError("injected before append")):
            with self.assertRaises(OSError): self.cli(*self.result_args(sid))
        self.assertEqual(before,self.bytes_pair()); self.assertFalse((self.first/self.ctl.TXN_NAME).exists())
        original=self.ctl.append_prepared_event
        def after_append(*a,**kw): original(*a,**kw);raise OSError("injected after append")
        with mock.patch.object(self.ctl,"append_prepared_event",side_effect=after_append):self.cli(*self.result_args(sid))
        applied=self.bytes_pair();self.assertTrue(self.cli(*self.result_args(sid))["already_applied"])
        self.assertEqual(applied,self.bytes_pair());self.assertFalse((self.first/self.ctl.TXN_NAME).exists())

del Fixture  # Avoid collecting imported fixture tests as this module tests.
if __name__ == "__main__": unittest.main()
