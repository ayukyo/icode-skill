"""Stable reads across Windows stat APIs, without devices or Git operations."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class StatView:
    def __init__(self, original, ctime):
        self.original, self.st_ctime_ns = original, ctime

    def __getattr__(self, name):
        return getattr(self.original, name)


class WindowsOS:
    """Model the observed cross-API ctime difference, keeping real file I/O."""
    name = "nt"

    def __init__(self, *, changing=False, before_open=None):
        self.calls, self.changing, self.before_open = 0, changing, before_open

    def __getattr__(self, name):
        return getattr(os, name)

    def fstat(self, descriptor):
        self.calls += 1
        return StatView(os.fstat(descriptor), 123000 + (self.calls if self.changing else 0))

    def open(self, path, flags):
        if self.before_open:
            self.before_open(Path(path))
        return os.open(path, flags)


class FileIdentityCompatibilityTests(unittest.TestCase):
    def fixture(self, flavor):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        name = {"evidence": "evidence_bundle", "stage": "stage_acceptance", "visual": "visual_evidence"}[flavor]
        spec = importlib.util.spec_from_file_location("identity_test_" + name, ROOT / "tools" / (name + ".py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        if flavor in {"evidence", "visual"}:
            target = project / "raw.log"
            target.write_bytes(b"original raw data\n")
            if flavor == "visual":
                def run():
                    self.assertEqual(api._read_path(target), target.read_bytes())
                return api, target, run, None
            reader = api.Reader(project)
            def run():
                self.assertEqual(reader.read(target.name), target.read_bytes())
                reader.finish()
            return api, target, run, reader
        target = project / ".icode_output/.icode_output_1" / api.ctl().METADATA_NAME
        target.parent.mkdir(parents=True)
        # A legacy ordinary ticket permits discovery without stage/ledger state.
        target.write_text(json.dumps({"ticket_id": "legacy"}), encoding="utf-8")
        def run():
            self.assertIsNone(api.discover_stage_history(project))
        return api, target, run, None

    def test_unchanged_files_pass_with_native_stat_apis(self):
        for flavor in ("evidence", "stage", "visual"):
            with self.subTest(flavor=flavor):
                _api, _target, run, _reader = self.fixture(flavor)
                run()

    def test_windows_path_and_handle_ctime_can_differ(self):
        for flavor in ("evidence", "stage", "visual"):
            with self.subTest(flavor=flavor):
                api, _target, run, _reader = self.fixture(flavor)
                with mock.patch.object(api, "os", WindowsOS()):
                    run()

    def test_handle_ctime_change_during_read_is_still_rejected(self):
        for flavor in ("evidence", "stage", "visual"):
            with self.subTest(flavor=flavor):
                api, _target, run, _reader = self.fixture(flavor)
                with mock.patch.object(api, "os", WindowsOS(changing=True)):
                    error = api.ctl().ControlError if flavor == "stage" else ValueError
                    with self.assertRaisesRegex(error, "changed during read|changed while reading"):
                        run()

    def test_real_identical_file_replacement_before_open_is_rejected(self):
        for flavor in ("evidence", "stage", "visual"):
            with self.subTest(flavor=flavor):
                api, target, run, _reader = self.fixture(flavor)
                original = target.stat()
                def replace(path):
                    self.assertEqual(path, target)
                    replacement = target.with_suffix(".replacement")
                    replacement.write_bytes(target.read_bytes())
                    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
                    os.replace(replacement, target)
                with mock.patch.object(api, "os", WindowsOS(before_open=replace)):
                    error = api.ctl().ControlError if flavor == "stage" else ValueError
                    with self.assertRaisesRegex(error, "replaced before"):
                        run()

    def test_path_ctime_change_during_read_is_still_rejected(self):
        for flavor in ("evidence", "stage", "visual"):
            with self.subTest(flavor=flavor):
                api, target, run, _reader = self.fixture(flavor)
                original_lstat, calls = Path.lstat, 0
                def lstat(path, *args, **kwargs):
                    nonlocal calls
                    info = original_lstat(path, *args, **kwargs)
                    if path == target:
                        calls += 1
                        if calls > 1:
                            return StatView(info, info.st_ctime_ns + 1)
                    return info
                with mock.patch.object(api, "os", WindowsOS()), mock.patch.object(Path, "lstat", lstat):
                    error = api.ctl().ControlError if flavor == "stage" else ValueError
                    with self.assertRaisesRegex(error, "replaced during read|changed while reading"):
                        run()

    def test_evidence_finish_detects_path_ctime_change(self):
        api, target, _run, reader = self.fixture("evidence")
        with mock.patch.object(api, "os", WindowsOS()):
            reader.read(target.name)
            original_lstat = Path.lstat
            def lstat(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                return StatView(info, info.st_ctime_ns + 1) if path == target else info
            with mock.patch.object(Path, "lstat", lstat):
                with self.assertRaisesRegex(ValueError, "replaced after read"):
                    reader.finish()


if __name__ == "__main__":
    unittest.main()
