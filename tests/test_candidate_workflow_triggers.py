"""Keep candidate changes covered by the existing inspection workflow."""
from pathlib import Path
import re
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/inspection-software.yml"
START = "on:\n  push:\n    branches: [main]\n    paths:\n"
END = "  workflow_dispatch:\n"


def push_paths(text):
    # Deliberately bounded to this workflow's current literal-list layout.
    if text.count(START) != 1 or text.count(END) != 1:
        raise AssertionError("inspection push paths boundaries changed")
    before, body = text.split(START)
    paths, after = body.split(END)
    values = []
    for line in paths.splitlines():
        match = re.fullmatch(r"      - (['\"])(.+)\1", line)
        if match is None:
            raise AssertionError("unexpected push paths item: " + repr(line))
        values.append(match.group(2))
    if not values or len(values) != len(set(values)):
        raise AssertionError("empty or duplicate push paths")
    return values


class CandidateWorkflowTriggers(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def assert_trigger(self, target):
        paths = push_paths(self.text)
        self.assertIn("tools/inspection_worklist.py", paths)
        self.assertIn(target, paths)
        # A real temporary copy must reject a removed item, even with its comment.
        item = "      - '" + target + "'\n"
        self.assertEqual(self.text.count(item), 1)
        changed = self.text.replace(item, "", 1) + "\n# " + target + "\n"
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "inspection-software.yml"
            copy.write_text(changed, encoding="utf-8")
            self.assertNotIn(target, push_paths(copy.read_text(encoding="utf-8")))

    def test_candidate_source_triggers_inspection(self):
        self.assert_trigger("tools/icode_candidate.py")
        self.assert_trigger("tests/test_candidate_workflow_triggers.py")

    def test_candidate_provider_test_triggers_inspection(self):
        self.assert_trigger("tests/test_candidate_git_provider_unit.py")

    def test_candidate_workflow_triggers_inspection(self):
        self.assert_trigger(".github/workflows/candidate-provider-software.yml")


if __name__ == "__main__":
    unittest.main()
