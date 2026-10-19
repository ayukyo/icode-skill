"""Batch Read preserves native authority and receipts; no Git commits/devices."""
import contextlib
import hashlib
import io
import json
import unittest
from unittest import mock
from test_stage_acceptance import StageAcceptanceTests as Fixture

class InspectionBatchTests(unittest.TestCase):
    cli=Fixture.cli
    _base_setup=Fixture.setUp
    file_count=3

    def setUp(self):
        self._base_setup()
        self.paths=['file_%03d.py'%i for i in range(self.file_count)]
        for i,name in enumerate(self.paths):
            (self.project/name).write_text('value = %d\n'%i,encoding='utf-8')
        self.cli('metadata-update','--dir',str(self.first),'--set-json',json.dumps(dict(code_files=self.paths)))
        (self.first/'03_plan_final.md').write_text('# Disposable batch test\n',encoding='utf-8')
        self.cli('step','--dir',str(self.first),'--step','code','--phase','start','--attempt','batch-attempt')
        self.cli('step','--dir',str(self.first),'--step','code','--phase','check',
            '--attempt','batch-attempt','--boundary','before_write')
        self.cli('inspection','--dir',str(self.first),'--step','code','--attempt','batch-attempt',
            '--phase','prepare','--max-files','512','--request','prepare')

    def report(self):return json.loads((self.first/'code_worklist.json').read_text(encoding='utf-8'))

    def control_bytes(self):
        return tuple((self.first/name).read_bytes() for name in
            (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME,'code_worklist.json'))

    def batch(self,paths,request='batch-read',**extra):
        raw=paths if extra.pop('raw',False) else json.dumps(paths)
        try:
            return self.cli('inspection','--dir',str(self.first),'--step','code','--attempt','batch-attempt',
                '--phase',extra.pop('phase','read'),'--read-phase',extra.pop('read_phase','code_review'),
                '--paths-json',raw,'--request',request,*extra.pop('options',[]))
        except SystemExit as exc:
            self.fail('Native batch Read is unsupported: parser exit %s'%exc.code)

    def reads(self):return {f['path']:f['reads'] for unit in self.report()['units'] for f in unit['files']}

    def test_batch_records_every_selected_member_in_one_artifact(self):
        before=self.ctl.read_events(self.first)[0]
        # Actual caller reads precede recording; prepare alone does not read.
        for name in self.paths[:2]:(self.project/name).read_text(encoding='utf-8')
        result=self.batch(self.paths[:2])
        after=self.ctl.read_events(self.first)[0]
        self.assertEqual(len(after),len(before)+1)
        self.assertEqual(after[-1]['event_id'],result['event_id'])
        self.assertEqual(after[-1]['payload']['inspection_request']['source_paths'],self.paths[:2])
        self.assertEqual(self.reads()[self.paths[2]],{})
        for name in self.paths[:2]:
            self.assertEqual(self.reads()[name]['code_review'],hashlib.sha256((self.project/name).read_bytes()).hexdigest())

    def test_exact_set_replay_returns_original_receipt_and_preserves_later_single_read(self):
        first=self.batch(self.paths[:2])
        self.cli('inspection','--dir',str(self.first),'--step','code','--attempt','batch-attempt',
            '--phase','read','--read-phase','code_review','--path',self.paths[2],'--request','later-single')
        before=self.control_bytes()
        replay=self.batch(list(reversed(self.paths[:2])))
        self.assertTrue(replay['already_applied'])
        self.assertEqual(replay['event_id'],first['event_id'])
        self.assertEqual(self.control_bytes(),before)

    def test_same_request_different_members_rejects_without_partial_write(self):
        self.batch(self.paths[:2]);before=self.control_bytes()
        with self.assertRaises(self.ctl.ControlError):self.batch(self.paths[1:])
        self.assertEqual(self.control_bytes(),before)

    def test_invalid_members_and_parameters_never_partially_register(self):
        cases=[[],[self.paths[0],self.paths[0]],self.paths[:1]+['missing.py'],
               [self.paths[0],None],[self.paths[0],'../outside.py'],['/absolute.py'],
               ['C:/absolute.py'],['a\\b.py'],['./'+self.paths[0]],['a/../b.py'],['bad\0.py'],
               'wrong',{'path':self.paths[0]},[str(i)+'.py' for i in range(257)]]
        for i,value in enumerate(cases):
            before=self.control_bytes()
            with self.subTest(value=repr(value)[:80]),self.assertRaises(self.ctl.ControlError):
                self.batch(value,request='bad-%d'%i)
            self.assertEqual(self.control_bytes(),before)

    def test_only_read_and_one_unambiguous_path_option(self):
        for options in [dict(phase='prepare'),dict(phase='assess'),dict(phase='check'),
                        dict(read_phase='not_a_phase'),dict(options=['--path',self.paths[0]]),dict(options=['--path',''])]:
            before=self.control_bytes()
            with self.subTest(options=options),self.assertRaises(self.ctl.ControlError):self.batch(self.paths[:1],**options)
            self.assertEqual(self.control_bytes(),before)

    def test_byte_budget_and_invalid_json_reject_before_any_write(self):
        for raw in ['[', ' '*65537+json.dumps(self.paths[:1])]:
            before=self.control_bytes()
            with self.assertRaises(self.ctl.ControlError):self.batch(raw,raw=True)
            self.assertEqual(self.control_bytes(),before)

    def test_unselected_source_drift_still_rejects_whole_batch(self):
        (self.project/self.paths[2]).write_text('value = 999\n',encoding='utf-8')
        before=self.control_bytes()
        with self.assertRaises(self.ctl.ControlError):self.batch(self.paths[:1])
        self.assertEqual(self.control_bytes(),before)

    def test_late_publication_refusal_is_not_reported_as_rollback_or_success(self):
        before=self.control_bytes()
        with mock.patch.object(self.ctl,'_stage_publication_check',side_effect=self.ctl.ControlError('late refusal')):
            with self.assertRaises(self.ctl.ControlError):self.batch(self.paths[:1])
        after=self.control_bytes()
        self.assertEqual(after[:2],before[:2])
        self.assertNotEqual(after[2],before[2])
        events=self.ctl.read_events(self.first)[0]
        digest=hashlib.sha256(after[2]).hexdigest()
        self.assertFalse(any(e.get('event_type')=='artifact_written' and e.get('payload',{}).get('sha256')==digest for e in events))

    def test_legacy_namespace_without_new_field_retains_legacy_request_shape(self):
        args=self.ctl.build_parser().parse_args(['inspection','--dir',str(self.first),'--step','code',
            '--attempt','batch-attempt','--phase','read','--read-phase','code_review','--path',self.paths[0],'--request','legacy-read'])
        if hasattr(args,'paths_json'):del args.paths_json
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(args.func(args),0)
        event=self.ctl.read_events(self.first)[0][-1]
        self.assertNotIn('source_paths',event['payload']['inspection_request'])
        self.assertEqual(event['payload']['inspection_request']['source_path'],self.paths[0])

class BatchUpperBoundaryTests(unittest.TestCase):
    file_count=256
    cli=InspectionBatchTests.cli
    _base_setup=InspectionBatchTests._base_setup
    setUp=InspectionBatchTests.setUp
    report=InspectionBatchTests.report
    batch=InspectionBatchTests.batch
    reads=InspectionBatchTests.reads

    def test_256_members_are_supported_with_frozen_prepare_budget(self):
        self.batch(self.paths)
        self.assertEqual(len(self.reads()),256)
        self.assertTrue(all('code_review' in value for value in self.reads().values()))


class HostBatchProviderTests(unittest.TestCase):
    """Real native Host/provider path with in-memory Git facts, never commits."""
    cli=Fixture.cli
    _base_setup=Fixture.setUp
    _provider=Fixture.provider

    def setUp(self):
        self._base_setup()
        self.provider=self._provider()
        self.old_source=(self.project/'source.py').read_bytes()
        self.factory_fault=None
        self.clock=[100.0]
        self.cli('metadata-update','--dir',str(self.first),'--set-json',json.dumps(dict(code_files=['source.py'])))
        self.invoke('candidate','--phase','capture','--base','1'*40,'--request-id','host-base')
        (self.first/'03_plan_final.md').write_text('# Isolated Host batch plan\n',encoding='utf-8')
        self.invoke('step','--step','code','--phase','start','--attempt','host-batch')
        self.invoke('step','--step','code','--phase','check','--attempt','host-batch','--boundary','before_write')
        self.invoke('inspection','--step','code','--phase','prepare','--attempt','host-batch','--request','host-prepare')

    def factory(self,module,workspace):
        outer=self
        class Transport(module.InspectionGitProvider):
            def __init__(self):
                self.workspace=workspace.parent if outer.factory_fault=='root' else workspace
            def query(self,request,*,limit):
                operation=request.operation.value
                if operation=='resolve_commit':data=('1'*40+'\n').encode()
                elif operation=='list_files':data=b'source.py\0' if request.kind=='association' else b''
                elif operation in ('diff_names','trigger_diff'):data=b''
                elif operation=='historical_blob':data=outer.old_source
                else:raise AssertionError('Unexpected inspection operation '+operation)
                return module.GitQueryResult(data,0,False)
        if self.factory_fault=='late':self.clock[0]+=31
        return Transport()

    def invoke(self,command,*options,factory=True):
        args=self.ctl.build_parser().parse_args([command,'--dir',str(self.first),*options])
        access=dict(git_provider=self.provider,require_git_provider=True)
        if command in ('step','inspection'):
            access['inspection_provider_factory']=self.factory if factory else None
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(args.func(args,**access),0)
        return json.loads(stream.getvalue())

    def batch(self,request='host-read',paths=None,**access):
        return self.invoke('inspection','--step','code','--phase','read','--attempt','host-batch',
            '--read-phase','code_review','--paths-json',json.dumps(paths or ['source.py']),'--request',request,**access)

    def state(self):
        return tuple((self.first/name).read_bytes() for name in
            (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME,'code_worklist.json'))

    def test_explicit_provider_batch_and_exact_replay(self):
        first=self.batch();before=self.state();second=self.batch()
        self.assertTrue(second['already_applied'])
        self.assertEqual(first['event_id'],second['event_id'])
        self.assertEqual(self.state(),before)
        with self.assertRaises(self.ctl.ControlError):self.batch(paths=['unknown.py'])
        self.assertEqual(self.state(),before)

    def test_missing_factory_wrong_root_and_expired_provider_reject_before_write(self):
        before=self.state()
        with self.assertRaises(self.ctl.ControlError):self.batch(factory=False)
        self.assertEqual(self.state(),before)
        self.factory_fault='root'
        with self.assertRaises(self.ctl.ControlError):self.batch()
        self.assertEqual(self.state(),before)
        self.factory_fault=None
        error=self.ctl.candidate_module().CandidateError('expired provider')
        with mock.patch.object(self.provider,'assert_context',side_effect=error):
            with self.assertRaises(self.ctl.ControlError):self.batch()
        self.assertEqual(self.state(),before)

    def test_factory_deadline_and_source_drift_do_not_use_cached_authority(self):
        self.factory_fault='late';before=self.state()
        with mock.patch.object(self.ctl.time,'monotonic',side_effect=lambda:self.clock[0]):
            with self.assertRaises(self.ctl.ControlError):self.batch()
        self.assertEqual(self.state(),before)
        self.factory_fault=None
        (self.project/'source.py').write_bytes(b'value = 999\n')
        with self.assertRaises(self.ctl.ControlError):self.batch()
        self.assertEqual(self.state(),before)

    def test_late_provider_expiry_keeps_actual_file_without_success_receipt(self):
        before=self.state();expired=[False]
        original_write=self.ctl.atomic_write_json
        def write(path,value):
            original_write(path,value)
            if str(path).endswith('code_worklist.json'):expired[0]=True
        def guard():
            if expired[0]:raise self.ctl.candidate_module().CandidateError('late expiry')
        with mock.patch.object(self.ctl,'atomic_write_json',side_effect=write), \
             mock.patch.object(self.provider,'assert_context',side_effect=guard):
            with self.assertRaises(self.ctl.ControlError):self.batch()
        after=self.state()
        self.assertEqual(after[:2],before[:2])
        self.assertNotEqual(after[2],before[2])


class StageObservationPerformanceTests(unittest.TestCase):
    setUp=Fixture.setUp
    cli=Fixture.cli
    enable=Fixture.enable
    provider=Fixture.provider
    ready=Fixture.ready
    record=Fixture.record
    accept=Fixture.accept
    enable_next_candidate=Fixture.enable_next_candidate

    def accepted_predecessor(self):
        access,contract,item,run,bundle=self.ready()
        self.record(access,run,bundle)
        self.accept(access)
        self.enable_next_candidate(access)
        self.ctl.require_stage_action(self.second,'code',**access)
        return access

    def test_retirement_identity_guard_does_not_requalify_predecessor_evidence(self):
        access=self.accepted_predecessor()
        stage=self.ctl.stage_module()
        with mock.patch.object(stage,'_predecessors',side_effect=AssertionError('Retirement identity unnecessarily requalified all evidence')):
            token=self.ctl._stage_retirement_guard(self.second,**access)
        self.assertTrue(token['retirement_only'])
        # Full action permission remains a separate current qualification.
        (self.first/'raw.log').write_bytes(b'changed after identity check')
        with self.assertRaises(self.ctl.ControlError):self.ctl.require_stage_action(self.second,'code',**access)

    def test_predecessor_ledger_change_between_verified_and_history_never_qualifies(self):
        access=self.accepted_predecessor()
        stage=self.ctl.stage_module();historical=stage._historical;changed=[False]
        def append_unverified(directory,review,**kwargs):
            if not changed[0] and str(directory)==str(self.first):
                changed[0]=True
                with (self.first/self.ctl.EVENTS_NAME).open('ab') as stream:stream.write(b'{}\n')
            return historical(directory,review,**kwargs)
        with mock.patch.object(stage,'_historical',side_effect=append_unverified):
            with self.assertRaises(self.ctl.ControlError):self.ctl.require_stage_action(self.second,'code',**access)

    def test_control_change_during_raw_validation_is_rejected_at_end(self):
        access=self.accepted_predecessor()
        module=self.ctl.verification_evidence_module();original=module.validate_bundle
        changed=[False]
        def change_after_raw(*args,**kwargs):
            result=original(*args,**kwargs)
            if not changed[0]:
                changed[0]=True
                with (self.first/self.ctl.EVENTS_NAME).open('ab') as stream:stream.write(b'{}\n')
            return result
        with mock.patch.object(module,'validate_bundle',side_effect=change_after_raw):
            with self.assertRaises(self.ctl.ControlError):self.ctl.require_stage_action(self.second,'code',**access)
        self.assertTrue(changed[0])

    def test_provider_expiry_during_history_is_rejected_at_end(self):
        access=self.accepted_predecessor();expired=[False]
        module=self.ctl.verification_evidence_module();original=module.validate_bundle
        def expire(*args,**kwargs):
            result=original(*args,**kwargs);expired[0]=True;return result
        def guard():
            if expired[0]:raise self.ctl.candidate_module().CandidateError('expired during evidence qualification')
        with mock.patch.object(module,'validate_bundle',side_effect=expire), \
             mock.patch.object(access['git_provider'],'assert_context',side_effect=guard):
            with self.assertRaises(self.ctl.ControlError):self.ctl.require_stage_action(self.second,'code',**access)
        self.assertTrue(expired[0])

del Fixture

if __name__=='__main__':unittest.main()
