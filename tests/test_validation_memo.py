"""Pure validation performance contracts; no Git, workflow or device writes."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'

def load(name):
    spec = importlib.util.spec_from_file_location('memo_test_' + name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def snapshot(api):
    root = str(Path(tempfile.gettempdir()).resolve())
    row = dict(path='source.py',kind='file',mode='100644',sha256='a'*64,size=1,git_oid='3'*40)
    value = dict(identity=dict(common_git_dir=root,checkout_realpath=root,object_format='sha1',
        common_git_device=0,common_git_inode=0,checkout_device=0,checkout_inode=0),
        base='1'*40,head='1'*40,head_tree='2'*40,version=1,code_files=['source.py'],
        scope_contract=None,inspection_scope={},excluded_side_effects=[],
        control_artifact_exclusions=list(api.CONTROL_DIRS),manifest=[row],
        untracked_manifest=[],index_manifest=[dict(path='source.py',mode='100644',oid='3'*40)])
    keys=('identity','base','code_files','scope_contract','inspection_scope','excluded_side_effects',
          'control_artifact_exclusions','manifest')
    value['content_id'] = api._digest({k:value[k] for k in keys})
    value['candidate_id'] = api._digest(value)
    return value


class ControlReadObservationTests(unittest.TestCase):
    def setUp(self):
        self.api = load('icode_control')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def observe(self):
        obs = self.api._ControlReadObservation(self.root)
        meta = self.api.load_metadata(self.root, _observation=obs)
        events, path = self.api.read_events(self.root, _observation=obs)
        obs.bind(self.root, meta, events)
        return obs, meta, events

    def test_raw_hash_and_legacy_utf8_universal_newline_parsing_match(self):
        import hashlib
        for newline in (b'\n', b'\r\n', b'\r'):
            with self.subTest(newline=newline):
                metadata = '{\n"name":"摄像头"\n}\n'.encode('utf-8').replace(b'\n', newline)
                # Unicode U+2028 belongs to the string, not a file-line boundary.
                ledger = ('{"text":"中文\u2028内容"}\n\n{"n":2}\n').encode('utf-8').replace(b'\n', newline)
                (self.root / self.api.METADATA_NAME).write_bytes(metadata)
                (self.root / self.api.EVENTS_NAME).write_bytes(ledger)
                obs, meta, events = self.observe()
                self.assertEqual(meta, self.api.load_metadata(self.root))
                self.assertEqual(events, self.api.read_events(self.root)[0])
                self.assertEqual(len(events), 2)
                self.assertEqual(obs.files[self.api.METADATA_NAME][2], hashlib.sha256(metadata).hexdigest())
                self.assertEqual(obs.files[self.api.EVENTS_NAME][2], hashlib.sha256(ledger).hexdigest())
                actual = obs.check(self.root)
                self.assertIs(actual[0], meta)
                self.assertIs(actual[1], events)

    def test_control_change_pending_and_root_replacement_reject(self):
        for mode in ('bytes', 'pending', 'root', 'file'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as name:
                self.root = Path(name) / 'control'
                self.root.mkdir()
                (self.root / self.api.METADATA_NAME).write_bytes(b'{"a":1}')
                (self.root / self.api.EVENTS_NAME).write_bytes(b'{"n":1}\n')
                obs, _, _ = self.observe()
                if mode == 'bytes':
                    (self.root / self.api.EVENTS_NAME).write_bytes(b'{"n":2}\n')
                elif mode == 'pending':
                    (self.root / self.api.TXN_NAME).write_bytes(b'{}')
                elif mode == 'root':
                    self.root.rename(self.root.with_name('old'))
                    self.root.mkdir()
                else:
                    target = self.root / self.api.EVENTS_NAME
                    replacement = self.root / 'replacement'
                    replacement.write_bytes(target.read_bytes())
                    replacement.replace(target)
                with self.assertRaises(self.api.ControlError):
                    obs.check(self.root)

    def test_incomplete_or_override_observation_never_attests_disk_metadata(self):
        (self.root / self.api.METADATA_NAME).write_bytes(b'{}')
        obs = self.api._ControlReadObservation(self.root)
        with self.assertRaises(self.api.ControlError): obs.check(self.root)
        with self.assertRaises(self.api.ControlError): obs.bind(self.root, {}, [])
        with self.assertRaises(self.api.ControlError):
            self.api.stage_module()._verified(self.root, _metadata_override={}, _observation=obs)

class ProductionValidationTests(unittest.TestCase):
    def test_same_candidate_avoids_repeating_expensive_path_validation(self):
        api=load('icode_candidate'); value=snapshot(api)
        with mock.patch.object(api,'_paths',wraps=api._paths) as paths:
            self.assertTrue(api.snapshot_identity_ok(value))
            first=paths.call_count
            self.assertGreater(first,0)
            self.assertTrue(api.snapshot_identity_ok(copy.deepcopy(value)))
            self.assertEqual(paths.call_count,first,'Identical complete pure input repeated expensive validation')

    def test_candidate_mutation_and_actual_rule_change_never_hit_prior_validity(self):
        api=load('icode_candidate'); value=snapshot(api)
        self.assertTrue(api.snapshot_identity_ok(value))
        changed=copy.deepcopy(value);changed['manifest'][0]['sha256']='b'*64
        self.assertFalse(api.snapshot_identity_ok(changed))
        with mock.patch.object(api,'CONTROL_DIRS',('.other_control',)):
            self.assertFalse(api.snapshot_identity_ok(value))
        self.assertTrue(api.snapshot_identity_ok(value))
        changed=copy.deepcopy(value);changed['version']=True
        self.assertFalse(api.snapshot_identity_ok(changed))

    def test_large_schema_hot_success_does_not_walk_rows_again(self):
        api=load('icode_control')
        values=[dict(name='item') for _ in range(100)]
        schema=dict(type='array',items=dict(type='object',properties=dict(name=dict(type='string',pattern='^item$'))))
        with mock.patch.object(api,'_check_type',wraps=api._check_type) as check:
            errors=[]
            self.assertIs(api.check_schema(values,schema,'$.first',errors),errors)
            first=check.call_count
            self.assertGreater(first,100)
            errors=['earlier error']
            self.assertIs(api.check_schema(copy.deepcopy(values),schema,'$.second',errors),errors)
            self.assertEqual(errors,['earlier error'])
            self.assertEqual(check.call_count,first,'Hot successful array repeated recursive validation')

    def test_schema_changes_and_invalid_diagnostics_keep_original_order_and_path(self):
        api=load('icode_control')
        values=['ok']*100
        schema=dict(type='array',items=dict(type='string',pattern='^ok$'))
        self.assertEqual(api.check_schema(values,schema),[])
        schema['items']['pattern']='^different$'
        errors=api.check_schema(values,schema,'$.changed')
        self.assertEqual(len(errors),100)
        self.assertTrue(errors[0].startswith('$.changed[0]:'))
        self.assertTrue(errors[-1].startswith('$.changed[99]:'))
        schema['items']['pattern']='^ok$';values[0]=1
        errors=['existing']
        self.assertIs(api.check_schema(values,schema,'$.mutated',errors),errors)
        self.assertEqual(len(errors),2)
        self.assertTrue(errors[1].startswith('$.mutated[0]:'))

class PureMemoTests(unittest.TestCase):
    def setUp(self):
        if not (TOOLS/'validation_memo.py').is_file():
            self.skipTest('New helper not implemented; production repeat-work tests supply valid RED')
        self.api=load('validation_memo')

    def memo(self,check,**kwargs):
        return self.api.PureJsonMemo(check,**kwargs)

    def test_complete_value_and_rules_recompute_without_aliasing(self):
        calls=[]
        def check(value,rules):
            calls.append(copy.deepcopy((value,rules)))
            return value==rules
        memo=self.memo(check)
        self.assertTrue(memo.check({'x':[1]}, {'x':[1]}))
        self.assertTrue(memo.check({'x':[1]}, {'x':[1]}))
        self.assertEqual(len(calls),1)
        self.assertFalse(memo.check({'x':[2]}, {'x':[1]}))
        self.assertFalse(memo.check({'x':[1]}, {'x':[2]}))
        self.assertEqual(len(calls),3)

    def test_subclasses_non_json_and_cycles_fall_back_without_coercion(self):
        class CustomInt(int):pass
        class CustomList(list):pass
        class CustomDict(dict):pass
        cases=[(1,),{1:'value'},CustomInt(1),CustomList([1]),CustomDict(x=1),float('nan')]
        cycle=[];cycle.append(cycle);cases.append(cycle)
        for value in cases:
            seen=[]
            memo=self.memo(lambda actual,rules:seen.append(actual) or True)
            self.assertTrue(memo.check(value));self.assertTrue(memo.check(value))
            self.assertEqual(len(seen),2)
            self.assertIs(seen[0],value)
            self.assertIs(seen[1],value)

    def test_hot_keys_keep_exact_types_and_runtime_fallback_semantics(self):
        class CustomInt(int):pass
        class CustomDict(dict):pass
        for fast in (True,False):
            with mock.patch.object(self.api,'_TYPED_KEY_SAFE',fast):
                memo=self.memo(lambda value,rules:type(value) is int)
                self.assertTrue(memo.check(1))
                for value in (True,1.0,CustomInt(1)):
                    self.assertFalse(memo.check(value))
                memo=self.memo(lambda value,rules:type(value) is dict)
                self.assertTrue(memo.check({'x':1}))
                self.assertFalse(memo.check(CustomDict(x=1)))

    def test_checker_mutation_does_not_poison_input_or_cached_values(self):
        value={'items':[1]}
        def check(actual,rules):actual['items'].append(2);return True
        memo=self.memo(check)
        self.assertTrue(memo.check(value))
        self.assertEqual(value,{'items':[1]})
        self.assertTrue(memo.check(value))
        self.assertEqual(value,{'items':[1]})

    def test_eviction_and_oversize_only_affect_work_not_validity(self):
        calls=[]
        memo=self.memo(lambda value,rules:calls.append(value) or True,
            max_entries=2,max_key_bytes=100,max_item_bytes=60)
        for value in ('a','b','c','a'):
            self.assertTrue(memo.check(value))
        self.assertEqual(len(calls),4)
        value='x'*100
        self.assertTrue(memo.check(value));self.assertTrue(memo.check(value))
        self.assertEqual(len(calls),6)
        stats=memo.stats()
        self.assertLessEqual(stats['entries'],2)
        self.assertLessEqual(stats['key_bytes'],100)
        self.assertGreater(stats['bypasses'],0)

    def test_exceptions_and_failures_are_not_cached_as_success(self):
        def fail(value,rules):raise ValueError('actual invalid input')
        memo=self.memo(fail)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'actual invalid input'):memo.check({'x':1})
        calls=[]
        memo=self.memo(lambda value,rules:calls.append(value) or False)
        self.assertFalse(memo.check({'x':1}));self.assertFalse(memo.check({'x':1}))
        self.assertEqual(len(calls),2)

    def test_concurrent_checks_are_bounded_and_keep_distinct_results(self):
        memo=self.memo(lambda value,rules:value['valid'],max_entries=4)
        values=[{'valid':bool(i%2),'value':i%8} for i in range(128)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(memo.check,values))
        self.assertEqual(result,[v['valid'] for v in values])
        self.assertLessEqual(memo.stats()['entries'],4)

    def test_interleaved_input_or_rule_change_cannot_cache_another_value(self):
        for changed in ('value', 'rules'):
            with self.subTest(changed=changed):
                value, rules = {'accepted': False}, {'accepted': False}
                target = value if changed == 'value' else rules
                checker = lambda actual, policy: (actual if changed == 'value' else policy)['accepted']
                with mock.patch.object(self.api, '_TYPED_KEY_SAFE', True):
                    memo = self.memo(checker)
                ordinary = self.api._ordinary_json
                def interleave(item, max_nodes):
                    target['accepted'] = True
                    return ordinary(item, max_nodes)
                with mock.patch.object(self.api, '_ordinary_json', side_effect=interleave):
                    self.assertTrue(memo.check(value, rules))
                target['accepted'] = False
                self.assertFalse(memo.check(value, rules), 'A rejected value reused another frozen value success')

    def test_unicode_surrogate_values_keys_and_rules_keep_original_checker(self):
        pair = '\ud83d\ude00'
        cases = [
            (pair, None, lambda value, rules: len(value) == 1),
            ({pair: 1}, None, lambda value, rules: len(next(iter(value))) == 1),
            ('ordinary', {'name': pair}, lambda value, rules: len(rules['name']) == 1),
        ]
        for fast in (True, False):
            for number, (value, rules, checker) in enumerate(cases):
                with self.subTest(fast=fast, case=number), mock.patch.object(self.api, '_TYPED_KEY_SAFE', fast):
                    calls = []
                    def observed(actual, policy):
                        calls.append((actual is value, policy is rules))
                        return checker(actual, policy)
                    memo = self.memo(observed)
                    self.assertFalse(memo.check(value, rules))
                    self.assertFalse(memo.check(value, rules))
                    self.assertEqual(calls, [(True, True), (True, True)])

    def test_dictionary_insertion_order_is_preserved_in_cached_checker_input(self):
        for fast in (True, False):
            with self.subTest(fast=fast), mock.patch.object(self.api, '_TYPED_KEY_SAFE', fast):
                calls = []
                def checker(value, rules):
                    calls.append(list(value))
                    return list(value) == ['z', 'a']
                memo = self.memo(checker)
                self.assertTrue(memo.check({'z': 1, 'a': 2}))
                self.assertTrue(memo.check({'z': 1, 'a': 2}))
                self.assertEqual(calls, [['z', 'a']])

    def test_unicode_non_bmp_character_remains_a_supported_hot_value(self):
        for fast in (True, False):
            with self.subTest(fast=fast), mock.patch.object(self.api, '_TYPED_KEY_SAFE', fast):
                calls = []
                memo = self.memo(lambda value, rules: calls.append(value) or len(value) == 1)
                self.assertTrue(memo.check('\U0001f600'))
                self.assertTrue(memo.check('\U0001f600'))
                self.assertEqual(calls, ['\U0001f600'])

if __name__=='__main__':unittest.main()
