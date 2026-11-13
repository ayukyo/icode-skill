"""Stage discovery keeps its own bounded raw I/O for v2 and mixed history."""
import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

import test_candidate_storage_control as fixtures


class StorageStageTests(fixtures.StorageControlTests):
    def test_json_syntax_diagnostics_stay_distinct_from_typed_storage_errors(self):
        metadata = self.api.load_metadata(self.directory)
        events = self.api.read_events(self.directory)[0]
        meta_path, events_path = self.directory / self.api.METADATA_NAME, self.directory / self.api.EVENTS_NAME
        self.api.atomic_write_json(meta_path, metadata)
        events_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        legacy_meta, legacy_events = meta_path.read_bytes(), events_path.read_bytes()
        for kind in ("metadata_json", "ledger_json", "metadata_envelope", "event_envelope"):
            meta_path.write_bytes(legacy_meta)
            events_path.write_bytes(legacy_events)
            damaged_path = meta_path if kind.startswith("metadata") else events_path
            if kind == "metadata_json": damaged_path.write_bytes(b"{broken json}\n")
            elif kind == "ledger_json": damaged_path.write_bytes(legacy_events + b"{broken json}\n")
            else:
                value = metadata if kind.startswith("metadata") else events[0]
                damaged_path.write_text(json.dumps({"storage_version": 99,
                    "kind": "metadata" if kind.startswith("metadata") else "event", "value": value}) + "\n", encoding="utf-8")
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
            with self.subTest(kind=kind), self.assertRaises(self.api.ControlError) as error:
                self.api.stage_module().discover_stage_history(self.directory.parent.parent)
            diagnostic = error.exception.extra["discovery"]
            self.assertEqual(diagnostic["reason"], "invalid_control_json" if kind.endswith("json") else "invalid_candidate_storage")
            self.assertEqual(diagnostic["path"], str(damaged_path))
            self.assertEqual(diagnostic["control_root"], str(self.directory))
            if kind.endswith("json"):
                self.assertEqual(diagnostic["line"], 2 if kind == "ledger_json" else 1)
            if kind.startswith("ledger"):
                self.assertEqual(diagnostic["ticket_id"], metadata["ticket_id"])
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})

    def test_v2_coordinator_parse_error_projects_the_actual_ledger_source(self):
        from test_stage_acceptance import StageAcceptanceTests
        case = StageAcceptanceTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.enable()
        expected_ticket = case.ctl.load_metadata(case.coord)["ticket_id"]
        for name in (case.ctl.EVENTS_NAME, case.ctl.METADATA_NAME):
            path = case.coord / name
            original = path.read_bytes()
            expected_line = len(original.splitlines()) + 1
            path.write_bytes(original + b"{broken json}\n")
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in case.project.rglob("*") if p.is_file()}
            with self.subTest(name=name):
                view = case.ctl.stage_projection(case.second)
                self.assertEqual(view["state"], "unknown")
                self.assertTrue(view["blocked"])
                self.assertEqual(view["available_actions"], [])
                self.assertEqual(view["discovery"]["reason"], "invalid_control_json")
                self.assertEqual(view["discovery"]["path"], str(path))
                self.assertEqual(view["discovery"]["control_root"], str(case.coord))
                self.assertEqual(view["discovery"]["line"], expected_line)
                if name == case.ctl.EVENTS_NAME:
                    self.assertEqual(view["discovery"]["ticket_id"], expected_ticket)
                self.assertIn(str(path), view["reasons"][0])
                self.assertEqual(view["recovery"]["action"], "repair_control_ledger")
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in case.project.rglob("*") if p.is_file()})
            path.write_bytes(original)

    def test_discovery_uses_only_its_counted_raw_reads(self):
        self.update()
        project = self.directory.parent.parent
        stage = self.api.stage_module()
        with mock.patch.object(self.api, "_committed_ledger", side_effect=AssertionError("uncounted ledger read")):
            self.assertIsNone(stage.discover_stage_history(project))
        with self.assertRaisesRegex(self.api.ControlError, "budget"):
            stage.discover_stage_history(project, limits={"max_files": 1})

    def test_tolerated_v2_ticket_cannot_hide_a_completely_missing_ledger(self):
        for has_refs in (False, True):
            if has_refs: self.update()
            ledger = self.directory / self.api.EVENTS_NAME
            saved = self.directory / "offline-ledger-fixture"
            ledger.rename(saved)
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
            with self.subTest(has_refs=has_refs), self.assertRaises(self.api.ControlError):
                self.api.stage_module().discover_stage_history(self.directory.parent.parent, tolerate=self.directory)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})
            saved.rename(ledger)

    def test_typed_legacy_valued_metadata_cannot_downgrade_missing_ledger(self):
        self.update()
        path = self.directory / self.api.METADATA_NAME
        physical = json.loads(path.read_text(encoding="utf-8"))
        physical["value"]["schema_version"] = 2
        path.write_text(json.dumps(physical), encoding="utf-8")
        (self.directory / self.api.EVENTS_NAME).rename(self.directory / "offline-ledger-fixture")
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
        for tolerated in (None, self.directory):
            with self.subTest(tolerated=tolerated is not None), self.assertRaises(self.api.ControlError):
                self.api.stage_module().discover_stage_history(self.directory.parent.parent, tolerate=tolerated)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})

    def test_mixed_history_and_storage_errors_cannot_be_tolerated(self):
        self.update()
        project = self.directory.parent.parent
        stage = self.api.stage_module()
        ledger = self.directory / self.api.EVENTS_NAME
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        rows[0] = rows[0]["value"]
        ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.assertIsNone(stage.discover_stage_history(project))
        for mode in ("missing", "unknown", "reserved", "corrupt", "pending"):
            metadata = (self.directory / self.api.METADATA_NAME).read_bytes()
            ledger_bytes = ledger.read_bytes()
            with self.subTest(mode=mode):
                if mode == "pending":
                    (self.directory / self.api.TXN_NAME).write_text("{}", encoding="utf-8")
                elif mode == "corrupt":
                    damaged = copy.deepcopy(rows)
                    damaged[1]["value"]["payload"]["snapshot"]["manifest"][0]["size"] = 77
                    ledger.write_text("\n".join(json.dumps(row) for row in damaged) + "\n", encoding="utf-8")
                else:
                    damaged = json.loads(metadata)
                    if mode == "unknown": damaged["storage_version"] = 99
                    elif mode == "reserved": damaged["value"]["extensions"]["misplaced"] = {"$icode_manifest_ref": {}}
                    else: damaged["value"]["extensions"]["storage_fixture"]["manifest"]["$icode_manifest_ref"]["sha256"] = "f" * 64
                    (self.directory / self.api.METADATA_NAME).write_text(json.dumps(damaged), encoding="utf-8")
                before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
                with self.assertRaises(self.api.ControlError): stage.discover_stage_history(project)
                if mode != "pending":
                    with self.assertRaises(self.api.ControlError): stage.discover_stage_history(project, tolerate=self.directory)
                self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})
            (self.directory / self.api.METADATA_NAME).write_bytes(metadata)
            ledger.write_bytes(ledger_bytes)
            (self.directory / self.api.TXN_NAME).unlink(missing_ok=True)

    def test_archive_is_self_contained_and_handoff_uses_physical_bytes(self):
        self.update()
        metadata = self.api.load_metadata(self.directory)
        archive = self.directory.parent.parent / "retained"
        archive.mkdir()
        after = copy.deepcopy(metadata)
        after["archive_path"] = str(archive)
        self.api.commit_metadata_and_event(self.directory, metadata, after, "metadata_updated", {})
        files = []
        for name in (self.api.METADATA_NAME, self.api.EVENTS_NAME):
            path = archive / name
            shutil.copyfile(self.directory / name, path)
            files.append({"path": name, "size": path.stat().st_size, "sha256": self.api.file_sha256(path)})
        manifest = {"schema_version": 1, "complete": True, "ticket_id": after["ticket_id"],
            "archive_dir": str(archive), "files": files, "external_refs": [],
            "lint_roundtrip": [{"gate_id": key, "source_ok": True, "archive_ok": True, "equivalent": True}
                for key in self.api.load_state_machine()["gate_policy"]["gate_linters"]]}
        self.api.atomic_write_json(archive / self.api.ARCHIVE_MANIFEST_NAME, manifest)
        original_ledger = (self.directory / self.api.EVENTS_NAME).read_bytes()
        self.assertEqual(self.api.handoff_close_control_root(self.directory, after), archive)
        self.assertEqual((archive / self.api.EVENTS_NAME).read_bytes(), original_ledger)
        self.assertEqual(json.loads((archive / self.api.METADATA_NAME).read_text(encoding="utf-8"))["storage_version"], 2)
        self.directory.rename(self.directory.with_name("source-unavailable"))
        self.assertEqual(self.api.read_metadata_readonly(archive), after)
        self.assertEqual(self.api.verify_event_chain(archive, after)[1], [])
        self.assertEqual(self.api.verify_archive_manifest(archive, after["ticket_id"])[1], [])


if __name__ == "__main__": unittest.main()
