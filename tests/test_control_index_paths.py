"""工单索引相对路径在三个平台上使用同一 wire 格式。"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory


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


if __name__ == "__main__":
    unittest.main()
