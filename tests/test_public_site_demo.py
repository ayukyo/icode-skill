"""Use a disposable copy of the real demo; never touch historical demo tickets."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def digest_tree(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class PublicDemoTests(unittest.TestCase):
    def test_real_demo_copy_keeps_private_evidence_out_of_public_artifact(self):
        spec = importlib.util.spec_from_file_location('builder', ROOT / 'tools/build_public_site.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='icode-public-demo-') as temp:
            source = Path(temp) / 'demo'
            source.mkdir()
            # Only these four real source files are copied; never existing tickets.
            for name in ['calc.c', 'calc.h', 'main.c', 'Makefile']:
                shutil.copy2(ROOT / 'demo' / name, source / name)
            shutil.copy2(ROOT / 'SKILL.md', source / 'SKILL.md')
            shutil.copytree(ROOT / 'site', source / 'site')
            shutil.copytree(ROOT / 'assets', source / 'assets')
            for relative in ['.icode_output/.icode_output_1/.ico_metadata.json',
                             '.icode_output/.crosscheck/review.md', 'mcp/private/config.json',
                             'log/device.log', '.env', 'README.md', 'site/unlisted-secret.txt']:
                file = source / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('PRIVATE-DEMO-CANARY-12345', encoding='utf-8')
            before = digest_tree(source)
            for name, base, key in [('project', 'https://ayukyo.github.io/icode-skill/', None),
                                     ('custom', 'https://icode.example.org/', 'demo-test-key-1234')]:
                out = Path(temp) / name
                manifest = builder.build(source, out, base, key)
                self.assertEqual(manifest['urls'], [base, base + 'en/'])
                for content in digest_tree(out):
                    self.assertNotIn(b'PRIVATE-DEMO-CANARY', (out / content).read_bytes())
                with self.assertRaises(ValueError):
                    builder.build(source, out, base, key)
            with self.assertRaises(ValueError):
                builder.build(source, Path(temp) / 'bad-version',
                              'https://icode.example.org/', expected_version='v0.0.0')
            self.assertEqual(before, digest_tree(source))

    def test_builder_manifest_consumed_by_notifier_without_network(self):
        from unittest.mock import patch
        import contextlib
        import io
        import os
        modules = {}
        for name in ['build_public_site', 'notify_indexnow']:
            spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
        with tempfile.TemporaryDirectory(prefix='icode-public-integration-') as temp:
            output = Path(temp) / 'public'
            modules['build_public_site'].build(ROOT, output, 'https://ayukyo.github.io/icode-skill/', 'demo-test-key-1234')
            with patch.dict(os.environ, {'INDEXNOW_KEY': 'demo-test-key-1234'}):
                with patch('socket.getaddrinfo', side_effect=AssertionError('no network allowed')):
                    result = io.StringIO()
                    with contextlib.redirect_stdout(result):
                        code = modules['notify_indexnow'].main(['--manifest', str(output / 'public-manifest.json')])
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(result.getvalue())['status'], 'dry-run')


if __name__ == '__main__':
    unittest.main()
