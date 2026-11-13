"""Prepare independent module config references before changing registrations.

Only selected host entries are read. No configuration values or credentials are
printed; conflicting roots must be migrated one host at a time.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

LIB_DIR = Path(__file__).resolve().parent.parent / '_lib'
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
from install_reconcile import (HOSTS, _actual, _read, _safe, _write,
                               check_payload_dependencies, check_runtime_dependencies)

MEMBERS = {
    'workspace': ('icode-workspace', 'ICODE_WORKSPACE_CONFIG'),
    'evidence': ('icode-evidence', 'ICODE_EVIDENCE_CONFIG'),
    'index': ('icode-local-index', 'ICODE_LOCAL_INDEX_CONFIG'),
    'health': ('icode-mcp-health', 'ICODE_MCP_HEALTH_CONFIG'),
    'policy': ('icode-mcp-policy', 'ICODE_MCP_POLICY_CONFIG'),
}


def _path(value: str, *, base: Path | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError('configuration path is unavailable')
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        if base is None or not base.is_absolute():
            raise RuntimeError('relative configuration path has no absolute cwd')
        path = base / path
    return _safe(path).resolve()


def _validated_config(path: Path) -> Path:
    _read(path, 'module configuration')
    return path


def _entry_source_dir(node: dict) -> Path:
    transport = node.get('transport', node)
    if not isinstance(transport, dict):
        raise RuntimeError('invalid host transport')
    cwd = transport.get('cwd', node.get('cwd'))
    base = _path(cwd) if cwd is not None else None
    args = transport.get('args', [])
    if not isinstance(args, list):
        raise RuntimeError('invalid host arguments')
    scripts = [_path(value, base=base) for value in args
               if isinstance(value, str) and Path(value).name == 'server.py']
    if len(scripts) != 1:
        raise RuntimeError('entry has no unambiguous Python server path')
    return scripts[0].parent


def _entry_config(node: dict, env_name: str) -> Path | None:
    transport = node.get('transport', node)
    if not isinstance(transport, dict):
        raise RuntimeError('invalid host transport')
    env = transport.get('env') or {}
    if not isinstance(env, dict):
        raise RuntimeError('invalid host environment')
    cwd = transport.get('cwd', node.get('cwd'))
    base = _path(cwd) if cwd is not None else None
    if env_name in env:
        return _validated_config(_path(env[env_name], base=base))
    args = transport.get('args', [])
    if not isinstance(args, list):
        raise RuntimeError('invalid host arguments')
    scripts = [_path(value, base=base) for value in args
               if isinstance(value, str) and Path(value).name == 'server.py']
    if len(scripts) != 1:
        raise RuntimeError('legacy entry has no unambiguous Python server path')
    default = scripts[0].parent / 'config.json'
    return _validated_config(default) if default.exists() else None


def _profile_mapping(path: Path) -> dict[str, str]:
    data = _read(path, 'local profile')
    mapping = data.get('module_configs')
    if not isinstance(mapping, dict) or set(mapping) - set(MEMBERS):
        raise RuntimeError('invalid local module_configs')
    return {module: str(_validated_config(_path(value, base=path.parent)))
            for module, value in mapping.items()}


def _check_config_paths(module: str, path: Path, *, original_dir: Path | None = None,
                        target: Path | None = None, module_dirs: list[Path] | None = None) -> None:
    directories = list(module_dirs or [])
    if original_dir is not None:
        directories.append(original_dir)
    if target is not None:
        directories.extend([target, *(target.parent / name for name, _ in MEMBERS.values())])
    check_runtime_dependencies(None, directories, module_configs={module: path})
    config = _read(path, 'module configuration')
    for field in ('allowed_roots', 'trusted_server_roots', 'data_dir'):
        values = config.get(field)
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if not isinstance(value, str) or not Path(os.path.expandvars(os.path.expanduser(value))).is_absolute():
                raise RuntimeError(f'relative module path changes base: {module}.{field}')
    if module == 'policy' and 'policy_file' in config:
        value = config['policy_file']
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError('invalid policy_file')
        expanded = Path(os.path.expandvars(os.path.expanduser(value)))
        if not expanded.is_absolute() and expanded != Path('policy.json'):
            # The distributed default is separately checked for equal permission
            # data. Arbitrary relative dependencies cannot be relocated safely.
            if original_dir is None or target is None or original_dir != target.parent / 'icode-mcp-policy':
                raise RuntimeError('relative policy dependency changes source directory')


def _check_policy_dependency(node: dict, config_path: Path | None) -> None:
    transport = node.get('transport', node)
    cwd = transport.get('cwd', node.get('cwd'))
    base = _path(cwd) if cwd else None
    scripts = [_path(value, base=base) for value in transport.get('args', [])
               if isinstance(value, str) and Path(value).name == 'server.py']
    if len(scripts) != 1:
        raise RuntimeError('policy entry has no unambiguous source directory')
    original_dir = scripts[0].parent
    config = _read(config_path, 'policy configuration') if config_path else {}
    policy = _path(config.get('policy_file', str(original_dir / 'policy.json')), base=original_dir)
    data = _read(policy, 'policy dependency')
    if policy.is_relative_to(original_dir):
        distributed = _read(Path(__file__).resolve().parent.parent / 'icode-mcp-policy' / 'policy.json',
                            'distributed policy')
        # Transport metadata changes tool names only. Permission data must stay
        # identical before replacing the tracked policy payload in place.
        original_permissions = {key: value for key, value in data.items() if key != 'transport'}
        next_permissions = {key: value for key, value in distributed.items() if key != 'transport'}
        if original_permissions != next_permissions:
            raise RuntimeError('policy dependency conflict; preserve it outside the replaced payload first')


def prepare_profile(target: Path, hosts: list[str]) -> dict:
    target = _safe(target)
    if not hosts or len(hosts) != len(set(hosts)) or any(host not in HOSTS for host in hosts):
        raise RuntimeError('selected hosts must be a nonempty unique host list')
    names = ['icode-local', *(name for name, _ in MEMBERS.values())]
    nodes = {(host, name): _actual(host, name) for host in hosts for name in names}
    module_dirs = [target, *(target.parent / name for name, _ in MEMBERS.values())]
    for (_, name), node in nodes.items():
        if node is not None:
            directory = _entry_source_dir(node)
            module_dirs.append(directory)
            if name == 'icode-local':
                module_dirs.extend(directory.parent / member for member, _ in MEMBERS.values())
    mapping: dict[str, str] = {}
    profile_paths: set[Path] = set()
    existing = target / 'config.json'
    if existing.exists():
        profile_paths.add(existing)
        check_runtime_dependencies(existing, module_dirs)
        mapping.update(_profile_mapping(existing))
        for module, value in mapping.items():
            _check_config_paths(module, Path(value),
                                original_dir=target.parent / MEMBERS[module][0], target=target,
                                module_dirs=module_dirs)

    def merge(module: str, value: str, host: str) -> None:
        if module in mapping and mapping[module] != value:
            raise RuntimeError(f'configuration conflict for {module} ({host}); migrate hosts separately')
        mapping[module] = value

    for host in hosts:
        # A rerun can have an already unified host while another host still uses
        # legacy entries. Compare its independent paths as well.
        unified = nodes[(host, 'icode-local')]
        if unified is not None:
            transport = unified.get('transport', unified)
            env = transport.get('env') or {}
            if 'ICODE_LOCAL_CONFIG' not in env:
                raise RuntimeError('existing local entry has no explicit profile path')
            cwd = transport.get('cwd', unified.get('cwd'))
            base = _path(cwd) if cwd else None
            scripts = [_path(value, base=base) for value in transport.get('args', [])
                       if isinstance(value, str) and Path(value).name == 'server.py']
            if len(scripts) != 1:
                raise RuntimeError('existing local entry has no unambiguous source directory')
            policy_dir = scripts[0].parent.parent / 'icode-mcp-policy'
            unified_profile = _path(env['ICODE_LOCAL_CONFIG'], base=base)
            profile_paths.add(unified_profile)
            check_runtime_dependencies(unified_profile, module_dirs)
            unified_mapping = _profile_mapping(unified_profile)
            if (policy_dir / 'policy.json').exists():
                _check_policy_dependency({'args': [str(policy_dir / 'server.py')]},
                                         Path(unified_mapping['policy']) if 'policy' in unified_mapping else None)
            for module, value in unified_mapping.items():
                _check_config_paths(module, Path(value), original_dir=policy_dir, target=target,
                                    module_dirs=module_dirs)
                merge(module, value, host)
        for module, (name, env_name) in MEMBERS.items():
            node = nodes[(host, name)]
            if node is not None:
                path = _entry_config(node, env_name)
                if path is not None:
                    transport = node.get('transport', node)
                    cwd = transport.get('cwd', node.get('cwd'))
                    base = _path(cwd) if cwd else None
                    scripts = [_path(value, base=base) for value in transport.get('args', [])
                               if isinstance(value, str) and Path(value).name == 'server.py']
                    _check_config_paths(module, path,
                                        original_dir=scripts[0].parent if len(scripts) == 1 else None,
                                        target=target, module_dirs=module_dirs)
                    merge(module, str(path), host)
                if module == 'policy':
                    _check_policy_dependency(node, path)
    retained_policy = target.parent / 'icode-mcp-policy'
    if (retained_policy / 'policy.json').exists():
        # Subsequent upgrades no longer have legacy registrations to inspect.
        # The retained default policy still owns permissions and must be checked.
        _check_policy_dependency({'args': [str(retained_policy / 'server.py')]},
                                 Path(mapping['policy']) if 'policy' in mapping else None)
    check_runtime_dependencies(None, module_dirs, module_configs=mapping)
    publication_dirs = [target, *(target.parent / name for name, _ in MEMBERS.values())]
    source_mcp = Path(__file__).resolve().parent.parent
    check_payload_dependencies(source_mcp, publication_dirs, module_configs=mapping)
    for profile_path in profile_paths:
        check_payload_dependencies(source_mcp, publication_dirs, profile_path=profile_path,
                                   module_configs=mapping)
    return {'module_configs': mapping}


def setup_profile(target: Path, hosts: list[str]) -> dict:
    proposed = prepare_profile(target, hosts)
    destination = _safe(target) / 'config.json'
    if not destination.exists() or _read(destination, 'local profile') != proposed:
        _write(destination, proposed)
    return proposed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'setup-profile'])
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--hosts', required=True, help='comma separated frozen host IDs')
    args = parser.parse_args()
    try:
        function = prepare_profile if args.action == 'preflight' else setup_profile
        proposed = function(args.target, args.hosts.split(','))
    except (OSError, RuntimeError, ValueError, TypeError):
        # Parsing errors and source entry data can contain credentials.
        print('Local configuration preflight failed: invalid or conflicting module paths; migrate selected hosts separately.', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'ok', 'action': args.action,
                      'modules': sorted(proposed['module_configs'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
