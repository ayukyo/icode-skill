"""Offline renderer installation, ownership and failure-path contracts."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools/docx'
sys.path.insert(0, str(TOOLS))
import bootstrap_renderer as bootstrap
import renderer_support as support
import resolve_renderer as resolver


class RendererTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='icode-renderer-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'runtime'
        self.cache = self.base / 'cache'
        self.cache.mkdir()
        self.catalog = self.base / 'packages.json'
        self.host = {'os': 'linux', 'arch': 'x86_64', 'libc': 'glibc', 'glibc': '2.35',
                     'kernel': '6.8.0', 'cpu_flags': []}
        # A real deb fixture, extracted by the real dpkg-deb/safe_unpack path.
        self.source = self.base / 'deb-source'
        (self.source / 'DEBIAN').mkdir(parents=True)
        (self.source / 'DEBIAN/control').write_text('Package: icode-renderer-fixture\nVersion: 1.0\nArchitecture: amd64\nMaintainer: Fixture <fixture@example.invalid>\nDescription: offline test\n')
        for label in ('soffice', 'pdftoppm'):
            path = self.source / 'engine' / label
            path.parent.mkdir(exist_ok=True)
            path.write_text('#!/bin/sh\nprintf "fixture version 1.0\\n"\n')
            path.chmod(0o755)
        (self.source / 'lib').mkdir()
        (self.source / 'engine/owned-alias').symlink_to('soffice')
        self.archive = self.cache / 'fixture.deb'
        subprocess.run(['dpkg-deb', '--build', str(self.source), str(self.archive)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.package = {'id': 'fixture', **{k: self.host[k] for k in ('os', 'arch', 'libc')},
                        'glibc_min': '2.35', 'min_free_bytes': 1,
                        'executables': {'soffice': 'root/engine/soffice', 'pdftoppm': 'root/engine/pdftoppm'},
                        'library_dir': 'root/lib', 'artifacts': [{'name': self.archive.name, 'kind': 'deb',
                        'bytes': self.archive.stat().st_size, 'sha256': support.sha256_file(self.archive),
                        'urls': ['https://example.invalid/fixture.deb']}]}
        self.write_catalog()
        self.addCleanup(patch.stopall)
        patch.object(bootstrap, 'host', return_value=self.host).start()
        patch.object(resolver, 'host', return_value=self.host).start()
        patch.dict(os.environ, {'ICODE_DOCX_RENDERER_ROOT': str(self.root)}).start()

    def write_catalog(self):
        self.catalog.write_text(json.dumps({'schema_version': 1, 'packages': [self.package]}))

    def install(self):
        # Fixture executables are shell scripts; ELF probes are covered by the
        # actual upstream-package smoke, not misrepresented by this unit test.
        with patch.object(bootstrap, 'check_probes'):
            return bootstrap.bootstrap(self.catalog, self.root, self.cache, offline=True)

    def test_install_and_reinstall_are_idempotent(self):
        first = self.install()
        self.assertEqual(first['status'], 'installed')
        registry = self.root / 'renderer_manifest.json'
        before = registry.read_bytes(), registry.stat().st_mtime_ns
        self.assertEqual(self.install()['status'], 'ready')
        self.assertEqual(before, (registry.read_bytes(), registry.stat().st_mtime_ns))
        self.assertEqual(resolver.resolve()['status'], 'ready')

    def test_default_resolution_survives_skill_directory_copy(self):
        self.install()
        installed = self.base / 'installed/tools/docx'
        installed.mkdir(parents=True)
        for name in ('resolve_renderer.py', 'renderer_support.py', 'renderer_manifest.json'):
            shutil.copyfile(TOOLS / name, installed / name)
        result = subprocess.run([sys.executable, str(installed / 'resolve_renderer.py')], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'ready')

    def test_explicit_manifest_never_reads_user_registry(self):
        self.install()
        self.assertEqual(resolver.resolve(TOOLS / 'renderer_manifest.json')['status'], 'unavailable')

    def test_check_missing_and_unsupported_are_read_only(self):
        self.assertEqual(bootstrap.bootstrap(self.catalog, self.root, self.cache, check=True)['status'], 'missing')
        self.assertFalse(self.root.exists())
        self.package['arch'] = 'aarch64'
        self.write_catalog()
        self.assertEqual(bootstrap.bootstrap(self.catalog, self.root, self.cache)['status'], 'unavailable')
        self.assertFalse(self.root.exists())

    def test_platform_libc_kernel_and_cpu_gates(self):
        entry = {**self.package, 'kernel_min': '4.18', 'cpu_flags': ['sse4_2']}
        self.assertFalse(support.matches(entry, self.host))
        current = {**self.host, 'cpu_flags': ['sse4_2']}
        self.assertTrue(support.matches(entry, current))
        for updates in ({'glibc': '2.34'}, {'libc': 'musl'}, {'kernel': '4.17'}, {'arch': 'aarch64'}, {'os': 'windows'}):
            self.assertFalse(support.matches(entry, {**current, **updates}))

    def test_corrupt_cache_is_not_replaced_or_downloaded(self):
        self.archive.write_bytes(b'corrupted')
        with patch.object(bootstrap.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                self.install()
        opener.assert_not_called()
        self.assertEqual(self.archive.read_bytes(), b'corrupted')
        self.assertFalse((self.root / 'renderer_manifest.json').exists())

    def test_offline_missing_archive_fails_without_network(self):
        self.archive.unlink()
        with patch.object(bootstrap.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'offline renderer artifact missing'):
                self.install()
        opener.assert_not_called()

    def test_payload_tampering_fails_resolver_wrapper_and_reinstall(self):
        result = self.install()
        bundle = Path(result['renderer']['bundle'])
        registry_before = (self.root / 'renderer_manifest.json').read_bytes()
        (bundle / 'root/engine/soffice').write_text('tampered')
        self.assertEqual(resolver.resolve()['status'], 'invalid_bundle')
        run = subprocess.run([str(bundle / 'bin/soffice'), '--version'], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('SHA-256 mismatch', run.stderr)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.install()
        self.assertEqual((self.root / 'renderer_manifest.json').read_bytes(), registry_before)

    def test_symlink_cannot_redirect_to_a_system_engine(self):
        result = self.install()
        bundle = Path(result['renderer']['bundle'])
        link = bundle / 'root/engine/owned-alias'
        link.unlink()
        link.symlink_to('/bin/true')
        self.assertEqual(resolver.resolve()['status'], 'invalid_bundle')
        run = subprocess.run([str(bundle / 'bin/soffice'), '--version'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn('symlink', run.stderr)

    def test_missing_entry_hash_and_executable_are_rejected(self):
        self.install()
        path = self.root / 'renderer_manifest.json'
        before = json.loads(path.read_text())
        for update in ({'sha256': {}}, {'soffice': 'missing'}, {'soffice': '/usr/bin/true'}, {'soffice': '../outside'}):
            data = copy.deepcopy(before)
            data['renderers'][0].update(update)
            path.write_text(json.dumps(data))
            self.assertEqual(resolver.resolve()['status'], 'invalid_bundle')

    def test_broken_registry_is_preserved(self):
        self.install()
        path = self.root / 'renderer_manifest.json'
        path.write_text('{')
        self.assertEqual(resolver.resolve()['status'], 'invalid_manifest')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(path.read_text(), '{')

    def test_existing_bundle_record_cannot_read_outside_payload(self):
        result = self.install()
        bundle = Path(result['renderer']['bundle'])
        path = bundle / 'renderer_manifest.json'
        data = json.loads(path.read_text())
        data['renderers'][0]['payload_manifest'] = '../outside-not-json'
        path.write_text(json.dumps(data))
        (bundle.parent / 'outside-not-json').write_text('must not be read')
        with self.assertRaisesRegex(ValueError, 'invalid renderer relative path'):
            self.install()

    def test_failed_probe_never_registers_or_commits_bundle(self):
        with patch.object(bootstrap, 'check_probes', side_effect=ValueError('missing shared library')):
            with self.assertRaisesRegex(ValueError, 'missing shared library'):
                bootstrap.bootstrap(self.catalog, self.root, self.cache, offline=True)
        self.assertFalse((self.root / 'renderer_manifest.json').exists())
        self.assertFalse(list(self.root.glob('fixture-*')))

    def test_non_https_and_invalid_artifact_paths_rejected(self):
        for update in ({'urls': ['http://example.invalid/a.deb']}, {'name': '../escape'}, {'sha256': ''}, {'bytes': -1}):
            original = copy.deepcopy(self.package)
            self.package['artifacts'][0].update(update)
            self.write_catalog()
            with self.assertRaises(ValueError):
                bootstrap.catalog_entries(self.catalog)
            self.package = original

    def test_only_transport_failures_retry_trusted_mirror(self):
        artifact = {**self.package['artifacts'][0], 'urls': ['https://example.invalid/one', 'https://example.invalid/two']}
        self.archive.unlink()
        with patch.object(bootstrap.urllib.request, 'build_opener') as create:
            opener = create.return_value
            opener.open.side_effect = [TimeoutError(), TimeoutError()]
            with self.assertRaises(TimeoutError):
                bootstrap.download(artifact, self.cache, False)
            self.assertEqual(opener.open.call_count, 2)
        with patch.object(bootstrap.urllib.request, 'build_opener') as create:
            create.return_value.open.side_effect = urllib.error.HTTPError(artifact['urls'][0], 404, 'not found', {}, None)
            with self.assertRaises(urllib.error.HTTPError):
                bootstrap.download(artifact, self.cache, False)
            self.assertEqual(create.return_value.open.call_count, 1)

    def test_https_redirect_downgrade_rejected(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            bootstrap.HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid/file')

    def test_launcher_revision_changes_runtime_identity(self):
        original = bootstrap.package_key(self.package)
        with patch.object(bootstrap, 'INSTALLER_VERSION', '1.0.1'):
            self.assertNotEqual(bootstrap.package_key(self.package), original)

    def test_version_comparison_and_malformed_manifest_are_bounded(self):
        self.assertTrue(support.version_at_least('2.35', '2.35.0'))
        self.assertFalse(support.version_at_least('unknown', '2.35'))
        self.root.mkdir()
        registry = self.root / 'renderer_manifest.json'
        for data in ([], {'schema_version': 1, 'renderers': {}}, {'schema_version': 1, 'renderers': [None]}):
            registry.write_text(json.dumps(data))
            self.assertEqual(resolver.resolve()['status'], 'invalid_manifest')

    def test_install_lock_serializes_other_process(self):
        command = [sys.executable, '-c',
                   'import sys;sys.path.insert(0,sys.argv[1]);from bootstrap_renderer import install_lock;from pathlib import Path;'
                   '\nwith install_lock(Path(sys.argv[2])):Path(sys.argv[3]).write_text("acquired")',
                   str(TOOLS), str(self.root), str(self.base / 'acquired')]
        with bootstrap.install_lock(self.root):
            process = subprocess.Popen(command)
            try:
                import time
                time.sleep(0.3)
                self.assertFalse((self.base / 'acquired').exists())
                self.assertIsNone(process.poll())
            except BaseException:
                process.terminate()
                process.wait(timeout=5)
                raise
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual((self.base / 'acquired').read_text(), 'acquired')

    def test_archive_rejects_path_link_and_device_escapes_before_writes(self):
        for name, kind, link in [('../escape', tarfile.REGTYPE, ''), ('/escape', tarfile.REGTYPE, ''),
                                 ('link', tarfile.SYMTYPE, '../../escape'), ('link', tarfile.LNKTYPE, '/escape'),
                                 ('device', tarfile.CHRTYPE, '')]:
            with self.subTest(name=name, kind=kind):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w') as tar:
                    item = tarfile.TarInfo(name)
                    item.type, item.linkname = kind, link
                    tar.addfile(item)
                stream.seek(0)
                target = self.base / 'extract'
                target.mkdir(exist_ok=True)
                with tarfile.open(fileobj=stream) as tar:
                    with self.assertRaises(ValueError):
                        bootstrap.safe_unpack(tar, target)
                self.assertEqual(list(target.iterdir()), [])

    def test_installer_and_distribution_carry_same_renderer_contract(self):
        self.assertIn('bootstrap_renderer.py', (ROOT / 'install.sh').read_text())
        for name in ('bootstrap_renderer.py', 'renderer_support.py', 'renderer_launch.py', 'renderer_packages.lock.json'):
            self.assertIn('tools/docx/' + name, (ROOT / 'tools/build_skill_distribution.py').read_text())

    def test_renderer_install_failure_stops_before_mcp_install(self):
        source = self.base / 'installer-source'
        (source / 'scripts').mkdir(parents=True)
        (source / 'mcp').mkdir()
        shutil.copyfile(ROOT / 'install.sh', source / 'install.sh')
        sync = source / 'scripts/sync-to-global.sh'
        sync.write_text('#!/bin/sh\nexit 0\n')
        sync.chmod(0o755)
        (source / 'mcp/install.sh').write_text('#!/bin/sh\nexit 0\n')
        installed = self.base / 'installed'
        (installed / 'tools/docx').mkdir(parents=True)
        trace = self.base / 'install-trace'
        for name, code in [('bootstrap_runtime.py', 0), ('bootstrap_renderer.py', 1)]:
            (installed / 'tools/docx' / name).write_text(
                'from pathlib import Path\nimport sys\n'
                f'with Path({str(trace)!r}).open("a") as f:f.write({name!r}+"\\n")\n'
                f'sys.exit({code})\n')
        (installed / 'mcp').mkdir()
        (installed / 'mcp/install.sh').write_text(f'#!/bin/sh\nprintf "mcp\\n" >> "{trace}"\n')
        env = {**os.environ, 'GLOBAL_DIR': str(installed), 'AGENTS_DIR': str(self.base / 'codex'),
               'ICODE_PYTHON_BIN': sys.executable}
        result = subprocess.run(['bash', str(source / 'install.sh'), '--client', 'claude'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(trace.read_text().splitlines(), ['bootstrap_runtime.py', 'bootstrap_renderer.py'])

    def test_corrupt_manifest_marks_visual_failure_instead_of_pending(self):
        document = self.base / 'input.docx'
        document.write_bytes(b'not-used-before-resolution')
        manifest = self.base / 'broken-renderer.json'
        manifest.write_text('{')
        delivery = self.base / 'delivery.json'
        result = subprocess.run([sys.executable, str(TOOLS / 'render_docx.py'), str(document),
                                 str(self.base / 'preview'), '--manifest', str(manifest),
                                 '--result-manifest', str(delivery)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(delivery.read_text())['visual_qa']['status'], 'visual_qa_failed')

    def test_release_workflow_checks_public_install_sync_and_generated_package(self):
        text = (ROOT / '.github/workflows/docx-renderer.yml').read_text()
        for required in ('runs-on: ubuntu-22.04', 'release:', 'types: [published]', 'workflow_call:', 'permissions:',
                         'contents: read', 'install.sh --client all --skip-mcp',
                         'sync-to-global.sh --apply --client all', 'build_skill_distribution.py',
                         'skills/icode/scripts/check-docx-renderer-release.py'):
            self.assertIn(required, text)
        self.assertNotIn('contents: write', text)
        self.assertNotIn('${{ runner.temp }}', text)
        self.assertIn('$RUNNER_TEMP/icode-docx-renderer', text)
        self.assertIn('>> "$GITHUB_ENV"', text)

    def test_public_distribution_cannot_publish_without_renderer_qualification(self):
        text = (ROOT / '.github/workflows/public-site.yml').read_text()
        renderer, build = text.split('\n  build:\n', 1)
        self.assertIn('uses: ./.github/workflows/docx-renderer.yml', renderer)
        self.assertIn('    needs: renderer\n', build.split('\n  deploy:\n', 1)[0])
        self.assertIn('    needs: build\n', build.split('\n  deploy:\n', 1)[1])
        self.assertNotIn('continue-on-error', renderer)


if __name__ == '__main__':
    unittest.main(verbosity=2)
