"""Controlled tail consolidation: ordinary temporary tickets, no Git writes."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import threading
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from test_stage_acceptance import StageAcceptanceTests as Fixture


class StageConsolidationTests(unittest.TestCase):
    setUp = Fixture.setUp
    cli = Fixture.cli
    enable = Fixture.enable
    provider = Fixture.provider
    ready = Fixture.ready
    record = Fixture.record
    accept = Fixture.accept
    enable_next_candidate = Fixture.enable_next_candidate
    visual_bundle = Fixture.visual_bundle
    actual_visual_review = Fixture.actual_visual_review

    def proposal(self, target='merged'):
        proposed = copy.deepcopy(self.plan)
        proposed['stages'] = [dict(id=target, title='Combined pending work',
                                   required_actions=['code', 'verify', 'serial_exec', 'build'])]
        return proposed

    def descriptor(self, target='merged'):
        path = self.project / 'obligations.md'
        path.write_text('one: Preserve first verification and serial evidence.\n'
                        'two: Preserve code review, verification and build.\n', encoding='utf-8')
        return dict(schema_version=1, old_plan_digest=self.ctl.canonical_digest(self.plan),
            sources=[dict(id='requirements', path=str(path),
                sha256='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest(), item_ids=['one', 'two'])],
            mappings=[dict(source_stage_id=sid, target_stage_ids=[target],
                obligations=[dict(source_id='requirements', item_ids=[sid])], contract_target=target)
                for sid in ('one', 'two')])

    def preview(self, proposed, descriptor, **access):
        # Convert unsupported API into an explicit target-behavior assertion.
        # This positive RED is accompanied by existing-API mutation guard REDs,
        # so a missing keyword alone is never the feature's causal proof.
        try:
            return self.ctl.preview_stage_revision(self.coord, proposed, consolidation=descriptor, **access)
        except TypeError as exc:
            if 'consolidation' not in str(exc):
                raise
            self.fail('Legal explicit tail consolidation is unsupported: '+str(exc))

    def apply(self, proposed, descriptor, request_id='merge-one', **access):
        view=self.preview(proposed,descriptor,**access)
        request=dict(view['challenge'],request_id=request_id,reason='User approved tail consolidation')
        trusted=lambda request: dict(source='host_user',source_id='test-user:consolidation',intent='修订阶段计划')
        result=self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,
            trusted_confirmation=trusted,**access)
        return view,result,request,trusted

    def test_valid_tail_consolidation_preserves_source_controls(self):
        self.enable()
        before={p:tuple((p/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))
                for p in (self.first,self.second)}
        view,result,_request,_trusted=self.apply(self.proposal(),self.descriptor())
        self.assertTrue(result['ok'])
        self.assertEqual(view['accepted_prefix'],[])
        program=self.ctl.load_metadata(self.coord)['extensions']['stage_program']
        self.assertEqual([r['id'] for r in program['plan']['stages']],['merged'])
        for p,raw in before.items():
            self.assertEqual(tuple((p/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME)),raw)
            state=self.ctl.stage_projection(p)
            self.assertEqual(state['state'],'superseded')
            self.assertEqual(state['replacement_stages'],['merged'])
            self.assertEqual(state['available_actions'],[])
        self.assertEqual(self.ctl.verify_event_chain(self.coord,self.ctl.load_metadata(self.coord))[1],[])

    def test_existing_shared_guard_rejects_neutral_mutations_before_exemptions(self):
        self.enable()
        retired=dict(enabled=True,blocked=False,state='superseded',stage_id='one',generation=3,
            program_id='program',coordinator_root=str(self.coord),available_actions=[],replacement_stages=['merged'])
        mutations=[('operation_started',dict(**{'class':'read_only'})),
                   ('verification_recorded',dict(outcome='fail')),
                   ('verification_recorded',dict(outcome='inconclusive')),
                   ('metadata_updated',dict(set=dict(requirement_summary='draft'),append={})),
                   ('claim_recorded',dict(kind='fact')),
                   ('snapshot_written',dict(path='ticket_snapshot.json'))]
        # The authenticated retirement identity is the unit-test boundary.
        # Ordinary binding/events below it remain actual native fixtures.
        with mock.patch.object(self.ctl.stage_module(),'_retirement_projection',return_value=retired):
            for kind,payload in mutations:
                with self.subTest(event=kind,payload=payload),self.assertRaises(self.ctl.ControlError):
                    self.ctl._stage_write_guard(self.first,kind,payload)

    def test_retired_source_real_writes_leave_controls_unchanged(self):
        self.enable()
        self.apply(self.proposal(),self.descriptor())
        before=tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))
        commands=[('metadata-update','--set-json',json.dumps(dict(requirement_summary='old draft'))),
            ('operation','--phase','start','--name','old-read-ledger','--opclass','read_only','--request','old-read'),
            ('record-verification','--kind','device_test','--outcome','fail','--evidence','old failure'),
            ('snapshot',)]
        for command in commands:
            with self.subTest(command=command[0]),self.assertRaises(self.ctl.ControlError):
                self.cli(command[0],'--dir',str(self.first),*command[1:])
        self.assertEqual(tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME)),before)
        self.assertFalse((self.first/self.ctl.SNAPSHOT_NAME).exists())

    def test_default_without_descriptor_still_rejects_deletion(self):
        self.enable()
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.preview_stage_revision(self.coord,self.proposal())

    def retire_with_damaged_report(self):
        access,_contract,_item,_run,_bundle=self.ready()
        review=self.ctl.review_stage(self.first,request_id='original-inconclusive',**access)
        self.apply(self.proposal(),self.descriptor(),**access)
        self.assertEqual(self.ctl.stage_projection(self.first,**access)['state'],'superseded')
        report=Path(review['report_path'])
        report.write_bytes(report.read_bytes()+b'\nTemporary fixture damage\n')
        view=self.ctl.stage_projection(self.first,**access)
        self.assertTrue(view['blocked'])
        self.assertEqual(view['replacement_stages'],['merged'])
        return view

    def test_retired_bad_report_refuses_before_snapshot_first_write(self):
        self.retire_with_damaged_report()
        before=tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))
        with mock.patch.object(self.ctl,'atomic_write_json',wraps=self.ctl.atomic_write_json) as writer:
            with self.assertRaises(self.ctl.ControlError):
                self.cli('snapshot','--dir',str(self.first))
            writer.assert_not_called()
        self.assertFalse((self.first/self.ctl.SNAPSHOT_NAME).exists())
        self.assertEqual(tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME)),before)

    def test_retired_bad_report_refuses_retirement_only_publication(self):
        view=self.retire_with_damaged_report()
        token=self.ctl._stage_token(self.first,view,None)
        token['retirement_only']=True
        with self.assertRaises(self.ctl.ControlError):
            self.ctl._stage_publication_check(token)

    def test_nonretired_blocked_future_draft_remains_writable(self):
        self.enable()
        view=self.ctl.stage_projection(self.second)
        self.assertTrue(view['blocked'])
        self.assertNotIn('replacement_stages',view)
        self.cli('metadata-update','--dir',str(self.second),'--set-json',
            json.dumps(dict(requirement_summary='Future stage draft')))
        self.assertEqual(self.ctl.load_metadata(self.second)['requirement_summary'],'Future stage draft')

    def test_descriptor_source_drift_rejects_apply(self):
        self.enable()
        descriptor=self.descriptor()
        view=self.preview(self.proposal(),descriptor)
        (self.project/'obligations.md').write_text('Changed after approval',encoding='utf-8')
        request=dict(view['challenge'],request_id='drift',reason='Approve unchanged original')
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord,self.proposal(),request,consolidation=descriptor,
                trusted_confirmation=lambda request:dict(source='host_user',source_id='user',intent='修订阶段计划'))

    def test_replay_returns_once_and_source_conflict_rejected(self):
        self.enable()
        proposed,descriptor=self.proposal(),self.descriptor()
        _view,_result,request,trusted=self.apply(proposed,descriptor)
        replay=self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,trusted_confirmation=trusted)
        self.assertTrue(replay['already_applied'])
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,
                trusted_confirmation=lambda request:dict(source='host_user',source_id='other-user',intent='修订阶段计划'))

    def create_target(self, contract, stage_id='merged', number=4):
        path=self.project/('.icode_output/.icode_output_'+str(number))
        self.cli('create','--dir',str(path),'--ticket-id','target-'+stage_id,'--requirement','Preserved obligations',
            '--birth','plan','--stage-id',stage_id,'--metadata-json',json.dumps(dict(verification_contract=contract)))
        return path

    def test_inherited_contract_cannot_be_weakened_at_birth_or_update(self):
        access,contract,_item,_run,_bundle=self.ready()
        contract.update(profile='camera',baseline_ref='sha256:'+'a'*64)
        contract['evidence_contract']['required_items'][0]['min_count']=2
        self.cli('metadata-update','--dir',str(self.first),'--set-json',json.dumps(dict(verification_contract=contract)))
        self.apply(self.proposal(),self.descriptor(),**access)
        weak=copy.deepcopy(contract)
        weak['evidence_contract']['required_items'][0]['min_count']=1
        with self.assertRaises(self.ctl.ControlError):
            self.create_target(weak,number=4)
        failed=self.project/'.icode_output/.icode_output_4'
        self.assertFalse((failed/self.ctl.METADATA_NAME).exists())
        self.assertFalse((failed/self.ctl.EVENTS_NAME).exists())
        target=self.create_target(contract,number=5)
        for kind in ('profile','baseline_ref','count','method','required'):
            changed=copy.deepcopy(contract)
            if kind in ('profile','baseline_ref'):
                changed.pop(kind)
            elif kind=='count':
                changed['evidence_contract']['required_items']=[]
            elif kind=='method':
                changed['evidence_contract']['required_items'][0]['method']='human_observation'
            else:
                changed['required']=False
            before=(target/self.ctl.METADATA_NAME).read_bytes()
            with self.subTest(kind=kind),self.assertRaises(self.ctl.ControlError):
                self.cli('metadata-update','--dir',str(target),'--set-json',json.dumps(dict(verification_contract=changed)))
            self.assertEqual((target/self.ctl.METADATA_NAME).read_bytes(),before)

    def test_unbound_intermediate_and_ordinary_revision_keep_ancestor_contract(self):
        access,contract,_item,_run,_bundle=self.ready()
        self.apply(self.proposal(),self.descriptor(),**access)
        ordinary=self.proposal()
        ordinary['stages'].append(dict(id='extra',title='Independent later obligation',required_actions=['verify']))
        view=self.ctl.preview_stage_revision(self.coord,ordinary,**access)
        trusted=lambda request:dict(source='host_user',source_id='ordinary-user',intent='修订阶段计划')
        request=dict(view['challenge'],request_id='ordinary-between',reason='Approved additional pending work')
        self.ctl.revise_stage_program(self.coord,ordinary,request,trusted_confirmation=trusted,**access)
        proposed=self.proposal('final')
        path=self.project/'later-obligations.md'
        path.write_text('merged: original obligations\nextra: later verification\n',encoding='utf-8')
        descriptor=dict(schema_version=1,old_plan_digest=self.ctl.canonical_digest(ordinary),
            sources=[dict(id='later',path=str(path),sha256='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest(),item_ids=['merged','extra'])],
            mappings=[dict(source_stage_id=sid,target_stage_ids=['final'],contract_target='final',
                obligations=[dict(source_id='later',item_ids=[sid])]) for sid in ('merged','extra')])
        self.apply(proposed,descriptor,request_id='merge-second',**access)
        self.assertEqual(self.ctl.stage_projection(self.first,**access)['replacement_stages'],['final'])
        weak=copy.deepcopy(contract)
        weak['evidence_contract']['required_items']=[]
        with self.assertRaises(self.ctl.ControlError):
            self.create_target(weak,stage_id='final',number=4)
        self.create_target(contract,stage_id='final',number=5)
        self.assertEqual(self.ctl.verify_event_chain(self.coord,self.ctl.load_metadata(self.coord),**access)[1],[])

    def test_mapping_and_authority_omissions_rejected_without_writes(self):
        self.enable()
        for kind in ('missing_source','duplicate_source','unknown_target','lost_action','authority','old_id','missing_obligation','contract_target'):
            proposed,descriptor=self.proposal(),self.descriptor()
            if kind=='missing_source': descriptor['mappings'].pop()
            elif kind=='duplicate_source': descriptor['mappings'][1]['source_stage_id']='one'
            elif kind=='unknown_target': descriptor['mappings'][1]['target_stage_ids']=['other']
            elif kind=='lost_action': proposed['stages'][0]['required_actions'].remove('serial_exec')
            elif kind=='authority': proposed['authorized_actions'].append('deploy')
            elif kind=='old_id': proposed['stages'][0]['id']='one'
            elif kind=='missing_obligation': descriptor['sources'][0]['item_ids'].append('lost')
            else: descriptor['mappings'][0]['contract_target']='other'
            before=(self.coord/self.ctl.EVENTS_NAME).read_bytes()
            with self.subTest(kind=kind),self.assertRaises(self.ctl.ControlError):
                self.preview(proposed,descriptor)
            self.assertEqual((self.coord/self.ctl.EVENTS_NAME).read_bytes(),before)

    def test_retired_id_cannot_be_reintroduced_by_ordinary_revision(self):
        self.enable()
        self.apply(self.proposal(),self.descriptor())
        proposed=self.proposal()
        proposed['stages'].append(copy.deepcopy(self.plan['stages'][0]))
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.preview_stage_revision(self.coord,proposed)

    def test_retired_index_and_snapshot_refuse_before_atomic_business_write(self):
        self.enable()
        self.apply(self.proposal(),self.descriptor())
        for argv in [('index-write','--ticket-dir',str(self.first),'--index',str(self.project/'index.json')),
                     ('snapshot','--dir',str(self.first))]:
            with self.subTest(command=argv[0]),mock.patch.object(self.ctl,'atomic_write_json',wraps=self.ctl.atomic_write_json) as writer:
                with self.assertRaises(self.ctl.ControlError):
                    self.cli(*argv)
                writer.assert_not_called()

    def test_provider_is_inflight_and_receipt_precedes_finish(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        proposed,descriptor=self.proposal(),self.descriptor()
        def reviewer(request):
            trace=self.ctl.execution_trace(self.ctl.read_events(self.first)[0])
            self.assertEqual(len(trace['open_operations']),1)
            with self.assertRaises(self.ctl.ControlError):
                self.preview(proposed,descriptor,**access)
            return {key:request[key] for key in ('method','item_sha256','run_id','candidate_id')} | dict(
                outcome='pass',conclusion='Actual fixture provider called',limitations=['Offline fixture'],source_id='fixture-call',provider='fixture',model='fixture')
        result=self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',
            trusted_reviewer=reviewer,request_id='inflight',**access)
        events=self.ctl.read_events(self.first)[0]
        publication=next(i for i,e in enumerate(events) if e.get('request_id')=='inflight')
        finish=next(i for i,e in enumerate(events) if e.get('request_id')=='review-finish:inflight')
        self.assertLess(publication,finish)
        self.assertFalse(self.ctl.execution_trace(events)['open_operations'])
        self.assertEqual(result['outcome'],'pass')

    def test_same_request_concurrency_calls_provider_once(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        reached,release=threading.Event(),threading.Event()
        calls=[]
        def reviewer(request):
            calls.append(request)
            reached.set()
            if not release.wait(10): raise RuntimeError('test barrier timeout')
            return {key:request[key] for key in ('method','item_sha256','run_id','candidate_id')} | dict(
                outcome='pass',conclusion='One actual fixture call',limitations=['Offline fixture'],source_id='one-call',provider='fixture',model='fixture')
        def execute():
            return self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',
                trusted_reviewer=reviewer,request_id='one-owner',**access)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(execute)
            self.assertTrue(reached.wait(5))
            second=pool.submit(execute)
            try:
                with self.assertRaises(self.ctl.ControlError): second.result(timeout=5)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)['outcome'],'pass')
        self.assertEqual(len(calls),1)
        self.assertTrue(execute()['already_applied'])
        self.assertEqual(len(calls),1)

    def test_fixed_preupgrade_lineage_and_exact_replay_selection(self):
        # Portable old record proves lineage/replay selection, not live path
        # provenance. Native ordinary-revision tests cover real temporary IO.
        path=Path(__file__).parent/'fixtures/stage_revision_pre_consolidation.json'
        legacy=json.loads(path.read_text(encoding='utf-8'))
        program=legacy['program']
        stage=self.ctl.stage_module()
        self.assertEqual(len(stage._revision_plans(program)),2)
        state=dict(program=program,stage_id=None,coordinator=self.coord)
        trusted=lambda request:legacy['trusted_source']
        with mock.patch.object(stage,'_resolve',return_value=state):
            for kwargs in ({},{'consolidation':None}):
                result=self.ctl.revise_stage_program(self.coord,legacy['proposed'],legacy['request'],
                    trusted_confirmation=trusted,**kwargs)
                self.assertTrue(result['already_applied'])
            with self.assertRaises(self.ctl.ControlError):
                self.ctl.revise_stage_program(self.coord,legacy['proposed'],legacy['request'],
                    trusted_confirmation=lambda request:dict(legacy['trusted_source'],source_id='other'))

    def test_accepted_prefix_bytes_and_decision_preserved(self):
        access,_contract,_item,run,bundle=self.ready()
        self.record(access,run,bundle)
        self.accept(access)
        before=tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))
        proposed=self.proposal()
        proposed['stages'].insert(0,copy.deepcopy(self.plan['stages'][0]))
        descriptor=self.descriptor()
        descriptor['mappings']=descriptor['mappings'][1:]
        descriptor['sources'][0]['item_ids']=['two']
        view,_result,_request,_trusted=self.apply(proposed,descriptor,**access)
        self.assertEqual(view['accepted_prefix'],['one'])
        self.assertEqual(tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME)),before)
        self.assertEqual(self.ctl.stage_projection(self.first,**access)['state'],'accepted')
        bad=copy.deepcopy(proposed)
        bad['stages'][0]['title']='Changed accepted work'
        with self.assertRaises(self.ctl.ControlError):
            self.preview(bad,descriptor,**access)

    def test_failed_provider_terminates_and_same_request_cannot_reexecute(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        calls=[]
        def fail(request):
            calls.append(request)
            raise RuntimeError('Deliberate fixture failure')
        for _ in range(2):
            with self.assertRaises(self.ctl.ControlError):
                self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',
                    trusted_reviewer=fail,request_id='failed-once',**access)
        self.assertEqual(len(calls),1)
        events=self.ctl.read_events(self.first)[0]
        self.assertFalse(self.ctl.execution_trace(events)['open_operations'])
        self.assertEqual([e['payload']['outcome'] for e in events if e['event_type']=='operation_finished'],['failure'])
        self.assertIsNone(self.ctl.stage_module().protected_values(self.ctl.load_metadata(self.first))['evidence_reviews'])

    def test_receipt_publication_failure_has_real_failed_terminal(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        calls=[]
        def provider(request):
            calls.append(request)
            return {key:request[key] for key in ('method','item_sha256','run_id','candidate_id')} | dict(
                outcome='pass',conclusion='Fixture called',limitations=[],source_id='call',provider='fixture',model='fixture')
        with mock.patch.object(self.ctl,'_publish_review',side_effect=self.ctl.ControlError('injected publication failure')):
            with self.assertRaises(self.ctl.ControlError):
                self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',
                    trusted_reviewer=provider,request_id='publish-failure',**access)
        self.assertEqual(len(calls),1)
        events=self.ctl.read_events(self.first)[0]
        self.assertFalse(self.ctl.execution_trace(events)['open_operations'])
        self.assertEqual([e['payload']['outcome'] for e in events if e['event_type']=='operation_finished'],['failure'])


    def test_multitarget_mapping_preserves_each_contract_destination(self):
        access,contract,_item,_run,_bundle=self.ready()
        proposed=self.proposal('first-target')
        proposed['stages'].append(dict(id='second-target',title='Later preserved work',required_actions=['code','verify','build']))
        descriptor=self.descriptor('first-target')
        descriptor['mappings'][1]['target_stage_ids']=['first-target','second-target']
        descriptor['mappings'][1]['contract_target']='second-target'
        self.apply(proposed,descriptor,**access)
        self.assertEqual(self.ctl.stage_projection(self.second,**access)['replacement_stages'],['first-target','second-target'])
        program=self.ctl.load_metadata(self.coord)['extensions']['stage_program']
        inherited=self.ctl.stage_module().consolidation_module().inherited(program)
        self.assertEqual(inherited['first-target']['contracts'][0]['contract'],contract)
        self.assertEqual(inherited['second-target']['contracts'],[])
        self.assertEqual(len(inherited['first-target']['documents']),2)
        self.assertEqual(len(inherited['second-target']['documents']),1)

    def test_incompatible_profiles_and_baselines_are_not_silently_chosen(self):
        helper=self.ctl.stage_module().consolidation_module()
        for field in ('profile','baseline_ref'):
            with self.subTest(field=field),self.assertRaises(self.ctl.ControlError):
                helper._compatible(self.ctl,[dict(contract={field:'one'}),dict(contract={field:'two'})])

    def test_failed_report_and_controls_survive_without_accepting_target(self):
        access,contract,_item,_run,_bundle=self.ready()
        review=self.ctl.review_stage(self.first,request_id='old-inconclusive',**access)
        self.assertNotEqual(review['outcome'],'pass')
        report=Path(review['report_path']); original=report.read_bytes()
        before=tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME))
        self.apply(self.proposal(),self.descriptor(),**access)
        self.assertEqual(report.read_bytes(),original)
        self.assertEqual(tuple((self.first/name).read_bytes() for name in (self.ctl.METADATA_NAME,self.ctl.EVENTS_NAME)),before)
        target=self.create_target(contract)
        view=self.ctl.stage_projection(target)
        self.assertFalse(view['can_confirm'])
        self.assertNotEqual(view['state'],'accepted')
        self.assertEqual(self.ctl.stage_projection(self.first,**access)['report']['text'],original.decode('utf-8'))

    def test_source_relative_link_and_budget_are_rejected(self):
        self.enable()
        proposed,descriptor=self.proposal(),self.descriptor()
        helper=self.ctl.stage_module().consolidation_module()
        bundle=self.ctl.verification_evidence_module().bundle_module()
        relative=copy.deepcopy(descriptor);relative['sources'][0]['path']='obligations.md'
        with self.assertRaises(self.ctl.ControlError): self.preview(proposed,relative)
        with mock.patch.dict(bundle.DEFAULT_LIMITS,max_total_bytes=1):
            with self.assertRaises(self.ctl.ControlError): self.preview(proposed,descriptor)
        with mock.patch.object(helper.time,'monotonic',side_effect=[0,1000]):
            with self.assertRaises(self.ctl.ControlError): self.preview(proposed,descriptor)
        link=self.project/'linked.md'
        try: link.symlink_to(self.project/'obligations.md')
        except OSError: return  # Other cases ran; real link case is Linux-covered.
        linked=copy.deepcopy(descriptor);linked['sources'][0]['path']=str(link)
        with self.assertRaises(self.ctl.ControlError): self.preview(proposed,linked)

    def test_review_preparation_retirement_writes_no_report(self):
        access,_contract,_item,_run,_bundle=self.ready()
        proposed,descriptor=self.proposal(),self.descriptor()
        stage=self.ctl.stage_module(); original=stage._report_text
        reached,release=threading.Event(),threading.Event()
        def render(*args,**kwargs):
            text=original(*args,**kwargs); reached.set()
            if not release.wait(10): raise RuntimeError('barrier timeout')
            return text
        with mock.patch.object(stage,'_report_text',side_effect=render),ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(self.ctl.review_stage,self.first,request_id='stale-report',**access)
            self.assertTrue(reached.wait(5))
            try: self.apply(proposed,descriptor,**access)
            finally: release.set()
            with self.assertRaises(self.ctl.ControlError): task.result(timeout=10)
        self.assertFalse((self.first/'.stage_acceptance').exists())

    def test_provider_receipt_gap_blocks_revision_and_finish_gap_never_reexecutes(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        proposed,descriptor=self.proposal(),self.descriptor()
        calls=[];original=self.ctl._publish_review
        def provider(request):
            calls.append(request)
            return {key:request[key] for key in ('method','item_sha256','run_id','candidate_id')} | dict(
                outcome='pass',conclusion='Offline fixture',limitations=[],source_id='gap',provider='fixture',model='fixture')
        def publish(*args,**kwargs):
            with self.assertRaises(self.ctl.ControlError): self.preview(proposed,descriptor,**access)
            return original(*args,**kwargs)
        with mock.patch.object(self.ctl,'_publish_review',side_effect=publish),mock.patch.object(self.ctl,'_finish_review_activity',side_effect=self.ctl.ControlError('simulated finish interruption')):
            with self.assertRaises(self.ctl.ControlError):
                self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',trusted_reviewer=provider,request_id='finish-gap',**access)
        self.assertTrue(self.ctl.execution_trace(self.ctl.read_events(self.first)[0])['open_operations'])
        replay=self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',trusted_reviewer=provider,request_id='finish-gap',**access)
        self.assertTrue(replay['already_applied']);self.assertEqual(len(calls),1)
        with self.assertRaises(self.ctl.ControlError): self.preview(proposed,descriptor,**access)

    def test_local_archive_retirement_rejects_before_manifest_writer(self):
        self.enable();self.apply(self.proposal(),self.descriptor())
        with mock.patch.object(self.ctl,'_cmd_archive_manifest',return_value=0) as writer:
            for target in (self.first,self.first/'nested'):
                with self.subTest(target=str(target)),self.assertRaises(self.ctl.ControlError):
                    self.cli('archive-manifest','--dir',str(self.first),'--archive-dir',str(target),'--write')
            writer.assert_not_called()
        # Unit boundary: external backup dispatch stays usable; no fake archive proof.
        with mock.patch.object(self.ctl,'_cmd_archive_manifest',return_value=0) as writer:
            args=self.ctl.build_parser().parse_args(['archive-manifest','--dir',str(self.first),'--archive-dir',str(self.project/'external'),'--write'])
            self.assertEqual(self.ctl.cmd_archive_manifest(args),0);writer.assert_called_once()

    def test_staged_invalid_json_stays_controlled_before_first_write(self):
        self.enable()
        for raw in ('{','{"value":NaN}'):
            path=self.project/'.icode_output/.icode_output_4'
            with self.subTest(raw=raw),self.assertRaises(self.ctl.ControlError):
                self.cli('create','--dir',str(path),'--ticket-id','invalid-json','--requirement','Invalid JSON','--birth','plan','--stage-id','one','--metadata-json',raw)
            self.assertFalse((path/self.ctl.METADATA_NAME).exists())

    def crash_consolidation(self,after_append):
        self.enable();proposed,descriptor=self.proposal(),self.descriptor()
        view=self.preview(proposed,descriptor)
        request=dict(view['challenge'],request_id='crash-merge',reason='Approved consolidation')
        script="""import importlib.util,json,os,sys
spec=importlib.util.spec_from_file_location('crash_control',sys.argv[1]);ctl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctl)
original=ctl.append_prepared_event
def crash(*args,**kwargs):
    if sys.argv[6]=='after': original(*args,**kwargs)
    os._exit(71)
ctl.append_prepared_event=crash
ctl.revise_stage_program(sys.argv[2],json.loads(sys.argv[3]),json.loads(sys.argv[4]),consolidation=json.loads(sys.argv[5]),trusted_confirmation=lambda request:dict(source='host_user',source_id='crash-user',intent='修订阶段计划'))
"""
        result=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,self.ctl.__file__,str(self.coord),json.dumps(proposed),json.dumps(request),json.dumps(descriptor),'after' if after_append else 'before'],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,71,result.stderr.decode('utf-8','replace'))
        self.assertTrue((self.coord/self.ctl.TXN_NAME).exists())
        self.assertTrue(self.ctl.stage_projection(self.coord)['blocked'])
        resumed=self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,trusted_confirmation=lambda request:dict(source='host_user',source_id='crash-user',intent='修订阶段计划'))
        self.assertEqual(resumed['already_applied'],after_append)
        self.assertFalse((self.coord/self.ctl.TXN_NAME).exists())
        self.assertEqual(len(self.ctl.load_metadata(self.coord)['extensions']['stage_program']['plan_revisions']),1)
        self.assertEqual(self.ctl.verify_event_chain(self.coord,self.ctl.load_metadata(self.coord))[1],[])

    def test_consolidation_process_exit_before_event_recovers(self): self.crash_consolidation(False)
    def test_consolidation_process_exit_after_event_is_exact(self): self.crash_consolidation(True)

    def test_sixteen_lifecycle_cells_keep_retirement_visible(self):
        self.enable();proposed,descriptor=self.proposal(),self.descriptor()
        _view,_result,request,trusted=self.apply(proposed,descriptor)
        script="""import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('restart_control',sys.argv[1]);ctl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctl)
print(json.dumps(dict(child=ctl.stage_projection(sys.argv[2]),aggregate=ctl.stage_projection(sys.argv[3]))))
"""
        cells=[]
        for phase in ('immediate','converged','restart','exact_replay'):
            if phase=='exact_replay':
                self.assertTrue(self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,trusted_confirmation=trusted)['already_applied'])
            if phase=='restart':
                proc=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,self.ctl.__file__,str(self.first),str(self.coord)],capture_output=True,text=True,encoding='utf-8',timeout=30)
                self.assertEqual(proc.returncode,0,proc.stderr);views=json.loads(proc.stdout)
            else: views=dict(child=self.ctl.stage_projection(self.first),aggregate=self.ctl.stage_projection(self.coord))
            with self.subTest(phase=phase,entry='direct_query'):
                self.assertEqual(views['child']['state'],'superseded');cells.append((phase,'direct_query'))
            with self.subTest(phase=phase,entry='aggregate_selector'):
                retired={row['stage_id']:row['replacement_stages'] for row in views['aggregate']['retired_stages']}
                self.assertEqual(retired['one'],['merged']);cells.append((phase,'aggregate_selector'))
            with self.subTest(phase=phase,entry='persistent_reference'):
                binding=self.ctl.load_metadata(self.first)['extensions']['stage_binding']
                self.assertEqual(binding['stage_id'],'one')
                self.assertEqual(views['child']['replacement_stages'],['merged']);cells.append((phase,'persistent_reference'))
            with self.subTest(phase=phase,entry='external_projection'):
                policy=self.cli('action-policy','--dir',str(self.first))
                self.assertNotIn('code',policy['allowed_actions'])
                self.assertEqual(policy['blocked_reason'],'superseded_stage')
                self.assertEqual(policy['stage']['state'],'superseded');cells.append((phase,'external_projection'))
        self.assertEqual(len(set(cells)),16)


    def test_malformed_inherited_birth_is_controlled_and_has_no_business_files(self):
        access,_contract,_item,_run,_bundle=self.ready()
        self.apply(self.proposal(),self.descriptor(),**access)
        for value in ([1],{'required':True,'evidence_contract':{'required_items':[1]}}):
            with self.subTest(contract=value),self.assertRaises(self.ctl.ControlError): self.create_target(value,number=7)
            self.assertFalse((self.project/'.icode_output/.icode_output_7'/self.ctl.METADATA_NAME).exists())

    def test_provider_preparation_retired_before_admission_calls_nothing(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        proposed,descriptor=self.proposal(),self.descriptor()
        reader=self.ctl.verification_evidence_module().bundle_module().Reader
        original=reader.read;reached,release=threading.Event(),threading.Event();calls=[]
        def read(instance,path,*args,**kwargs):
            result=original(instance,path,*args,**kwargs)
            if path=='raw.log':
                reached.set()
                if not release.wait(10): raise RuntimeError('barrier timeout')
            return result
        def provider(request): calls.append(request); raise AssertionError('retired provider called')
        with mock.patch.object(reader,'read',read),ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(self.ctl.record_evidence_review,self.first,item=item,run=run,requirement_id='raw',method='ai_review',trusted_reviewer=provider,request_id='prepare-retired',**access)
            self.assertTrue(reached.wait(5))
            try:self.apply(proposed,descriptor,**access)
            finally:release.set()
            with self.assertRaises(self.ctl.ControlError):task.result(timeout=10)
        self.assertEqual(calls,[])
        self.assertFalse(self.ctl.execution_trace(self.ctl.read_events(self.first)[0])['open_operations'])

    def test_visual_provider_is_inflight_for_every_batch_and_negative_is_not_pass(self):
        access,run,_bundle,frames,route=self.visual_bundle()
        proposed,descriptor=self.proposal(),self.descriptor();batches=[]
        def adapter(request):
            batches.append(request)
            self.assertEqual(len(self.ctl.execution_trace(self.ctl.read_events(self.first)[0])['open_operations']),1)
            with self.assertRaises(self.ctl.ControlError): self.preview(proposed,descriptor,**access)
            return dict(status='success',frame_results=[dict(frame_id=row['frame_id'],frame_set_id=row['frame_set_id'],input_sha256=row['input_sha256'],source_sha256=row['source_sha256'],channel=request['channel'],provider='fixture',model='fixture',prompt_version=request['prompt_version'],conclusion='Synthetic offline negative fixture',outcome='fail',limitations=['Not a real camera']) for row in request['frames']])
        result=self.ctl.record_visual_review(self.first,frames=frames,route=route,run=run,prompt_version='display-v1',adapters={'native':adapter},request_id='negative-visual',**access)
        self.assertEqual([len(x['frames']) for x in batches],[4,1]);self.assertEqual(result['outcome'],'fail')
        self.assertFalse(self.ctl.execution_trace(self.ctl.read_events(self.first)[0])['open_operations'])
        events=self.ctl.read_events(self.first)[0]
        publication=next(i for i,e in enumerate(events) if e.get('request_id')=='negative-visual')
        finish=next(i for i,e in enumerate(events) if e.get('request_id')=='review-finish:negative-visual')
        self.assertLess(publication,finish)
        self.assertEqual(events[finish]['payload']['outcome'],'success')

    def test_concurrent_same_consolidation_publishes_once(self):
        self.enable();proposed,descriptor=self.proposal(),self.descriptor()
        view=self.preview(proposed,descriptor);request=dict(view['challenge'],request_id='concurrent-merge',reason='Approved merge')
        trusted=lambda request:dict(source='host_user',source_id='concurrent-user',intent='修订阶段计划')
        def apply():return self.ctl.revise_stage_program(self.coord,proposed,request,consolidation=descriptor,trusted_confirmation=trusted)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:apply(),range(2)))
        self.assertEqual(sum(not x['already_applied'] for x in results),1)
        self.assertEqual(len(self.ctl.load_metadata(self.coord)['extensions']['stage_program']['plan_revisions']),1)

    def test_process_exit_inside_provider_keeps_unknown_operation_and_never_replays(self):
        access,_contract,item,run,_bundle=self.ready(method='ai_review')
        tests=str(Path(__file__).parent)
        script="""import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from test_stage_consolidation import StageConsolidationTests
from test_stage_acceptance import load
f=StageConsolidationTests();f.ctl=load('icode_control');f.project=Path(sys.argv[2]);f.first=Path(sys.argv[3])
p=f.provider()
f.ctl.record_evidence_review(f.first,item=json.loads(sys.argv[4]),run=json.loads(sys.argv[5]),requirement_id='raw',method='ai_review',trusted_reviewer=lambda request:os._exit(71),request_id='killed-provider',git_provider=p,require_git_provider=True)
"""
        child=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,tests,str(self.project),str(self.first),json.dumps(item),json.dumps(run)],capture_output=True,timeout=30)
        self.assertEqual(child.returncode,71,child.stderr.decode('utf-8','replace'))
        self.assertEqual(len(self.ctl.execution_trace(self.ctl.read_events(self.first)[0])['open_operations']),1)
        calls=[]
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.record_evidence_review(self.first,item=item,run=run,requirement_id='raw',method='ai_review',trusted_reviewer=lambda request:calls.append(request),request_id='killed-provider',**access)
        self.assertEqual(calls,[])
        with self.assertRaises(self.ctl.ControlError):self.preview(self.proposal(),self.descriptor(),**access)


del Fixture

if __name__=='__main__':
    unittest.main()
