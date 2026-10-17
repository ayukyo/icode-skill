"""Authorized append-only stage-plan revisions preserve acceptance and failures."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from test_stage_acceptance import StageAcceptanceTests as Fixture


class StageRevisionTests(unittest.TestCase):
    setUp = Fixture.setUp
    cli = Fixture.cli
    enable = Fixture.enable
    provider = Fixture.provider
    ready = Fixture.ready
    record = Fixture.record
    accept = Fixture.accept
    enable_next_candidate = Fixture.enable_next_candidate

    def proposal(self):
        plan = copy.deepcopy(self.plan)
        plan['stages'].insert(1, {'id': 'independent', 'title': 'Host-only preparation',
                                  'required_actions': ['verify']})
        return plan

    def revision(self, plan, access=None, request_id='revision-one'):
        access = access or {}
        preview = getattr(self.ctl, 'preview_stage_revision', None)
        revise = getattr(self.ctl, 'revise_stage_program', None)
        self.assertTrue(callable(preview) and callable(revise),
                        'authorized plan changes lack a non-destructive preview/apply writer')
        view = preview(self.coord, plan, **access)
        request = {**view['challenge'], 'request_id': request_id, 'reason': 'User approved independent preparation'}
        trusted = lambda request: {'source': 'host_user', 'source_id': 'test-user:revision',
                                   'intent': '修订阶段计划'}
        return plan, view, request, trusted

    def test_authorized_revision_preserves_accepted_stage_and_old_binding(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        before = self.ctl.load_metadata(self.first)
        plan, preview, request, trusted = self.revision(self.proposal(), access)
        result = self.ctl.revise_stage_program(self.coord, plan, request,
                                              trusted_confirmation=trusted, **access)
        self.assertTrue(result['ok'])
        self.assertEqual(self.ctl.load_metadata(self.first), before)
        self.assertEqual(self.ctl.stage_projection(self.first, **access)['state'], 'accepted')
        self.assertTrue(self.ctl.stage_projection(self.second, **access)['blocked'])
        self.assertEqual(preview['accepted_prefix'], ['one'])
        self.assertEqual(self.ctl.verify_event_chain(self.coord, self.ctl.load_metadata(self.coord), **access)[1], [])

    def test_no_user_callback_cannot_revise(self):
        self.enable()
        plan, _, request, _ = self.revision(self.proposal())
        before = self.ctl.load_metadata(self.coord)
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord, plan, request)
        self.assertEqual(self.ctl.load_metadata(self.coord), before)

    def test_unaccepted_stage_can_add_required_authorized_actions(self):
        self.enable()
        before = self.ctl.load_metadata(self.first)
        plan = copy.deepcopy(self.plan)
        plan['stages'][0]['required_actions'].append('code')
        plan, view, request, trusted = self.revision(plan)
        self.assertEqual(view['action_effects']['one']['added_actions'], ['code'])
        self.assertEqual(view['action_effects']['two']['added_actions'], [])
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.assertEqual(self.ctl.load_metadata(self.first), before)
        self.assertIn('code', self.ctl.stage_projection(self.first)['available_actions'])
        with self.assertRaisesRegex(self.ctl.ControlError, 'candidate tracking must be enabled'):
            self.ctl.require_stage_action(self.first, 'code')
        self.assertEqual(self.ctl.verify_event_chain(self.coord, self.ctl.load_metadata(self.coord))[1], [])

    def test_action_addition_cannot_change_old_obligations_or_order(self):
        self.enable()
        for kind in ['remove', 'reorder', 'title', 'duplicate', 'outside_authority']:
            plan = copy.deepcopy(self.plan)
            actions = plan['stages'][0]['required_actions']
            if kind == 'remove':
                actions[:] = ['verify', 'code']
            elif kind == 'reorder':
                actions[:] = ['serial_exec', 'verify', 'code']
            elif kind == 'title':
                actions.append('code')
                plan['stages'][0]['title'] = 'Changed obligation'
            elif kind == 'duplicate':
                actions.append('verify')
            else:
                actions.append('audit')
            with self.subTest(kind=kind), self.assertRaises(self.ctl.ControlError):
                self.ctl.preview_stage_revision(self.coord, plan)

    def test_action_addition_cannot_modify_accepted_stage(self):
        access, _, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        plan = copy.deepcopy(self.plan)
        plan['stages'][0]['required_actions'].append('code')
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.preview_stage_revision(self.coord, plan, **access)

    def test_adding_code_invalidates_review_and_requires_code_freshness(self):
        access, _, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.ctl.review_stage(self.first, request_id='before-code-review', **access)
        self.assertTrue(self.ctl.stage_projection(self.first, **access)['can_confirm'])
        old_meta = self.ctl.load_metadata(self.first)
        plan = copy.deepcopy(self.plan)
        plan['stages'][0]['required_actions'].append('code')
        plan, _, request, trusted = self.revision(plan, access)
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted, **access)
        self.assertEqual(self.ctl.load_metadata(self.first), old_meta)
        self.assertFalse(self.ctl.stage_projection(self.first, **access)['can_confirm'])
        review = self.ctl.review_stage(self.first, request_id='after-code-review', **access)
        self.assertNotEqual(review['outcome'], 'pass')
        snapshot = self.ctl.load_json(Path(review['report_path']).with_name('stage_review.json'), 'stage review')
        self.assertTrue({'missing_current_code', 'missing_current_deepcheck', 'missing_current_audit'}
                        .issubset(snapshot['reasons']), snapshot['reasons'])
        self.assertFalse(self.ctl.stage_projection(self.first, **access)['can_confirm'])

    def test_revision_replay_and_conflicting_source(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        again = self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.assertTrue(again['already_applied'])
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=lambda request: {
                'source': 'host_user', 'source_id': 'different-user', 'intent': '修订阶段计划'})

    def test_cannot_remove_or_change_old_stage_or_authorized_actions(self):
        self.enable()
        preview = getattr(self.ctl, 'preview_stage_revision', None)
        self.assertTrue(callable(preview), 'stage revision preview is required')
        for kind in ['delete', 'actions', 'authority']:
            plan = self.proposal()
            if kind == 'delete':
                plan['stages'].pop()
            elif kind == 'actions':
                plan['stages'][0]['required_actions'] = ['code']
            else:
                plan['authorized_actions'].append('deploy')
            with self.subTest(kind=kind), self.assertRaises(self.ctl.ControlError):
                preview(self.coord, plan)

    def test_stale_preview_and_open_execution_rejected(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        self.cli('step', '--dir', str(self.first), '--step', 'verify', '--phase', 'start', '--request', 'open')
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)

    def test_old_registration_and_new_binding_resolve_after_revision(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.ctl.register_stage_program(self.coord, plan, request_id='register-replay')
        self.ctl.bind_stage(self.coord, 'one', self.first, request_id='bind-replay')
        target = self.project / '.icode_output/.icode_output_4'
        self.cli('create', '--dir', str(target), '--ticket-id', 'new-independent',
                 '--requirement', 'Independent stage', '--birth', 'plan', '--stage-id', 'independent')
        view = self.ctl.stage_projection(target)
        self.assertTrue(view['blocked'])  # Stage one has not passed.
        self.assertEqual(view['stage_id'], 'independent')

    def test_unaccepted_pass_review_needs_fresh_review_after_revision(self):
        access, _, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.ctl.review_stage(self.first, request_id='pending-review', **access)
        self.assertTrue(self.ctl.stage_projection(self.first, **access)['can_confirm'])
        plan, _, request, trusted = self.revision(self.proposal(), access)
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted, **access)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertFalse(view['can_confirm'])
        self.assertEqual(view['report']['outcome'], 'pass')
        self.ctl.review_stage(self.first, request_id='fresh-review', **access)
        self.assertTrue(self.ctl.stage_projection(self.first, **access)['can_confirm'])

    def test_cannot_insert_before_accepted_prefix(self):
        access, _, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        plan = self.proposal()
        plan['stages'][0], plan['stages'][1] = plan['stages'][1], plan['stages'][0]
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.preview_stage_revision(self.coord, plan, **access)

    def test_append_failure_rolls_back_and_same_request_can_retry(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        before = self.ctl.load_metadata(self.coord)
        locator = self.project / '.icode_output/.stage_program/locator.json'
        locator_before = locator.read_bytes()
        with mock.patch.object(self.ctl, 'append_prepared_event', side_effect=OSError('injected append failure')):
            with self.assertRaises(OSError):
                self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.assertEqual(self.ctl.load_metadata(self.coord), before)
        self.assertEqual(locator.read_bytes(), locator_before)
        self.assertFalse((self.coord / self.ctl.TXN_NAME).exists())
        self.assertTrue(self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)['ok'])

    def test_concurrent_same_approval_is_applied_once(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        def apply():
            return self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply(), range(2)))
        self.assertEqual(sum(not row['already_applied'] for row in results), 1)
        program = self.ctl.load_metadata(self.coord)['extensions']['stage_program']
        self.assertEqual(len(program['plan_revisions']), 1)

    def test_child_metadata_drift_rejects_the_preview(self):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        self.cli('metadata-update', '--dir', str(self.first), '--set-json', json.dumps({'requirement_summary': 'changed'}))
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)

    def test_failure_report_survives_moving_unaccepted_stage(self):
        access, _, _, run, bundle = self.ready()
        args = self.ctl.build_parser().parse_args(['record-verification', '--dir', str(self.first),
            '--kind', 'device_test', '--outcome', 'fail', '--environment', 'host',
            '--candidate-id', run['candidate_id'], '--layer', 'host', '--consumer', 'tool',
            '--scenario', 'steady', '--evidence', 'raw.log', '--request-id', 'actual-failure'])
        self.ctl.cmd_record_verification(args, **access)
        self.ctl.review_stage(self.first, request_id='failure-review', **access)
        before = self.ctl.load_metadata(self.first)
        plan = self.proposal()
        plan['stages'][0], plan['stages'][1] = plan['stages'][1], plan['stages'][0]
        plan, _, request, trusted = self.revision(plan, access)
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted, **access)
        self.assertEqual(self.ctl.load_metadata(self.first), before)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertTrue(view['blocked'])
        self.assertFalse(view['can_confirm'])
        self.assertEqual(view['report']['outcome'], 'fail')

    def test_cli_preview_is_readonly_and_cli_cannot_attest_apply(self):
        self.enable()
        before = self.ctl.load_metadata(self.coord)
        view = self.cli('stage', '--dir', str(self.coord), '--phase', 'preview-revision',
                        '--plan-json', json.dumps(self.proposal()))
        self.assertEqual(view['inserted_stages'], ['independent'])
        self.assertEqual(self.ctl.load_metadata(self.coord), before)

    def test_multiple_revisions_keep_original_locator_and_binding(self):
        self.enable()
        locator = self.project / '.icode_output/.stage_program/locator.json'
        initial_locator = locator.read_bytes()
        initial_child = self.ctl.load_metadata(self.first)
        plan, _, request, trusted = self.revision(self.proposal())
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        later = copy.deepcopy(plan)
        later['stages'].append({'id': 'third', 'title': 'Later', 'required_actions': ['verify']})
        plan, _, request, trusted = self.revision(later, request_id='revision-two')
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.assertEqual(locator.read_bytes(), initial_locator)
        self.assertEqual(self.ctl.load_metadata(self.first), initial_child)
        self.assertFalse(self.ctl.stage_projection(self.first)['blocked'])

    def test_schema_violations_cannot_publish_a_revision(self):
        self.enable()
        for kind in ['missing_title', 'wrong_type', 'extra_field']:
            plan = self.proposal()
            if kind == 'missing_title':
                del plan['stages'][1]['title']
            elif kind == 'wrong_type':
                plan['stages'][1]['title'] = 23
            else:
                plan['stages'][1]['unexpected'] = True
            with self.subTest(kind=kind), self.assertRaises(self.ctl.ControlError):
                self.ctl.preview_stage_revision(self.coord, plan)

    def test_corrupt_unaccepted_report_blocks_preview_and_apply(self):
        access, _, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        review = self.ctl.review_stage(self.first, request_id='pending-review', **access)
        plan, _, request, trusted = self.revision(self.proposal(), access)
        from pathlib import Path
        Path(review['report_path']).write_text('damaged report', encoding='utf-8')
        for operation in [lambda: self.ctl.preview_stage_revision(self.coord, plan, **access),
                          lambda: self.ctl.revise_stage_program(self.coord, plan, request,
                                                               trusted_confirmation=trusted, **access)]:
            with self.assertRaises(self.ctl.ControlError):
                operation()

    def test_preview_contains_action_effects_and_inline_original_backup(self):
        self.enable()
        _, view, _, _ = self.revision(self.proposal())
        self.assertEqual(view['action_effects']['independent']['required_actions'], ['verify'])
        self.assertFalse(view['action_effects']['independent']['eligible_after_revision'])
        backup = view['backup_snapshot']
        self.assertEqual(backup['controls'][0]['metadata'], self.ctl.load_metadata(self.coord))
        self.assertEqual(backup['original_plan'], self.plan)

    def crash_and_resume(self, after_append):
        self.enable()
        plan, _, request, trusted = self.revision(self.proposal())
        from pathlib import Path
        entry = Path(self.ctl.__file__)
        script = '''import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location('crash_control', sys.argv[1])
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)
original = ctl.append_prepared_event
def crash(*args, **kwargs):
    if sys.argv[5] == 'after':
        original(*args, **kwargs)
    os._exit(71)
ctl.append_prepared_event = crash
ctl.revise_stage_program(sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4]),
    trusted_confirmation=lambda request: {'source': 'host_user', 'source_id': 'test-user:revision', 'intent': '修订阶段计划'})
'''
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
                                 str(entry), str(self.coord), json.dumps(plan), json.dumps(request),
                                 'after' if after_append else 'before'], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 71, result.stderr.decode('utf-8', 'replace'))
        self.assertTrue((self.coord / self.ctl.TXN_NAME).exists())
        self.assertTrue(self.ctl.stage_projection(self.coord)['blocked'])
        resumed = self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
        self.assertEqual(resumed['already_applied'], after_append)
        self.assertFalse((self.coord / self.ctl.TXN_NAME).exists())
        self.assertEqual(len(self.ctl.load_metadata(self.coord)['extensions']['stage_program']['plan_revisions']), 1)
        self.assertEqual(self.ctl.verify_event_chain(self.coord, self.ctl.load_metadata(self.coord))[1], [])

    def test_process_exit_before_event_recovers_and_retries(self):
        self.crash_and_resume(False)

    def test_process_exit_after_event_recovers_idempotently(self):
        self.crash_and_resume(True)

    def test_revision_cannot_overtake_a_child_final_append(self):
        self.enable()
        plan = self.proposal()
        plan['stages'][0], plan['stages'][1] = plan['stages'][1], plan['stages'][0]
        plan, _, request, trusted = self.revision(plan)
        reached, release, revised = threading.Event(), threading.Event(), threading.Event()
        original = self.ctl.append_prepared_event
        def barrier(root, event):
            if root == self.first and event['event_type'] == 'step_started':
                reached.set()
                if not release.wait(10):
                    raise RuntimeError('test barrier timed out')
            return original(root, event)
        def start_child():
            args = self.ctl.build_parser().parse_args(['step', '--dir', str(self.first), '--step', 'verify',
                '--phase', 'start', '--request', 'child-at-final-append'])
            return args.func(args)
        def revise():
            try:
                return self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted)
            finally:
                revised.set()
        with mock.patch.object(self.ctl, 'append_prepared_event', side_effect=barrier), ThreadPoolExecutor(max_workers=2) as pool:
            child = pool.submit(start_child)
            self.assertTrue(reached.wait(5))
            revision = pool.submit(revise)
            overtook = revised.wait(1)
            release.set()
            self.assertEqual(child.result(timeout=10), 0)
            with self.assertRaises(self.ctl.ControlError):
                revision.result(timeout=10)
        self.assertFalse(overtook, 'revision published while a child writer held its final append lock')

    def test_accepted_prefix_then_independent_acceptance_does_not_accept_old_failure(self):
        access, contract, _, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        self.cli('metadata-update', '--dir', str(self.second), '--set-json',
                 json.dumps({'verification_contract': contract}))
        args = self.ctl.build_parser().parse_args(['candidate', '--dir', str(self.second), '--phase', 'capture',
            '--base', '1' * 40, '--request-id', 'second-candidate'])
        self.ctl.cmd_candidate(args, **access)
        current = self.ctl.candidate_snapshot(self.second, self.ctl.load_metadata(self.second), **access)
        args = self.ctl.build_parser().parse_args(['record-verification', '--dir', str(self.second),
            '--kind', 'device_test', '--outcome', 'fail', '--environment', 'host',
            '--candidate-id', current['candidate_id'], '--layer', 'host', '--consumer', 'tool',
            '--scenario', 'steady', '--evidence', 'observed failure', '--request-id', 'second-failed'])
        self.ctl.cmd_record_verification(args, **access)
        failed = self.ctl.review_stage(self.second, request_id='second-failure', **access)
        self.assertEqual(failed['outcome'], 'fail')
        plan, _, request, trusted = self.revision(self.proposal(), access)
        self.ctl.revise_stage_program(self.coord, plan, request, trusted_confirmation=trusted, **access)
        target = self.project / '.icode_output/.icode_output_4'
        self.cli('create', '--dir', str(target), '--ticket-id', 'independent-proof', '--requirement',
                 'Independent preparation', '--birth', 'plan', '--stage-id', 'independent')
        self.assertFalse(self.ctl.stage_projection(target, **access)['blocked'])
        self.cli('metadata-update', '--dir', str(target), '--set-json', json.dumps({'verification_contract': contract}))
        args = self.ctl.build_parser().parse_args(['candidate', '--dir', str(target), '--phase', 'capture',
            '--base', '1' * 40, '--request-id', 'independent-candidate'])
        self.ctl.cmd_candidate(args, **access)
        candidate = self.ctl.candidate_snapshot(target, self.ctl.load_metadata(target), **access)
        run.update(candidate_id=candidate['candidate_id'])
        bundle.update(ticket_id='independent-proof', stage_id='independent', candidate_id=candidate['candidate_id'])
        (target / 'raw.log').write_bytes((self.first / 'raw.log').read_bytes())
        self.first = target
        self.record(access, run, bundle)
        review = self.ctl.review_stage(target, request_id='independent-review', **access)
        self.assertEqual(review['outcome'], 'pass')
        view = self.ctl.stage_projection(target, **access)
        confirmation = {**view['challenge'], 'decision': 'confirm', 'request_id': 'independent-confirm'}
        self.ctl.confirm_stage(target, confirmation, trusted_confirmation=lambda request: {
            'source': 'host_user', 'source_id': 'test-user:independent', 'intent': '确认本阶段验收'}, **access)
        view = self.ctl.stage_projection(self.second, **access)
        self.assertFalse(view['blocked'])
        self.assertFalse(view['can_confirm'])
        self.assertEqual(view['report']['outcome'], 'fail')
        self.assertNotEqual(view['state'], 'accepted')


del Fixture  # Do not collect the imported fixture's existing test methods twice.

if __name__ == '__main__':
    unittest.main()
