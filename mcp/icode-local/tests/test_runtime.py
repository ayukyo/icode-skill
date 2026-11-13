from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / 'mcp' / 'icode-local'
MODULES = {'workspace': 'icode-workspace', 'evidence': 'icode-evidence', 'index': 'icode-local-index', 'health': 'icode-mcp-health', 'policy': 'icode-mcp-policy'}
ENV = {'workspace': 'ICODE_WORKSPACE_CONFIG', 'evidence': 'ICODE_EVIDENCE_CONFIG', 'index': 'ICODE_LOCAL_INDEX_CONFIG', 'health': 'ICODE_MCP_HEALTH_CONFIG', 'policy': 'ICODE_MCP_POLICY_CONFIG'}

def listed(server):
    coroutine = server.mcp.list_tools()
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("tools/list unexpectedly performs asynchronous I/O")

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((LOCAL / 'server.py').exists(), 'unified local runtime is not implemented')
        self.server = load(LOCAL / 'server.py', 'local_under_test')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.profile = self.base / 'profile.json'
        self.profile.write_text('{"module_configs": {}}')
        self.environment = mock.patch.dict(os.environ, {'ICODE_LOCAL_CONFIG': str(self.profile)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for value in ENV.values():
            patch = mock.patch.dict(os.environ)
            patch.start(); self.addCleanup(patch.stop)
            os.environ.pop(value, None)

    def test_list_and_static_describe_are_lazy_and_match_all_old_contracts(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
            tools = listed(self.server)
            self.server.describe_capabilities()
        self.assertEqual(self.server._MODULES, {})
        actual = {tool.name: tool for tool in tools}
        self.assertEqual(len(actual), 28)
        manifest = json.loads((LOCAL / 'tools_manifest.json').read_text())
        self.assertEqual(set(actual), {row['name'] for row in manifest['tools']})
        for prefix, directory in MODULES.items():
            old = load(ROOT / 'mcp' / directory / 'server.py', 'baseline_' + prefix)
            for tool in listed(old):
                new = actual[prefix + '_' + tool.name]
                self.assertEqual(new.inputSchema, tool.inputSchema)
                self.assertEqual(new.outputSchema, tool.outputSchema)
                self.assertEqual(new.annotations, tool.annotations)

    def test_module_configs_are_isolated_and_explicit_env_has_priority(self):
        allowed = self.base / 'allowed'; allowed.mkdir()
        evidence = self.base / 'evidence'; evidence.mkdir()
        file = allowed / 'sample.txt'; file.write_text('sample')
        workspace_cfg = self.base / 'workspace.json'
        workspace_cfg.write_text(json.dumps({'allowed_roots': [str(allowed)]}))
        evidence_cfg = self.base / 'evidence.json'
        evidence_cfg.write_text(json.dumps({'allowed_roots': [str(evidence)]}))
        self.profile.write_text(json.dumps({'module_configs': {'workspace': str(workspace_cfg), 'evidence': str(evidence_cfg)}}))
        self.assertEqual(self.server.workspace_describe_capabilities()['answer']['access_policy']['allowed_roots'], [str(allowed)])
        self.assertEqual(self.server.evidence_inspect_file(str(file))['diagnostic']['kind'], 'mcp_allowed_roots')
        os.environ[ENV['evidence']] = str(workspace_cfg)
        self.assertEqual(self.server.evidence_inspect_file(str(file))['answer']['sha256'], __import__('hashlib').sha256(b'sample').hexdigest())

    def test_missing_or_malformed_explicit_configs_fail_closed(self):
        missing = self.base / 'missing.json'
        self.profile.write_text(json.dumps({'module_configs': {'evidence': str(missing)}}))
        self.assertIn('error_code', self.server.evidence_inspect_file(str(self.base)))
        self.profile.write_text('{broken')
        self.assertIn('error_code', self.server.evidence_inspect_file(str(self.base)))
        self.profile.write_text('{"module_configs": {}}')
        os.environ[ENV['evidence']] = str(missing)
        self.assertIn('error_code', self.server.evidence_inspect_file(str(self.base)))

    def test_runtime_rejects_unknown_module_keys_and_invalid_unused_values(self):
        for mapping in ({'workspce': str(self.base / 'restricted.json')},
                        {'evidence': None}, {'evidence': ''}, {'evidence': ' '},
                        {'evidence': {}}, {'evidence': []}, {'evidence': 10}):
            with self.subTest(mapping=mapping):
                self.profile.write_text(json.dumps({'module_configs': mapping}))
                result = self.server.workspace_describe_capabilities()
                self.assertIn('error_code', result)
                self.assertNotIn('answer', result)

    def test_index_writes_only_its_original_data_dir_and_matches_baseline(self):
        source = self.base / 'source'; source.mkdir()
        (source / 'one.txt').write_text('local needle')
        data = self.base / 'index-data'
        config = self.base / 'index.json'
        config.write_text(json.dumps({'allowed_roots': [str(source)], 'data_dir': str(data)}))
        self.profile.write_text(json.dumps({'module_configs': {'index': str(config)}}))
        built = self.server.index_build_index(str(source), max_files=3)
        self.assertEqual(Path(built['answer']['database']).parent, data)
        found = self.server.index_query_index(str(source), 'needle')['answer']
        self.assertEqual(found['count'], 1)
        self.assertEqual(set(self.server._MODULES), {'index'})
        baseline = load(ROOT / 'mcp' / MODULES['index'] / 'server.py', 'baseline_index_call')
        with mock.patch.dict(os.environ, {ENV['index']: str(config)}):
            self.assertEqual(self.server.index_query_index(str(source), 'needle'), baseline.query_index(str(source), 'needle'))

if __name__ == '__main__':
    unittest.main()
