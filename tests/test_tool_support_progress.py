"""Maintain a public, evidence-bounded registry; this does not certify hosts."""
from datetime import date
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ToolSupportProgressTest(unittest.TestCase):
    def test_ci_runs_new_checks_when_their_inputs_change(self):
        workflow = (ROOT / '.github/workflows/public-site.yml').read_text(encoding='utf-8')
        trigger = re.search(r'(?ms)^  pull_request:\n(.*?)(?=^  \S|\Z)', workflow)
        regression = re.search(
            r'(?ms)^      - name: Offline regression tests\n(.*?)(?=^      - |\Z)',
            workflow,
        )
        self.assertIsNotNone(trigger)
        self.assertIsNotNone(regression)
        for path in ('tools/check_skill_installation.py', 'tools/check_skill_evaluation.py',
                     'evals/**', 'tests/test_skill_installation.py',
                     'tests/test_skill_evaluation.py', 'tests/test_tool_support_progress.py'):
            self.assertIn(f"      - '{path}'", trigger[1].splitlines())
        for test in ('test_skill_installation.py', 'test_tool_support_progress.py'):
            self.assertIn(
                f'          python3 -m unittest discover -s tests -p {test} -v',
                regression[1].splitlines(),
            )
        self.assertIn(
            '          python3 -m pytest -p no:anyio tests/test_skill_evaluation.py -q',
            regression[1].splitlines(),
        )
        self.assertNotIn('unittest discover -s tests -p test_skill_evaluation.py', regression[1])

    def test_registry_has_extendable_rows_and_evidence_boundaries(self):
        path = ROOT / 'docs/tool-support-progress.md'
        self.assertTrue(path.is_file(), 'missing public tool support registry')
        text = path.read_text(encoding='utf-8')
        rows = [line.split('|')[1:-1] for line in text.splitlines()
                if line.startswith('| `')]
        self.assertGreaterEqual(len(rows), 12)
        ids = []
        kinds = {'宿主', '发现/安装', '创建/评估', '市场/目录', '文档索引', '自有入口'}
        for row in rows:
            self.assertEqual(len(row), 8, row)
            key, kind, discovery, execution, source, update, checked, next_step = (
                part.strip() for part in row)
            ids.append(key.strip('`'))
            self.assertIn(kind, kinds)
            for field in (discovery, execution, source, update, next_step):
                self.assertTrue(field)
            date.fromisoformat(checked)
            self.assertTrue(any(token in execution for token in ('通过', '待验', '受阻', '不适用')))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({'find-skills', 'skill-creator', 'skillhub', 'agentskill-sh',
                         'claude-code', 'codex', 'codebuddy', 'workbuddy'} <= set(ids))
        self.assertIn('https://github.com/ayukyo/icode-skill', text)
        self.assertIn('## 新增工具模板', text)
        self.assertIn('不代表', text)
        for internal in ('@qq.com', 'docs/local/', '/home/orbbec/', 'discovery-status-'):
            self.assertNotIn(internal, text)


if __name__ == '__main__':
    unittest.main()
