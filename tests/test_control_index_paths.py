"""工单索引相对路径在三个平台上使用同一 wire 格式。"""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "control_index_paths", ROOT / "tools" / "icode_control.py"
)
assert SPEC and SPEC.loader
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class ControlIndexPathTests(unittest.TestCase):
    def test_windows相对路径序列化为schema要求的正斜杠(self):
        relative = PureWindowsPath(".icode_output", ".icode_output_1")
        self.assertEqual(
            CONTROL.serialize_index_out_dir(relative),
            ".icode_output/.icode_output_1",
        )

    def test_index_entry路径通过schema(self):
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            out_dir = workspace / ".icode_output" / ".icode_output_1"
            out_dir.mkdir(parents=True)
            entry = CONTROL.build_index_entry(
                out_dir,
                {"ticket_id": "T-1", "status": "plan_done"},
            )
            self.assertEqual(entry["out_dir"], ".icode_output/.icode_output_1")
            self.assertEqual(
                CONTROL.validate_index(
                    {"version": 1, "updated_at": CONTROL.now_iso(), "tickets": [entry]}
                ),
                [],
            )

    def test_原子替换遇到短暂共享冲突后成功(self):
        for writer, payload in (
            (CONTROL.atomic_write_json, {"value": "new"}),
            (CONTROL.atomic_write_bytes, b"new"),
        ):
            with self.subTest(writer=writer.__name__), TemporaryDirectory() as temporary:
                target = Path(temporary) / "target.json"
                target.write_bytes(b"old")
                original_replace = os.replace
                attempts = 0

                def transient_replace(source, destination):
                    nonlocal attempts
                    attempts += 1
                    if attempts < 3:
                        raise PermissionError("simulated sharing violation")
                    original_replace(source, destination)

                with patch.object(CONTROL.os, "replace", side_effect=transient_replace):
                    writer(target, payload)
                self.assertEqual(attempts, 3)
                if isinstance(payload, bytes):
                    self.assertEqual(target.read_bytes(), payload)
                else:
                    self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)
                self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_持续共享冲突保持旧文件并清理临时文件(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target.json"
            target.write_bytes(b"old")
            with (
                patch.object(
                    CONTROL.os,
                    "replace",
                    side_effect=PermissionError("simulated permanent denial"),
                ) as replace,
                self.assertRaises(PermissionError),
            ):
                CONTROL.atomic_write_json(target, {"value": "new"})
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
