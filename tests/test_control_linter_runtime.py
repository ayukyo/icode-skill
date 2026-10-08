"""Fixed gate linters use the CP interpreter, without PATH or locale mutation.

Real subprocess cases exercise the bundled linters. Boundary mocks cover slow
or injected failures; str output is only compatibility with existing mocks,
not an additional subprocess output interface.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "control_linter_runtime", ROOT / "tools" / "icode_control.py"
)
assert SPEC and SPEC.loader
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)
GATES = ("thinking_gate", "mcp_coverage", "workflow_contract")


def completed(*, stdout=None, stderr=b"", code=0):
    if stdout is None:
        stdout = json.dumps({"message": "合法中文报告"}, ensure_ascii=False).encode("utf-8")
    return subprocess.CompletedProcess([], code, stdout, stderr)


class ControlLinterRuntimeTests(unittest.TestCase):
    def _real(self, *, empty_path=False, c_locale=False, legacy=True):
        # The child gets only runtime necessities, never model credentials.
        environment = {key: os.environ[key] for key in (
            "SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "LANG", "LC_ALL",
        ) if key in os.environ}
        if empty_path:
            environment["PATH"] = ""
        if c_locale:
            environment.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "中文 工单"
            directory.mkdir()
            (directory / ".ico_metadata.json").write_text(json.dumps({
                "ticket_id": "中文工单", "status": "init_in_progress", "completed_steps": [],
            }, ensure_ascii=False), encoding="utf-8")
            program = (
                "import json,runpy,sys; from pathlib import Path; "
                "cp=runpy.run_path(sys.argv[1]); "
                "rows=cp['run_gate_linters'](Path(sys.argv[2]),legacy=sys.argv[3]=='True'); "
                "print(json.dumps(rows,ensure_ascii=True))"
            )
            proc = subprocess.run(
                [sys.executable, "-B", "-X", "utf8=0" if c_locale else "utf8",
                 "-c", program, str(ROOT / "tools" / "icode_control.py"),
                 str(directory), str(legacy)],
                cwd=temporary, env=environment, capture_output=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", errors="replace"))
            return json.loads(proc.stdout)

    def test_real_empty_path_runs_all_three_bundled_linters(self):
        before = dict(os.environ)
        rows = self._real(empty_path=True)
        self.assertEqual(tuple(row["gate_id"] for row in rows), GATES)
        self.assertTrue(all(row["ok"] and isinstance(row.get("report"), dict) for row in rows), rows)
        self.assertEqual(dict(os.environ), before)

    def test_real_c_locale_and_chinese_path_keep_valid_utf8_reports(self):
        before = dict(os.environ)
        rows = self._real(c_locale=True)
        self.assertTrue(all(row["ok"] and isinstance(row.get("report"), dict) for row in rows), rows)
        self.assertIn("中文工单", json.dumps([row["report"] for row in rows], ensure_ascii=False))
        self.assertEqual(dict(os.environ), before)

    def test_real_nonzero_strict_reports_still_fail_closed(self):
        rows = self._real(empty_path=True, legacy=False)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(not row["ok"] and row.get("fail_closed") for row in rows), rows)
        self.assertTrue(all(row.get("returncode") != 0 and isinstance(row.get("report"), dict)
                            for row in rows), rows)

    def test_fixed_command_uses_current_interpreter_and_binary_capture(self):
        with patch.object(CONTROL.subprocess, "run", return_value=completed()) as run:
            rows = CONTROL.run_gate_linters(Path("ticket"), to_status="plan_done")
        self.assertTrue(all(row["ok"] for row in rows))
        for gate_id, call in zip(GATES, run.call_args_list):
            self.assertEqual(call.args[0], [sys.executable, "-X", "utf8",
                f"tools/lint_{gate_id}.py", "--strict", "--json", "--step", "plan", "ticket"])
            self.assertEqual(call.kwargs, {"capture_output": True, "text": False,
                "timeout": 180, "cwd": str(ROOT)})
        self.assertEqual(rows[0]["report"]["message"], "合法中文报告")

    def test_custom_commands_keep_argv_and_text_locale_semantics(self):
        for gate_id, command in (
            ("custom", ["python3", "tools/lint_thinking_gate.py", "--json"]),
            ("thinking_gate", ["python3", "tools/custom.py", "--json"]),
            ("thinking_gate", [sys.executable, "tools/lint_thinking_gate.py", "--json"]),
        ):
            with self.subTest(gate_id=gate_id, command=command):
                state = copy.deepcopy(CONTROL.load_state_machine())
                state["gate_policy"]["gate_linters"] = {gate_id: command}
                with patch.object(CONTROL, "load_state_machine", return_value=state), \
                     patch.object(CONTROL.subprocess, "run", return_value=completed(
                         stdout='{"custom":true}', stderr="")) as run:
                    rows = CONTROL.run_gate_linters(Path("ticket"))
                self.assertTrue(rows[0]["ok"])
                self.assertEqual(run.call_args.args[0], [*command, "ticket"])
                self.assertEqual(run.call_args.kwargs, {"capture_output": True, "text": True,
                    "timeout": 180, "cwd": str(ROOT)})

    def test_legacy_and_audit_verified_mapping_remain_unchanged(self):
        with patch.object(CONTROL.subprocess, "run", return_value=completed()) as run:
            rows = CONTROL.run_gate_linters(Path("ticket"), to_status="completed",
                delivery_verdict="verified", legacy=True)
        self.assertTrue(all(row["ok"] for row in rows))
        for gate_id, call in zip(GATES, run.call_args_list):
            command = call.args[0]
            self.assertNotIn("--strict", command)
            self.assertEqual(command[-3:], ["--step",
                "audit-verified" if gate_id == "workflow_contract" else "audit", "ticket"])

    def test_nonzero_valid_json_is_not_success(self):
        with patch.object(CONTROL.subprocess, "run", return_value=completed(code=1)):
            rows = CONTROL.run_gate_linters(Path("ticket"))
        self.assertTrue(all(not row["ok"] and row.get("fail_closed") and
                            row["returncode"] == 1 and isinstance(row.get("report"), dict)
                            for row in rows))

    def test_invalid_json_cannot_pass_even_with_zero_exit(self):
        for output in (b"not-json", b"", b"[]", b"null", b'{"value":NaN}'):
            with self.subTest(output=output):
                try:
                    with patch.object(CONTROL.subprocess, "run", return_value=completed(stdout=output)):
                        rows = CONTROL.run_gate_linters(Path("ticket"))
                except Exception as error:
                    self.fail(f"invalid JSON escaped report boundary: {type(error).__name__}")
                self.assertTrue(all(not row["ok"] and row.get("fail_closed") for row in rows))

    def test_existing_str_mock_keeps_json_fail_closed_compatibility(self):
        with patch.object(CONTROL.subprocess, "run", return_value=completed(
                stdout="not-json", stderr="")):
            rows = CONTROL.run_gate_linters(Path("ticket"))
        self.assertTrue(all(not row["ok"] and row.get("fail_closed") for row in rows))

    def test_invalid_utf8_fails_closed_and_collects_remaining_gates(self):
        for invalid in (completed(stdout=b"\xff"), completed(stderr=b"\xff")):
            with self.subTest(stream="stdout" if invalid.stdout == b"\xff" else "stderr"):
                try:
                    with patch.object(CONTROL.subprocess, "run", side_effect=[
                            invalid, completed(), completed()]) as run:
                        rows = CONTROL.run_gate_linters(Path("ticket"))
                except Exception as error:
                    self.fail(f"bad UTF8 escaped report boundary: {type(error).__name__}")
                self.assertEqual(run.call_count, 3)
                self.assertFalse(rows[0]["ok"])
                self.assertIs(rows[0].get("fail_closed"), True)
                self.assertTrue(all(row["ok"] for row in rows[1:]))

    def test_timeout_and_oserror_keep_existing_fail_closed_collection(self):
        for error in (subprocess.TimeoutExpired("fixed-linter", 180),
                      FileNotFoundError("missing executable"), PermissionError("denied")):
            with self.subTest(error=type(error).__name__):
                with patch.object(CONTROL.subprocess, "run", side_effect=error) as run:
                    rows = CONTROL.run_gate_linters(Path("ticket"))
                self.assertEqual(run.call_count, 3)
                self.assertTrue(all(not row["ok"] and row.get("fail_closed") for row in rows))

    def test_interruptions_and_custom_decode_errors_propagate_unchanged(self):
        for error in (KeyboardInterrupt("stop"), SystemExit(17)):
            with self.subTest(error=type(error).__name__):
                with patch.object(CONTROL.subprocess, "run", side_effect=error):
                    with self.assertRaises(type(error)) as raised:
                        CONTROL.run_gate_linters(Path("ticket"))
                self.assertIs(raised.exception, error)
        state = copy.deepcopy(CONTROL.load_state_machine())
        state["gate_policy"]["gate_linters"] = {"custom": ["custom-tool"]}
        error = UnicodeDecodeError("ascii", b"\xff", 0, 1, "injected locale failure")
        with patch.object(CONTROL, "load_state_machine", return_value=state), \
             patch.object(CONTROL.subprocess, "run", side_effect=error):
            with self.assertRaises(UnicodeDecodeError) as raised:
                CONTROL.run_gate_linters(Path("ticket"))
        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
