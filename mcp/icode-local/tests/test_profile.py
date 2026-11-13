from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest import mock
from test_runtime import LOCAL, load, MODULES, ENV

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((LOCAL / 'profile.py').exists(), 'profile migration CLI is missing')
        self.profile = load(LOCAL / 'profile.py', 'profile_under_test')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.target = self.base / 'new' / 'icode-local'
        self.config = self.base / 'workspace.json'
        self.config.write_text(json.dumps({'allowed_roots': [str(self.base / 'restricted')]}))

    def entries(self, host, name):
        if name == MODULES['workspace']:
            return {'command': 'python', 'args': [str(self.base / 'old/server.py')], 'env': {ENV['workspace']: str(self.config)}}
        return None

    def test_preflight_is_read_only_setup_writes_only_paths(self):
        with mock.patch.object(self.profile, '_actual', side_effect=self.entries):
            proposed = self.profile.prepare_profile(self.target, ['claude'])
            self.assertFalse(self.target.exists())
            self.assertEqual(proposed['module_configs'], {'workspace': str(self.config)})
            self.profile.setup_profile(self.target, ['claude'])
        actual = json.loads((self.target / 'config.json').read_text())
        self.assertEqual(actual, proposed)
        self.assertNotIn('restricted', (self.target / 'config.json').read_text())

    def test_conflicting_host_paths_refuse_even_identical_contents(self):
        other = self.base / 'other.json'; other.write_bytes(self.config.read_bytes())
        def entries(host, name):
            row = self.entries(host, name)
            if row and host == 'codex':
                row = {'transport': {**row, 'env': {ENV['workspace']: str(other)}}}
            return row
        with mock.patch.object(self.profile, '_actual', side_effect=entries):
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                self.profile.prepare_profile(self.target, ['claude', 'codex'])
        self.assertFalse(self.target.exists())

    def test_explicit_missing_bad_or_relative_unanchored_config_refuses(self):
        with mock.patch.object(self.profile, '_actual', side_effect=self.entries):
            self.config.unlink()
            with self.assertRaises(RuntimeError):
                self.profile.prepare_profile(self.target, ['claude'])
            self.config.write_text('{bad-secret-value')
            with self.assertRaises(RuntimeError) as error:
                self.profile.prepare_profile(self.target, ['claude'])
            self.assertNotIn('secret-value', str(error.exception))
        row = {'env': {ENV['workspace']: 'relative.json'}}
        with mock.patch.object(self.profile, '_actual', return_value=row):
            with self.assertRaises(RuntimeError):
                self.profile.prepare_profile(self.target, ['claude'])

    def test_existing_profile_is_preserved_and_conflict_is_not_overwritten(self):
        self.target.mkdir(parents=True)
        existing = self.target / 'config.json'
        existing.write_text(json.dumps({'module_configs': {'workspace': str(self.config)}}))
        before = existing.read_bytes()
        with mock.patch.object(self.profile, '_actual', return_value=None):
            self.profile.setup_profile(self.target, ['claude'])
        self.assertEqual(existing.read_bytes(), before)
        alternate = self.base / 'alternate.json'; alternate.write_text('{}')
        existing.write_text(json.dumps({'module_configs': {'workspace': str(alternate)}}))
        before = existing.read_bytes()
        with mock.patch.object(self.profile, '_actual', side_effect=self.entries):
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                self.profile.setup_profile(self.target, ['claude'])
        self.assertEqual(existing.read_bytes(), before)

    def test_custom_policy_dependency_under_replaced_payload_refuses(self):
        old = self.base / 'old-policy'; old.mkdir()
        policy = old / 'policy.json'; policy.write_text('{"default": "allow"}')
        config = self.base / 'policy-config.json'
        config.write_text(json.dumps({'policy_file': str(policy)}))
        entry = {'args': [str(old / 'server.py')], 'env': {ENV['policy']: str(config)}}
        with mock.patch.object(self.profile, '_actual', side_effect=lambda host, name: entry if name == MODULES['policy'] else None):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                self.profile.prepare_profile(self.target, ['claude'])
        self.assertFalse(self.target.exists())

    def test_reinstall_also_protects_retained_default_policy_dependency(self):
        self.target.mkdir(parents=True)
        (self.target / 'config.json').write_text('{"module_configs": {}}')
        old = self.target.parent / MODULES['policy']; old.mkdir()
        (old / 'policy.json').write_text('{"default": "allow"}')
        with mock.patch.object(self.profile, '_actual', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                self.profile.prepare_profile(self.target, ['claude'])

    def test_relative_module_paths_cannot_silently_change_their_base(self):
        config = self.base / 'relative-index.json'
        config.write_text(json.dumps({'allowed_roots': [str(self.base)], 'data_dir': 'relative-data'}))
        entry = {'args': [str(self.base / 'old/server.py')], 'cwd': str(self.base / 'old'),
                 'env': {ENV['index']: str(config)}}
        with mock.patch.object(self.profile, '_actual', side_effect=lambda host, name: entry if name == MODULES['index'] else None):
            with self.assertRaisesRegex(RuntimeError, 'relative'):
                self.profile.prepare_profile(self.target, ['claude'])

    def test_payload_staging_does_not_copy_private_config_or_runtime(self):
        staging = load(LOCAL / 'stage_payload.py', 'staging_under_test')
        source = self.base / 'source/icode-local'; source.mkdir(parents=True)
        for member in MODULES.values():
            folder = source.parent / member; folder.mkdir()
            (folder / 'server.py').write_text('pass')
            (folder / 'config.json').write_text('{}')
            (folder / 'private-config.json').write_text('private')
            (folder / '.env').write_text('private')
            (folder / '.venv').mkdir(); (folder / '.venv/marker').write_text('runtime')
        staging.stage_payload(source, self.target)
        for member in MODULES.values():
            folder = self.target.parent / member
            self.assertTrue((folder / 'server.py').is_file())
            self.assertEqual({path.name for path in folder.iterdir()}, {'server.py'})

    def test_preflight_rejects_active_dependencies_in_retired_runtime_without_writes(self):
        old = self.base / 'old' / MODULES['index']
        for runtime in ('.cache', '.venv', '__pycache__'):
            for dependency in ('config', 'index', 'policy'):
                with self.subTest(runtime=runtime, dependency=dependency):
                    target = self.base / ('new-' + runtime + '-' + dependency) / 'icode-local'
                    folder = old / runtime; folder.mkdir(parents=True, exist_ok=True)
                    database = folder / 'actual-index-data' / 'existing.sqlite3'
                    database.parent.mkdir(exist_ok=True); database.write_bytes(b'business database')
                    config = folder / 'active.json' if dependency == 'config' else self.base / 'active.json'
                    module = 'policy' if dependency == 'policy' else 'index'
                    values = {'data_dir': str(database.parent)} if dependency == 'index' else {}
                    if dependency == 'policy':
                        policy = folder / 'active-policy.json'
                        policy.write_bytes((LOCAL.parent / MODULES['policy'] / 'policy.json').read_bytes())
                        values['policy_file'] = str(policy)
                    config.write_text(json.dumps(values))
                    row = {'args': [str(old / 'server.py')], 'env': {ENV[module]: str(config)}}
                    with mock.patch.object(self.profile, '_actual', side_effect=lambda host, name: row if name == MODULES[module] else None):
                        with self.assertRaisesRegex(RuntimeError, 'runtime'):
                            self.profile.setup_profile(target, ['claude'])
                    self.assertFalse(target.exists())
                    self.assertEqual(database.read_bytes(), b'business database')
                    self.assertTrue(config.exists())

    def test_runtime_dependency_api_checks_current_profile_before_archive(self):
        old = self.base / 'old' / MODULES['index']; old.mkdir(parents=True)
        config = self.base / 'active-index.json'
        data = old / '.cache' / 'active-index'
        config.write_text(json.dumps({'data_dir': str(data)}))
        profile = self.base / 'active-profile.json'
        profile.write_text(json.dumps({'module_configs': {'index': str(config)}}))
        self.assertTrue(hasattr(self.profile, 'check_runtime_dependencies'), 'archive dependency recheck API is missing')
        with self.assertRaisesRegex(RuntimeError, 'runtime'):
            self.profile.check_runtime_dependencies(profile, [old])
        safe = self.base / '.cache' / 'outside-module'
        config.write_text(json.dumps({'data_dir': str(safe)}))
        self.profile.check_runtime_dependencies(profile, [old])
        nested_profile = old / '.venv' / 'active-profile.json'
        nested_profile.parent.mkdir(parents=True)
        nested_profile.write_bytes(profile.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'runtime'):
            self.profile.check_runtime_dependencies(nested_profile, [old])

    def test_preflight_rejects_active_config_alias_that_publication_would_change(self):
        old = self.target.parent / MODULES['workspace']; old.mkdir(parents=True)
        config = old / 'config.example.json'
        config.write_text(json.dumps({'allowed_roots': [str(self.base / 'restricted')]}))
        before = config.read_bytes()
        node = {'args': [str(old / 'server.py')], 'env': {ENV['workspace']: str(config)}}
        with mock.patch.object(self.profile, '_actual', side_effect=lambda host, name: node if name == MODULES['workspace'] else None):
            with self.assertRaisesRegex(RuntimeError, 'payload'):
                self.profile.prepare_profile(self.target, ['claude'])
        self.assertFalse(self.target.exists())
        self.assertEqual(config.read_bytes(), before)

    def test_staging_rechecks_active_config_alias_before_any_copy(self):
        staging = load(LOCAL / 'stage_payload.py', 'staging_alias_under_test')
        source = self.base / 'source/icode-local'; source.mkdir(parents=True)
        for member in MODULES.values():
            folder = source.parent / member; folder.mkdir()
            (folder / 'server.py').write_text('pass')
            (folder / 'config.example.json').write_text('{"allowed_roots": null}')
        old = self.target.parent / MODULES['workspace']; old.mkdir(parents=True)
        config = old / 'config.example.json'
        config.write_text(json.dumps({'allowed_roots': [str(self.base / 'restricted')]}))
        before = config.read_bytes()
        self.target.mkdir()
        profile = self.target / 'config.json'
        profile.write_text(json.dumps({'module_configs': {'workspace': str(config)}}))
        with self.assertRaisesRegex(RuntimeError, 'payload'):
            staging.stage_payload(source, self.target)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((old / 'server.py').exists())

if __name__ == '__main__': unittest.main()
