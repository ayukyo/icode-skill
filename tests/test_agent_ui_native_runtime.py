"""Owned Windows UI dependency/cache integrity without system changes."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent_runtime'))
from icode_agent import native_runtime as runtime


def package(tmp_path, extra=None):
    members = {'lib/net462/Core.dll': b'core', 'runtimes/win-x64/native/Loader.dll': b'loader',
               'LICENSE.txt': b'license'}
    if extra:
        members.update(extra)
    path = tmp_path / 'sdk.nupkg'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, body in members.items():
            info = zipfile.ZipInfo('entry')
            info.filename = name  # Preserve hostile separators even on Windows.
            archive.writestr(info, body)
    pin = {'version': '1.0', 'bytes': path.stat().st_size,
           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
           'url': 'https://api.nuget.org/sdk.nupkg',
           'files': {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
                     for name, body in members.items() if name in ('lib/net462/Core.dll', 'runtimes/win-x64/native/Loader.dll', 'LICENSE.txt')}}
    return path, pin


def test_verified_sdk_retains_only_pinned_payload_and_license(tmp_path):
    path, pin = package(tmp_path, {'ignored/docs.xml': b'docs'})
    target = tmp_path / 'payload'
    files = runtime.extract_sdk(path, target, pin)
    assert set(files) == {'Core.dll', 'Loader.dll', 'LICENSE.txt'}
    assert (target / 'Core.dll').read_bytes() == b'core'
    assert not (target / 'docs.xml').exists()


@pytest.mark.parametrize('damage', ['size', 'digest'])
def test_integrity_failure_never_unpacks(tmp_path, damage):
    path, pin = package(tmp_path)
    if damage == 'size':
        pin['bytes'] = 1
    else:
        pin['sha256'] = '0' * 64
    target = tmp_path / 'payload'
    with pytest.raises(runtime.NativeRuntimeError, match='integrity'):
        runtime.extract_sdk(path, target, pin)
    assert not target.exists()


@pytest.mark.parametrize('member', ['../escape', '/absolute', 'C:/escape', 'folder\\escape'])
def test_unsafe_archive_rejected_before_writes(tmp_path, member):
    path, pin = package(tmp_path, {member: b'bad'})
    with pytest.raises(runtime.NativeRuntimeError, match='archive'):
        runtime.extract_sdk(path, tmp_path / 'payload', pin)
    assert not (tmp_path / 'payload').exists()


def test_per_file_pin_mismatch_rejected(tmp_path):
    path, pin = package(tmp_path)
    pin['files']['lib/net462/Core.dll']['sha256'] = '0' * 64
    with pytest.raises(runtime.NativeRuntimeError, match='integrity'):
        runtime.extract_sdk(path, tmp_path / 'payload', pin)


def test_cached_package_corruption_is_preserved_and_not_downloaded(tmp_path, monkeypatch):
    path, pin = package(tmp_path)
    path.write_bytes(b'damaged')
    monkeypatch.setattr(runtime, '_download_package', lambda *_: pytest.fail('must not redownload corrupt cache'))
    with pytest.raises(runtime.NativeRuntimeError, match='integrity'):
        runtime.obtain_package(pin, path, offline=False)
    assert path.read_bytes() == b'damaged'


def test_offline_missing_package_never_touches_network(tmp_path, monkeypatch):
    _, pin = package(tmp_path)
    monkeypatch.setattr(runtime, '_download_package', lambda *_: pytest.fail('offline network'))
    with pytest.raises(runtime.NativeRuntimeError, match='offline'):
        runtime.obtain_package(pin, tmp_path / 'absent.nupkg', offline=True)


def test_online_download_closes_windows_file_before_atomic_rename(tmp_path, monkeypatch):
    path, pin = package(tmp_path)
    response = io.BytesIO(path.read_bytes())
    response.url = pin['url']
    class Opener:
        def open(self, *args, **kwargs):
            return response
    monkeypatch.setattr(runtime.urllib.request, 'build_opener', lambda *_: Opener())
    target = tmp_path / 'downloaded.nupkg'
    runtime._download_package(pin, target)
    runtime.verify_package(target, pin)
    assert not list(tmp_path.glob('.native-sdk-*'))


def test_runtime_identity_binds_source_pin_and_compiler(tmp_path):
    source, compiler = tmp_path / 'host.cs', tmp_path / 'csc.exe'
    source.write_bytes(b'source'); compiler.write_bytes(b'compiler')
    first = runtime.runtime_key(source, {'sha256': 'a'}, compiler)
    source.write_bytes(b'updated')
    assert first != runtime.runtime_key(source, {'sha256': 'a'}, compiler)
    assert first != runtime.runtime_key(source, {'sha256': 'b'}, compiler)


def test_non_windows_fails_before_creating_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'platform', 'linux')
    with pytest.raises(runtime.NativeRuntimeError, match='Windows'):
        runtime.ensure_native_runtime(cache_root=tmp_path / 'absent')
    assert not (tmp_path / 'absent').exists()


def test_current_lock_has_all_required_owned_windows_payloads():
    pin = runtime.load_lock()
    assert pin['version'] == '1.0.3650.58'
    assert set(Path(name).name for name in pin['files']) == {
        'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll',
        'WebView2Loader.dll', 'LICENSE.txt'}
    assert pin['url'].startswith('https://api.nuget.org/')


@pytest.mark.parametrize('value', ['{', '[]', '{"files": []}', '{"schema_version": 1, "version": 3}'])
def test_malformed_lock_reports_actionable_error(tmp_path, value):
    path = tmp_path / 'lock.json'
    path.write_text(value, encoding='utf-8')
    with pytest.raises(runtime.NativeRuntimeError, match='lock'):
        runtime.load_lock(path)


def test_bundle_manifest_wrong_types_are_integrity_errors(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'manifest.json').write_text(json.dumps({'identity': 'key', 'files': ['Core.dll', 'ICODEUI.exe']}))
    with pytest.raises(runtime.NativeRuntimeError, match='integrity'):
        runtime._validate_bundle(bundle, 'key', {'files': {'Core.dll': {'sha256': '0' * 64}}})


def test_webview_probe_timeout_is_actionable(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise runtime.subprocess.TimeoutExpired('probe', 15)
    monkeypatch.setattr(runtime.subprocess, 'run', timeout)
    with pytest.raises(runtime.NativeRuntimeError, match='WebView2'):
        runtime._check_webview2(tmp_path / 'loader.dll')
