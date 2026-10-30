"""Prepare the owned Windows WebView2 host without changing system policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
LOCK = HERE / 'native_runtime.lock.json'
HOST_SOURCE = HERE / 'windows_ui_host.cs'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
DOTNET48_RELEASE = 528040
TRUSTED_HOSTS = {'api.nuget.org', 'globalcdn.nuget.org'}


class NativeRuntimeError(RuntimeError):
    """The native UI cannot be safely prepared on this host."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise NativeRuntimeError('native runtime path contains a link')
        try:
            flags = getattr(item.lstat(), 'st_file_attributes', 0)
        except FileNotFoundError:
            continue
        if flags & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise NativeRuntimeError('native runtime path contains a reparse point')


def load_lock(path: Path = LOCK) -> dict:
    try:
        pin = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise NativeRuntimeError('invalid native SDK lock') from exc
    if (not isinstance(pin, dict) or pin.get('schema_version') != 1
            or not isinstance(pin.get('version'), str) or not re.fullmatch(r'[0-9.]+', pin['version'])
            or not isinstance(pin.get('bytes'), int) or not 0 < pin['bytes'] <= MAX_ARCHIVE_BYTES
            or not isinstance(pin.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256'])
            or not isinstance(pin.get('url'), str)
            or not isinstance(pin.get('files'), dict) or not pin['files']):
        raise NativeRuntimeError('invalid native SDK lock')
    _check_url(pin.get('url', ''))
    for name, record in pin['files'].items():
        _safe_member(name)
        if (not isinstance(record, dict) or not isinstance(record.get('bytes'), int) or not 0 < record['bytes'] <= MAX_ARCHIVE_BYTES
                or not isinstance(record.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])):
            raise NativeRuntimeError('invalid native SDK file pin')
    return pin


def verify_package(path: Path, pin: dict) -> None:
    _no_links(path)
    if path.stat().st_size != pin['bytes'] or sha256(path) != pin['sha256']:
        raise NativeRuntimeError('native SDK package integrity mismatch; original file preserved')


def _safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or '..' in path.parts
            or re.match(r'^[A-Za-z]:', name)):
        raise NativeRuntimeError('unsafe native SDK archive path')


def extract_sdk(package: Path, destination: Path, pin: dict) -> dict[str, Path]:
    verify_package(package, pin)
    _no_links(destination)
    payload = {}
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
            raise NativeRuntimeError('native SDK archive exceeds unpacked limit')
        seen = set()
        for info in infos:
            _safe_member(info.orig_filename)
            _safe_member(info.filename)
            folded = info.filename.casefold()
            mode = info.external_attr >> 16
            if folded in seen or stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise NativeRuntimeError('unsafe native SDK archive entry')
            seen.add(folded)
        for member, expected in pin['files'].items():
            try:
                info = archive.getinfo(member)
            except KeyError as exc:
                raise NativeRuntimeError('native SDK archive is missing pinned payload') from exc
            if info.file_size != expected['bytes']:
                raise NativeRuntimeError('native SDK payload integrity mismatch')
            data = archive.read(member)
            if hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise NativeRuntimeError('native SDK payload integrity mismatch')
            name = PurePosixPath(member).name
            if name.casefold() in {value.casefold() for value in payload}:
                raise NativeRuntimeError('native SDK archive flattened path collision')
            payload[name] = data
    if destination.exists() and any(destination.iterdir()):
        raise NativeRuntimeError('native SDK destination must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in payload.items():
        (destination / name).write_bytes(data)
    return {name: destination / name for name in payload}


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in TRUSTED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise NativeRuntimeError('native SDK download requires the pinned official HTTPS source')


class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_package(pin: dict, target: Path) -> None:
    _check_url(pin['url'])
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_TrustedRedirect())
    for attempt in range(3):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.native-sdk-', delete=False) as stream:
                temporary = Path(stream.name)
                deadline = time.monotonic() + 120
                request = urllib.request.Request(pin['url'], headers={'User-Agent': 'ICODE-native-ui/1.0'})
                with opener.open(request, timeout=30) as response:
                    _check_url(response.url)
                    size = 0
                    for block in iter(lambda: response.read(512 * 1024), b''):
                        size += len(block)
                        if size > pin['bytes']:
                            raise NativeRuntimeError('native SDK download integrity size limit exceeded')
                        if time.monotonic() > deadline:
                            raise TimeoutError('native SDK download deadline exceeded')
                        stream.write(block)
            # Windows cannot rename or unlink the still-open temporary file.
            verify_package(temporary, pin)
            temporary.replace(target)
            return
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 2:
                raise NativeRuntimeError('native SDK download failed: ' + str(exc)) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def obtain_package(pin: dict, path: Path, *, offline: bool) -> Path:
    _no_links(path)
    if path.exists():
        verify_package(path, pin)
    elif offline:
        raise NativeRuntimeError('offline native SDK package missing: ' + str(path))
    else:
        _download_package(pin, path)
    return path


def runtime_key(source: Path, pin: dict, compiler: Path) -> str:
    identity = {'source': sha256(source), 'sdk': pin, 'compiler': sha256(compiler),
                'bootstrap': sha256(Path(__file__))}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def _check_environment() -> Path:
    if sys.platform != 'win32':
        raise NativeRuntimeError('native UI requires Windows; use --browser on other platforms')
    if platform.machine().lower() not in ('amd64', 'x86_64') or struct.calcsize('P') != 8:
        raise NativeRuntimeError('native UI currently requires Windows x64 and 64-bit Python')
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            release = winreg.QueryValueEx(key, 'Release')[0]
    except OSError as exc:
        raise NativeRuntimeError('Windows native UI requires .NET Framework 4.8') from exc
    if release < DOTNET48_RELEASE:
        raise NativeRuntimeError('Windows native UI requires .NET Framework 4.8')
    compiler = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise NativeRuntimeError('Windows native C# compiler is unavailable')
    return compiler


def _check_webview2(loader: Path) -> None:
    # Probe in a short-lived child: keeping an SDK DLL loaded in this process
    # would lock the staging directory on Windows before its atomic rename.
    probe = (
        'import ctypes,sys;from ctypes import wintypes as w;'
        'd=ctypes.WinDLL(sys.argv[1]);f=d.GetAvailableCoreWebView2BrowserVersionString;'
        'f.argtypes=[w.LPCWSTR,ctypes.POINTER(w.LPWSTR)];f.restype=ctypes.c_long;'
        'v=w.LPWSTR();code=f(None,ctypes.byref(v));'
        'print(v.value or "");sys.exit(0 if code==0 and v.value else 1)'
    )
    try:
        result = subprocess.run([sys.executable, '-c', probe, str(loader)], capture_output=True,
                                timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeRuntimeError('WebView2 runtime probe failed: ' + str(exc)) from exc
    if result.returncode:
        raise NativeRuntimeError('Evergreen WebView2 Runtime is unavailable; install the official Microsoft runtime explicitly')


def _validate_bundle(bundle: Path, key: str, pin: dict) -> Path:
    _no_links(bundle)
    try:
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        expected = {PurePosixPath(name).name: record['sha256'] for name, record in pin['files'].items()}
        if (not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict)
                or manifest['identity'] != key or set(manifest['files']) != set(expected) | {'ICODEUI.exe'}):
            raise ValueError('identity mismatch')
        for name, digest in manifest['files'].items():
            path = bundle / name
            _no_links(path)
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or sha256(path) != digest or (name in expected and expected[name] != digest):
                raise ValueError('payload mismatch')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NativeRuntimeError('native UI cache integrity mismatch; original cache preserved') from exc
    _check_webview2(bundle / 'WebView2Loader.dll')
    return bundle / 'ICODEUI.exe'


def ensure_native_runtime(*, cache_root: Path | None = None, package_cache: Path | None = None,
                          offline: bool = False) -> Path:
    compiler = _check_environment()
    pin = load_lock()
    if not HOST_SOURCE.is_file():
        raise NativeRuntimeError('native UI host source missing from the installed UI component')
    root = Path(cache_root or Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'icode/runtime/ui-native').absolute()
    _no_links(root)
    resolved = root.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve() or resolved.is_relative_to(HERE.parents[1]):
        raise NativeRuntimeError('native UI runtime cache must be outside the Agent skill tree')
    key = runtime_key(HOST_SOURCE, pin, compiler)
    bundle = root / key
    if bundle.exists():
        return _validate_bundle(bundle, key, pin)
    root.mkdir(parents=True, exist_ok=True)
    archive = Path(package_cache) if package_cache else root / 'downloads' / ('WebView2-' + pin['version'] + '.nupkg')
    obtain_package(pin, archive, offline=offline)
    with tempfile.TemporaryDirectory(dir=root, prefix='.stage-') as temporary:
        stage = Path(temporary) / 'bundle'
        extract_sdk(archive, stage, pin)
        _check_webview2(stage / 'WebView2Loader.dll')
        exe = stage / 'ICODEUI.exe'
        references = ['System.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll',
                      'System.Web.Extensions.dll', str(stage / 'Microsoft.Web.WebView2.Core.dll'),
                      str(stage / 'Microsoft.Web.WebView2.WinForms.dll')]
        command = [str(compiler), '/nologo', '/target:exe', '/platform:x64',
                   '/langversion:5', '/warnaserror', '/out:' + str(exe)]
        command.extend('/reference:' + name for name in references)
        command.append(str(HOST_SOURCE))
        try:
            result = subprocess.run(command, capture_output=True, timeout=120,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeRuntimeError('native UI host compilation failed: ' + str(exc)) from exc
        if result.returncode or not exe.is_file():
            raise NativeRuntimeError('native UI host compilation failed: ' + (result.stdout + result.stderr).decode('utf-8', errors='replace')[-3000:])
        files = {path.name: sha256(path) for path in stage.iterdir() if path.is_file()}
        (stage / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'identity': key, 'files': files}, indent=2) + '\n', encoding='utf-8')
        # Both directory move endpoints are verified children of this dedicated cache.
        if stage.resolve().parent.parent != resolved or bundle.resolve().parent != resolved:
            raise NativeRuntimeError('native UI staging escaped cache root')
        if bundle.exists():
            return _validate_bundle(bundle, key, pin)
        try:
            stage.replace(bundle)
        except OSError:
            # Windows reports an existing nonempty directory as PermissionError.
            # Reuse a concurrent publication only after validating its identity.
            if bundle.exists():
                return _validate_bundle(bundle, key, pin)
            raise
    return _validate_bundle(bundle, key, pin)
