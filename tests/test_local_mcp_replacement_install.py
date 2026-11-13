"""Replacement installation contracts; all registry paths are isolated fixtures."""
import json
from pathlib import Path
import pytest
from test_mcp_install_reconcile import env, payload, register, prepare

MEMBERS = {"workspace": "icode-workspace", "evidence": "icode-evidence",
           "index": "icode-local-index", "health": "icode-mcp-health", "policy": "icode-mcp-policy"}
DEFAULT = ["icode-local", "icode-device-observe", "icode-mail-observe", "cheap-research",
           "vision-bridge", "context7", "memory", "playwright", "sequential-thinking"]


def catalog(root, names=("icode-local",), members=None):
    for name in [*names, *(members or MEMBERS).values()]:
        payload(root, name)
    data = {"schema_version": 1, "servers": list(names), "replacements": {
        "icode-local": {"members": members or MEMBERS, "retain_payload": True}}}
    (root / "mcp/servers.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def setup_replacement(env, names=("icode-local",)):
    r, source, installed, state = env
    catalog(source, names)
    for name in MEMBERS.values():
        old = payload(installed, name)
        (old / "config.json").write_text('{"allowed_roots":["private"]}', encoding="utf-8")
        for runtime in (".venv", ".cache", "__pycache__"):
            (old / runtime).mkdir()
            (old / runtime / "data").write_text("runtime", encoding="utf-8")
        register(r, name)
    return r, source, installed, state


def test_distribution_catalog_defaults_nine_with_legacy_installers():
    root = Path(__file__).resolve().parents[1]
    import install_reconcile as r
    assert set(r.desired(root)) == set(DEFAULT)
    assert all((root / "mcp" / name / "install.sh").exists() for name in MEMBERS.values())


def test_replacement_single_retires_only_members_keeps_code_config_and_archives_runtime(env):
    r, source, installed, state = setup_replacement(env)
    payload(installed, "unrelated")
    register(r, "unrelated")
    plan = prepare(env, single="icode-local")
    register(r, "icode-local")
    result = r.finish(plan)
    assert result["status"] == "complete", result
    assert result["retired"] == 5
    assert r._actual("claude", "unrelated") is not None
    for name in MEMBERS.values():
        assert r._actual("claude", name) is None
        old = installed / "mcp" / name
        assert (old / "server.py").exists() and (old / "config.json").exists()
        assert not (old / ".venv").exists()
    assert len(list((state / "archives").rglob("config.json"))) == 5
    assert len(list((state / "archives").rglob(".venv/data"))) == 5


def test_catalog_missing_falls_back_to_old_installers(env):
    r, source, _, _ = env
    payload(source, "legacy")
    assert r.desired(source) == ["legacy"]


@pytest.mark.parametrize("change", ["unknown-key", "missing-installer", "duplicates", "generated-conflict", "replacement-active", "unknown-replacement-key"])
def test_catalog_invalid_or_conflicting_fails_closed(env, change):
    r, source, _, state = env
    data = catalog(source)
    if change == "unknown-key": data["unexpected"] = True
    if change == "missing-installer": data["servers"].append("missing")
    if change == "duplicates": data["servers"].append("icode-local")
    if change == "replacement-active": data["servers"].append("icode-workspace")
    if change == "unknown-replacement-key": data["replacements"]["icode-local"]["extra"] = True
    if change == "generated-conflict":
        (source / "mcp" / r.CATALOG).write_text(json.dumps({"version": 1, "desired_names": ["icode-workspace"]}))
    (source / "mcp/servers.json").write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="catalog"):
        prepare(env)
    assert not state.exists()


def test_replacement_verification_gate_no_delete_and_dryrun(env):
    r, source, installed, state = setup_replacement(env)
    preview = prepare(env, single="icode-local", dry_run=True)
    assert set(preview["retired"]["claude"]) == set(MEMBERS.values())
    assert not state.exists()
    plan = prepare(env, single="icode-local")
    with pytest.raises(RuntimeError, match="verification"):
        r.finish(plan)
    assert all(r._actual("claude", name) for name in MEMBERS.values())
    register(r, "icode-local")
    result = r.finish(prepare(env, single="icode-local", no_delete=True))
    assert result["retained"] == 5
    assert all((installed / "mcp" / name / ".venv").exists() for name in MEMBERS.values())


def test_replacement_tampered_registration_is_retained_and_reported(env):
    r, source, installed, _ = setup_replacement(env)
    cfg = r.claude_registry._read_cfg(r.claude_registry.CLAUDE_JSON)
    cfg["mcpServers"]["icode-workspace"]["user_extra"] = True
    r.claude_registry._write_cfg(r.claude_registry.CLAUDE_JSON, cfg)
    plan = prepare(env, single="icode-local")
    register(r, "icode-local")
    result = r.finish(plan)
    assert result["status"] == "conflict"
    assert r._actual("claude", "icode-workspace")["user_extra"] is True
    assert (installed / "mcp/icode-workspace/.venv").exists()


def test_replacement_unselected_host_runtime_survives_until_host_migrates(env):
    r, source, installed, state = setup_replacement(env)
    path = r.client_registry.CODEBUDDY_CFG
    path.parent.mkdir()
    entries = {name: r._export(name) for name in MEMBERS.values()}
    path.write_text(json.dumps({"mcpServers": entries}))
    plan = prepare(env, single="icode-local")
    register(r, "icode-local")
    assert r.finish(plan)["retained"] == 5
    assert set(json.loads(path.read_text())["mcpServers"]) == set(MEMBERS.values())
    assert all((installed / "mcp" / name / ".venv").exists() for name in MEMBERS.values())
    r.client_registry.codebuddy_register("icode-local")
    result = r.finish(r.prepare(source, [installed], "codebuddy", state_root=state, single="icode-local"))
    assert result["status"] == "complete", result
    assert all(not (installed / "mcp" / name / ".venv").exists() for name in MEMBERS.values())


def test_replacement_runtime_move_partial_failure_retries_same_plan(env, monkeypatch):
    r, _, installed, state = setup_replacement(env)
    plan = prepare(env, single="icode-local")
    register(r, "icode-local")
    move = r.shutil.move
    counter = [0]
    def failing_move(src, dst):
        counter[0] += 1
        if counter[0] == 2: raise OSError("injected")
        return move(src, dst)
    monkeypatch.setattr(r.shutil, "move", failing_move)
    assert r.finish(plan)["status"] == "conflict"
    monkeypatch.setattr(r.shutil, "move", move)
    result = r.finish(plan)
    assert result["status"] == "complete", result
    assert all((installed / "mcp" / name / "server.py").exists() for name in MEMBERS.values())
    assert all(not (installed / "mcp" / name / ".venv").exists() for name in MEMBERS.values())

# Real public installer coverage uses an offline copy, never actual user hosts.
from test_install_reconciliation_entrypoints import deployment, run_install, first_install, servers, put


def add_local_release(deployment):
    source, home, environment = deployment
    original = source / 'mcp/fixture-current'
    put(source / 'mcp/icode-local/install.sh', (original / 'install.sh').read_text())
    put(source / 'mcp/icode-local/server.py', '# offline replacement\n')
    put(source / 'mcp/icode-local/profile.py', '''import os,sys
from pathlib import Path
assert sys.argv[1] == 'preflight'
assert '--hosts' in sys.argv
if os.environ.get('FIXTURE_PROFILE_FAIL'): sys.exit(8)
''')
    data = {'schema_version': 1, 'servers': ['fixture-current', 'icode-local'],
            'replacements': {'icode-local': {'members': {'workspace': 'fixture-old'}, 'retain_payload': True}}}
    put(source / 'mcp/servers.json', json.dumps(data))
    return source, home, environment


def test_public_replacement_install_updates_code_preserves_config_and_runtime_snapshot(deployment):
    first_install(deployment)
    source, home, environment = add_local_release(deployment)
    put(source / 'mcp/fixture-old/server.py', '# new business implementation\n')
    old = home / '.claude/skills/icode/mcp/fixture-old'
    put(old / '.venv/data', 'runtime')
    result = run_install(deployment, 'icode-local')
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(servers(home)) == {'fixture-current', 'icode-local'}
    assert (old / 'server.py').read_text() == '# new business implementation\n'
    assert json.loads((old / 'config.json').read_text()) == {'local_data': 'fixture-old'}
    assert not (old / '.venv').exists()
    assert len(list((home / '.local/state/icode/mcp-install/archives').rglob('.venv/data'))) == 1
    assert environment['FIXTURE_INSTALL_LOG']


def test_profile_preflight_failure_precedes_public_sync(deployment):
    first_install(deployment)
    source, home, environment = add_local_release(deployment)
    before = (home / '.claude/skills/icode/mcp/fixture-current/server.py').read_bytes()
    put(source / 'mcp/fixture-current/server.py', '# changed but should not sync\n')
    environment['FIXTURE_PROFILE_FAIL'] = '1'
    result = run_install(deployment, 'icode-local')
    assert result.returncode != 0
    assert (home / '.claude/skills/icode/mcp/fixture-current/server.py').read_bytes() == before
    assert 'fixture-old' in servers(home)


def test_replacement_without_ownership_export_is_reported_and_kept(env):
    r, source, installed, _ = setup_replacement(env)
    (r.claude_registry.ENTRY_DIR / 'icode-workspace.json').unlink()
    plan = prepare(env, single='icode-local')
    register(r, 'icode-local')
    result = r.finish(plan)
    assert result['status'] == 'conflict'
    assert any(item.get('name') == 'icode-workspace' for item in result['conflicts'])
    assert r._actual('claude', 'icode-workspace') is not None


def test_replacement_source_or_stage_drift_never_overwrites_old_payload(env):
    r, source, installed, state = setup_replacement(env)
    old = installed / 'mcp/icode-workspace'
    (old / 'extension.py').write_text('private extension')
    plan = prepare(env, single='icode-local')
    stage = installed.parent / '.icode-stage-test'
    payload(stage, 'icode-workspace')
    (stage / 'mcp/icode-workspace/server.py').write_text('tampered stage')
    with pytest.raises(RuntimeError, match='conflict'):
        r.preserve(plan, stage)
    assert (old / 'extension.py').read_text() == 'private extension'
    (source / 'mcp/icode-workspace/server.py').write_text('changed after plan')
    with pytest.raises(RuntimeError, match='source changed'):
        r.preserve(plan, stage)
    assert r._actual('claude', 'icode-workspace') is not None


def test_replacement_preserves_extensions_and_private_original_snapshot(env):
    r, source, installed, state = setup_replacement(env)
    old = installed / 'mcp/icode-workspace'
    (old / 'extension.py').write_text('private extension')
    (source / 'mcp/icode-workspace/server.py').write_text('trusted new code')
    original = (old / 'server.py').read_text()
    plan = prepare(env, single='icode-local')
    stage = installed.parent / '.icode-stage-expected'
    stage.mkdir()
    r.preserve(plan, stage)
    assert (stage / 'mcp/icode-workspace/server.py').read_text() == 'trusted new code'
    assert (stage / 'mcp/icode-workspace/extension.py').read_text() == 'private extension'
    assert (stage / 'mcp/icode-workspace/config.json').read_bytes() == (old / 'config.json').read_bytes()
    snapshots = list((state / 'archives').rglob('icode-workspace/payload/server.py'))
    assert len(snapshots) == 1 and snapshots[0].read_text() == original
    if __import__('os').name != 'nt':
        assert snapshots[0].stat().st_mode & 0o077 == 0
        assert (old / 'config.json').stat().st_mode & 0o777 == 0o644


def test_catalog_duplicate_json_keys_are_rejected(env):
    r, source, _, state = env
    catalog(source)
    path = source / 'mcp/servers.json'
    path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 2, "schema_version": 1'))
    with pytest.raises(RuntimeError, match='catalog'):
        prepare(env)
    assert not state.exists()


def test_snapshot_race_is_rejected_before_installer_can_publish(env, monkeypatch):
    r, _, installed, _ = setup_replacement(env)
    original = r._merge_payload
    def race(source, target):
        original(source, target)
        (target / 'server.py').write_text('raced snapshot')
    monkeypatch.setattr(r, '_merge_payload', race)
    with pytest.raises(RuntimeError, match='snapshot'):
        prepare(env, single='icode-local')
    assert r._actual('claude', 'icode-workspace') is not None
    assert (installed / 'mcp/icode-workspace/.venv').exists()


@pytest.mark.parametrize('legacy', [False, True])
def test_uninstall_defaults_catalog_and_allows_explicit_legacy(tmp_path, legacy):
    import os
    import shutil
    import subprocess
    import sys
    from test_install_reconciliation_entrypoints import bash_binary
    root = Path(__file__).resolve().parents[1]
    source, home = tmp_path / 'release', tmp_path / 'home'
    home.mkdir()
    data = catalog(source, DEFAULT)
    shutil.copytree(root / 'mcp/_lib', source / 'mcp/_lib', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(root / 'mcp/uninstall.sh', source / 'mcp/uninstall.sh')
    for name in [*DEFAULT, *MEMBERS.values()]:
        put(source / 'mcp' / name / 'uninstall.sh',
            '#!/usr/bin/env bash\nprintf "%s\\n" "$(basename "$(dirname "$0")")" >> "$FIXTURE_UNINSTALL_LOG"\n')
    if legacy:
        (source / 'mcp/servers.json').unlink()
    log = tmp_path / 'removed.log'
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       ICODE_PYTHON_BIN=sys.executable, FIXTURE_UNINSTALL_LOG=str(log))
    command = [bash_binary(), str(source / 'mcp/uninstall.sh')]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(log.read_text().splitlines()) == set(DEFAULT) | (set(MEMBERS.values()) if legacy else set())
    log.write_text('')
    result = subprocess.run([*command, 'icode-workspace'], env=environment, capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ['icode-workspace']


def test_stale_or_tampered_plan_cannot_override_current_catalog(env):
    r, source, _, _ = setup_replacement(env)
    plan = prepare(env, single='icode-local')
    data = json.loads(plan.read_text())
    data['distribution_names'].append('icode-workspace')
    plan.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='catalog'):
        r._load_plan(plan)


@pytest.mark.parametrize('dependency', ['index-data', 'module-config', 'policy-file', 'profile-file'])
def test_finish_never_archives_active_runtime_business_dependencies(env, dependency):
    r, source, installed, state = setup_replacement(env)
    old_index = installed / 'mcp/icode-local-index'
    business = old_index / '.cache/actual-index-data/example.sqlite3'
    business.parent.mkdir(parents=True)
    business.write_bytes(b'preserved business database')
    outside_config = installed.parent / 'active-module.json'
    profile = installed / 'mcp/icode-local/config.json'
    profile.parent.mkdir(parents=True)
    mapping = {'index': str(old_index / 'config.json')}
    if dependency == 'index-data':
        # This is the original effective config, not a test-only moved DB.
        (old_index / 'config.json').write_text(json.dumps({'data_dir': str(business.parent)}))
    profile.write_text(json.dumps({'module_configs': mapping}))
    plan = prepare(env, single='icode-local')
    if dependency == 'module-config':
        # A config switch after prepare changes no hashed old business source.
        hidden_config = installed / 'mcp/icode-evidence/.cache/active-index-config.json'
        hidden_config.write_text(json.dumps({'data_dir': str(business.parent)}))
        mapping['index'] = str(hidden_config)
        profile.write_text(json.dumps({'module_configs': mapping}))
    elif dependency == 'policy-file':
        active_policy = installed / 'mcp/icode-workspace/.cache/private-policy.json'
        active_policy.write_text('{"default":"deny"}')
        outside_config.write_text(json.dumps({'policy_file': str(active_policy)}))
        mapping['policy'] = str(outside_config)
        profile.write_text(json.dumps({'module_configs': mapping}))
    elif dependency == 'profile-file':
        hidden_profile = old_index / '.cache/active-profile.json'
        hidden_profile.write_bytes(profile.read_bytes())
        profile = hidden_profile
    register(r, 'icode-local', env={'ICODE_LOCAL_CONFIG': str(profile)})
    with pytest.raises(RuntimeError, match='runtime dependency'):
        r.finish(plan)
    assert business.read_bytes() == b'preserved business database'
    assert all((installed / 'mcp' / name / '.cache').is_dir() for name in MEMBERS.values())
    assert all(r._actual('claude', name) is not None for name in MEMBERS.values())
    assert not list((state / 'archives').rglob('example.sqlite3'))


def test_runtime_archive_rechecks_profile_after_each_move(env, monkeypatch):
    r, _, installed, _ = setup_replacement(env)
    evidence = installed / 'mcp/icode-evidence'
    databases = []
    for runtime in ('.cache', '.venv'):
        database = evidence / runtime / 'business-index/example.sqlite3'
        database.parent.mkdir()
        database.write_bytes(b'existing business index')
        databases.append(database)
    external = installed.parent / 'external-index-config.json'
    external.write_text('{}')
    profile = installed / 'mcp/icode-local/config.json'
    profile.parent.mkdir(parents=True)
    profile.write_text('{"module_configs":{}}')
    plan = prepare(env, single='icode-local')
    register(r, 'icode-local', env={'ICODE_LOCAL_CONFIG': str(profile)})
    move = r.shutil.move
    moved, active_database = [], []
    def switch_after_first_move(source, target):
        result = move(source, target)
        moved.append(Path(source))
        if len(moved) == 1:
            # Choose an existing DB in another runtime of the same member.
            # Only profile/config references change; no business data is moved.
            active_database.append(next(path for path in databases if path.exists()))
            external.write_text(json.dumps({'data_dir': str(active_database[0].parent)}))
            profile.write_text(json.dumps({'module_configs': {'index': str(external)}}))
        return result
    monkeypatch.setattr(r.shutil, 'move', switch_after_first_move)
    result = r.finish(plan)
    assert result['status'] == 'conflict'
    assert len(moved) == 1, 'the next runtime must remain after an active dependency appears'
    assert active_database[0].read_bytes() == b'existing business index'
    assert json.loads(external.read_text())['data_dir'] == str(active_database[0].parent)
    assert r._actual('claude', 'icode-local') is not None


@pytest.mark.parametrize('phase', ['prepare', 'preserve'])
def test_publication_refuses_changed_active_config_example(env, phase):
    r, source, installed, state = setup_replacement(env)
    old = installed / 'mcp/icode-workspace'
    active = old / 'config.example.json'
    restricted = {'allowed_roots': [str(installed.parent / 'restricted')]}
    active.write_text(json.dumps(restricted))
    (source / 'mcp/icode-workspace/config.example.json').write_text('{"allowed_roots":null}')
    if phase == 'prepare':
        register(r, 'icode-workspace', args=[str(old / 'server.py')],
                 env={'ICODE_WORKSPACE_CONFIG': str(active)})
        with pytest.raises(RuntimeError, match='active payload dependency'):
            prepare(env, single='icode-local')
        assert not state.exists(), 'unsafe publication must fail before plan/snapshot writes'
    else:
        plan = prepare(env, single='icode-local')
        profile = installed / 'mcp/icode-local/config.json'
        profile.parent.mkdir(parents=True)
        profile.write_text(json.dumps({'module_configs': {'workspace': str(active)}}))
        stage = installed.parent / '.icode-stage-config-alias'
        stage.mkdir()
        with pytest.raises(RuntimeError, match='active payload dependency'):
            r.preserve(plan, stage)
        assert not (stage / 'mcp/icode-workspace/config.example.json').exists()
    assert json.loads(active.read_text()) == restricted
    assert r._actual('claude', 'icode-workspace') is not None


@pytest.mark.parametrize('permission_changed', [False, True])
def test_payload_dependency_policy_allows_only_transport_metadata(env, permission_changed):
    r, source, installed, _ = setup_replacement(env)
    policy = installed / 'mcp/icode-mcp-policy/policy.json'
    policy.write_text(json.dumps({'default': 'deny', 'servers': {'old': {'tools': ['read']}}}))
    proposed = {'default': 'deny', 'servers': {'old': {'tools': ['read']}}, 'transport': {'server': 'icode-local'}}
    if permission_changed:
        proposed['servers']['old']['tools'].append('write')
    (source / 'mcp/icode-mcp-policy/policy.json').write_text(json.dumps(proposed))
    directories = [installed / 'mcp' / name for name in MEMBERS.values()]
    if permission_changed:
        with pytest.raises(RuntimeError, match='active payload dependency'):
            r.check_payload_dependencies(source / 'mcp', directories)
    else:
        r.check_payload_dependencies(source / 'mcp', directories)
    assert json.loads(policy.read_text()) == {'default': 'deny', 'servers': {'old': {'tools': ['read']}}}


def test_fresh_catalog_prepare_does_not_inspect_unselected_corrupt_host(env):
    r, source, installed, state = env
    catalog(source)
    r.claude_registry.CLAUDE_JSON.write_text('unselected corrupt host')
    plan = r.prepare(source, [installed], 'codebuddy', state_root=state)
    assert r._load_plan(plan)['hosts'] == ['codebuddy']
    assert r.claude_registry.CLAUDE_JSON.read_text() == 'unselected corrupt host'


def test_public_root_upgrade_keeps_active_config_example_and_runtime(deployment):
    source, home, _ = deployment
    installer = source / 'mcp/fixture-old/install.sh'
    original = "claude_registry.register(root.name,{'command':sys.executable,'args':[str(root/'server.py')]})"
    replacement = "entry={'command':sys.executable,'args':[str(root/'server.py')]}\nif root.name=='fixture-old': entry['env']={'ICODE_WORKSPACE_CONFIG':str(root/'config.example.json')}\nclaude_registry.register(root.name,entry)"
    assert original in installer.read_text()
    put(installer, installer.read_text().replace(original, replacement))
    restricted = {'allowed_roots': [str(home / 'restricted')]}
    put(source / 'mcp/fixture-old/config.example.json', json.dumps(restricted))
    first_install(deployment)
    _, _, _ = add_local_release(deployment)
    old = home / '.claude/skills/icode/mcp/fixture-old'
    original_config = (old / 'config.json').read_bytes()
    put(old / '.venv/marker', 'preserved runtime')
    put(source / 'mcp/fixture-old/config.example.json', '{"allowed_roots":null}')
    result = run_install(deployment, 'icode-local')
    assert result.returncode != 0
    assert 'active payload dependency' in result.stderr
    assert json.loads((old / 'config.example.json').read_text()) == restricted
    assert (old / 'config.json').read_bytes() == original_config
    assert (old / '.venv/marker').read_text() == 'preserved runtime'
    assert set(servers(home)) == {'fixture-current', 'fixture-old'}


@pytest.mark.parametrize('private_name', ['.env', 'ignored/private.json'])
def test_replacement_publishes_only_business_files_and_keeps_old_private_extensions(env, private_name):
    r, source, installed, _ = setup_replacement(env)
    published = source / 'mcp/icode-workspace'
    old = installed / 'mcp/icode-workspace'
    private = published / private_name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(b'source-private-fixture')
    (old / 'retained-private.json').write_bytes(b'original-local-fixture')
    (published / 'retained-private.json').write_bytes(b'other-source-private-fixture')
    (source / '.gitignore').write_text('mcp/*/.env\nmcp/*/ignored/\nmcp/*/retained-private.json\n')
    plan = prepare(env, single='icode-local')
    stage = installed.parent / '.icode-stage-private-source'
    stage.mkdir()
    r.preserve(plan, stage)
    target = stage / 'mcp/icode-workspace'
    assert not (target / private_name).exists()
    assert (target / 'retained-private.json').read_bytes() == b'original-local-fixture'
    assert (old / 'retained-private.json').read_bytes() == b'original-local-fixture'
    assert (target / 'server.py').read_bytes() == (published / 'server.py').read_bytes()


def test_private_source_files_are_not_read_during_replacement_publication(env, monkeypatch):
    r, source, installed, _ = setup_replacement(env)
    published = source / 'mcp/icode-workspace'
    private = published / '.env'
    private.write_bytes(b'private-source-fixture')
    original = Path.read_bytes
    def guard(path):
        assert path != private, 'source private files must not be scanned'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guard)
    plan = prepare(env, single='icode-local')
    stage = installed.parent / '.icode-stage-no-private-read'
    stage.mkdir()
    r.preserve(plan, stage)
    assert not (stage / 'mcp/icode-workspace/.env').exists()


@pytest.mark.skipif(__import__('os').name != 'posix', reason='real rsync installer fixture requires Linux/WSL')
def test_direct_local_rsync_keeps_profile_referenced_custom_config(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    if not shutil.which('rsync'):
        pytest.skip('real rsync is unavailable')
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / 'home'
    home.mkdir()
    registry = home / '.claude.json'
    registry.write_text('{"mcpServers":{}}')
    target_base = home / 'installed'
    target = target_base / 'icode-local'
    interpreter = target / '.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    put(interpreter.parent / 'pip', '#!/bin/sh\nexit 0\n')
    custom = target / 'custom-workspace.json'
    custom.write_text(json.dumps({'allowed_roots': [str(home / 'restricted')]}))
    before = custom.read_bytes()
    profile = target / 'config.json'
    profile.write_text(json.dumps({'module_configs': {'workspace': str(custom)}}))
    before_profile = profile.read_bytes()
    environment = {**os.environ, 'HOME': str(home), 'USERPROFILE': str(home),
                   'ICODE_MCP_TARGET_BASE': str(target_base), 'ICODE_MCP_SELECTED_HOSTS': 'claude'}
    result = subprocess.run(['bash', str(root / 'mcp/icode-local/install.sh')],
                            env=environment, capture_output=True, text=True, timeout=40)
    assert custom.exists(), result.stdout + result.stderr
    assert custom.read_bytes() == before
    assert result.returncode == 0, result.stdout + result.stderr
    assert profile.read_bytes() == before_profile
    assert 'icode-local' in json.loads(registry.read_text())['mcpServers']
