"""Native prepare budgets use ordinary temporary tickets, without Git writes."""
import contextlib
import io
import json
import unittest

from test_stage_acceptance import StageAcceptanceTests as Fixture


class InspectionBudgetTests(unittest.TestCase):
    cli = Fixture.cli
    _base_setup = Fixture.setUp

    def setUp(self):
        self._base_setup()
        (self.project / 'source.py').write_text('value = 1\n', encoding='utf-8')
        self.cli('metadata-update', '--dir', str(self.first), '--set-json',
                 json.dumps(dict(code_files=['source.py'])))
        (self.first / '03_plan_final.md').write_text('# Disposable native prepare fixture\n', encoding='utf-8')
        self.cli('step', '--dir', str(self.first), '--step', 'code', '--phase', 'start', '--attempt', 'budget-attempt')
        self.cli('step', '--dir', str(self.first), '--step', 'code', '--phase', 'check',
                 '--attempt', 'budget-attempt', '--boundary', 'before_write')

    def prepare(self, *options, request='prepare-budget'):
        try:
            return self.cli('inspection', '--dir', str(self.first), '--step', 'code',
                            '--attempt', 'budget-attempt', '--phase', 'prepare', '--request', request, *options)
        except SystemExit as exc:
            if '--max-files' in options and exc.code == 2:
                self.fail('Explicit native prepare budget is unsupported')
            raise

    def report(self):
        return json.loads((self.first / 'code_worklist.json').read_text(encoding='utf-8'))

    def control_bytes(self):
        return tuple((self.first / name).read_bytes() for name in
                     (self.ctl.METADATA_NAME, self.ctl.EVENTS_NAME, 'code_worklist.json'))

    def read_source(self):
        (self.project / 'source.py').read_text(encoding='utf-8')
        self.cli('inspection', '--dir', str(self.first), '--step', 'code', '--attempt', 'budget-attempt',
                 '--phase', 'read', '--read-phase', 'code_review', '--path', 'source.py', '--request', 'actual-read')

    def test_default_preserves_legacy_request_and_replay_after_read(self):
        first = self.prepare()
        self.assertEqual(self.report()['max_files'], 200)
        event = next(e for e in self.ctl.read_events(self.first)[0] if e.get('request_id') == 'prepare-budget')
        self.assertEqual(event['payload']['inspection_request'], dict(phase='prepare', read_phase=None,
                         source_path=None, related=None, scopes=None, baselines=None))
        self.read_source()
        before = self.control_bytes()
        replay = self.prepare()
        self.assertTrue(replay['already_applied'])
        self.assertEqual(replay['event_id'], first['event_id'])
        self.assertEqual(self.control_bytes(), before)

    def test_explicit_budget_is_frozen_without_fabricating_read(self):
        self.prepare('--max-files', '512')
        report = self.report()
        self.assertEqual(report['max_files'], 512)
        self.assertTrue(all(not f['reads'] for unit in report['units'] for f in unit['files']))
        self.assertTrue(all(not check['results'] for check in report['checks']))
        event = next(e for e in self.ctl.read_events(self.first)[0] if e.get('request_id') == 'prepare-budget')
        self.assertEqual(event['payload']['inspection_request']['max_files'], 512)

    def test_explicit_replay_and_omitted_new_request_keep_read_and_budget(self):
        first = self.prepare('--max-files', '512')
        self.read_source()
        before = self.control_bytes()
        replay = self.prepare('--max-files', '512')
        self.assertTrue(replay['already_applied'])
        self.assertEqual(replay['event_id'], first['event_id'])
        self.assertEqual(self.control_bytes(), before)
        worklist = (self.first / 'code_worklist.json').read_bytes()
        self.prepare(request='omitted-resume')
        self.assertEqual(self.report()['max_files'], 512)
        self.assertEqual((self.first / 'code_worklist.json').read_bytes(), worklist)

    def test_same_attempt_budget_change_is_rejected_without_writes(self):
        self.prepare('--max-files', '512')
        before = self.control_bytes()
        for request in ('prepare-budget', 'different-request'):
            with self.subTest(request=request), self.assertRaises(self.ctl.ControlError):
                self.prepare('--max-files', '256', request=request)
            self.assertEqual(self.control_bytes(), before)

    def test_explicit_request_cannot_be_replayed_as_omitted(self):
        self.prepare('--max-files', '512')
        before = self.control_bytes()
        with self.assertRaises(self.ctl.ControlError):
            self.prepare()
        self.assertEqual(self.control_bytes(), before)

    def test_omitted_request_cannot_be_replayed_as_explicit_default(self):
        self.prepare()
        before = self.control_bytes()
        with self.assertRaises(self.ctl.ControlError):
            self.prepare('--max-files', '200')
        self.assertEqual(self.control_bytes(), before)

    def test_programmatic_invalid_budget_rejects_before_any_change(self):
        self.prepare()
        before = self.control_bytes()
        for value in (0, -1, True, 1.5, '512'):
            args = self.ctl.build_parser().parse_args(['inspection', '--dir', str(self.first), '--step', 'code',
                    '--attempt', 'budget-attempt', '--phase', 'prepare', '--request', 'invalid-budget'])
            args.max_files = value
            with self.subTest(value=value), self.assertRaises(self.ctl.ControlError), contextlib.redirect_stdout(io.StringIO()):
                args.func(args)
            self.assertEqual(self.control_bytes(), before)

    def test_nonprepare_explicit_budget_is_rejected_before_other_validation(self):
        self.prepare()
        before = self.control_bytes()
        for phase in ('read', 'assess', 'check'):
            args = self.ctl.build_parser().parse_args(['inspection', '--dir', str(self.first), '--step', 'code',
                    '--attempt', 'budget-attempt', '--phase', phase, '--read-phase', 'code_review', '--path', 'source.py'])
            args.max_files = 512
            with self.subTest(phase=phase), self.assertRaises(self.ctl.ControlError) as caught, contextlib.redirect_stdout(io.StringIO()):
                args.func(args)
            self.assertEqual(caught.exception.report()['gate_id'], 'inspection_budget')
            self.assertEqual(self.control_bytes(), before)


del Fixture

if __name__ == '__main__':
    unittest.main()
