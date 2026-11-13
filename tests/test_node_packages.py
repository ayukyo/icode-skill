"""Managed Node packages: immutable installs, lock validation and fail-closed reuse."""
import base64
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


MODULE = Path(__file__).resolve().parents[1] / "mcp/_lib/node_packages.py"
REAL_RUN = subprocess.run


def loader():
    assert MODULE.is_file(), "managed Node package loader is missing"
    spec = importlib.util.spec_from_file_location("node_packages", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    package = "@example/server"
    write_json(catalog / "catalog.json", {"schema_version": 1, "servers": {
        "memory": {"package": package, "version": "1.0.0", "bin": "dist/index.js"}}})
    manifest = {"name": "icode-node-memory", "version": "1.0.0", "private": True,
                "dependencies": {package: "1.0.0"}}
    lock = {"name": manifest["name"], "version": "1.0.0", "lockfileVersion": 3,
            "requires": True, "packages": {"": manifest,
                "node_modules/" + package: {"version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/@example/server/-/server-1.0.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(bytes(64)).decode(),
                    "dependencies": {"helper": "^2.0.0"}},
                "node_modules/helper": {"version": "2.0.0",
                    "resolved": "https://registry.npmjs.org/helper/-/helper-2.0.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(bytes(64)).decode()}}}
    write_json(catalog / "memory/package.json", manifest)
    write_json(catalog / "memory/package-lock.json", lock)
    node = tmp_path / "node/node.exe"
    node.parent.mkdir()
    node.write_bytes(b"fake Node executable")
    npm = node.parent / "node_modules/npm/bin/npm-cli.js"
    npm.parent.mkdir(parents=True)
    npm.write_text("// fake npm", encoding="utf-8")
    calls = []

    def install(command, **kwargs):
        calls.append((command, kwargs))
        root = Path(kwargs["cwd"])
        write_json(root / "node_modules/@example/server/package.json", {
            "name": package, "version": "1.0.0", "bin": {"example": "dist/index.js"}})
        entry = root / "node_modules/@example/server/dist/index.js"
        entry.parent.mkdir()
        entry.write_text("console.log('package entry')", encoding="utf-8")
        write_json(root / "node_modules/helper/package.json", {"name": "helper", "version": "2.0.0"})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", install)
    return catalog, node, tmp_path / "runtimes", lock, calls, install


def test_load_spec_accepts_exact_public_lock(fixture):
    catalog, _, _, _, _, _ = fixture
    spec = loader().load_spec("memory", catalog)
    assert spec["package"] == "@example/server"
    assert spec["version"] == "1.0.0"
    assert len(spec["lock_sha256"]) == 64


def test_install_is_direct_private_and_idempotent(fixture):
    catalog, node, root, _, calls, _ = fixture
    module = loader()
    metadata = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert metadata == module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [str(node.resolve()), str(module.resolve_npm_cli(node)), "ci",
                       "--ignore-scripts", "--omit=dev", "--bin-links=false", "--no-audit", "--no-fund"]
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs.get("shell", False) is False
    assert Path(metadata["entry"]).read_text() == "console.log('package entry')"
    assert metadata["node"] == str(node.resolve())
    assert module.verify_runtime("memory", metadata["root"], catalog_root=catalog) == metadata


@pytest.mark.parametrize("damage", ["tamper", "extra", "missing", "unmarked", "receipt", "symlink"])
def test_reuse_rejects_damage_without_overwriting(fixture, damage):
    catalog, node, root, _, calls, _ = fixture
    module = loader()
    metadata = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    runtime = Path(metadata["root"])
    entry = Path(metadata["entry"])
    if damage == "tamper":
        entry.write_text("tampered", encoding="utf-8")
    elif damage == "extra":
        (runtime / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    elif damage == "missing":
        entry.unlink()
    elif damage == "unmarked":
        (runtime / ".icode-managed-node.json").unlink()
    elif damage == "receipt":
        (runtime / ".icode-node-receipt.json").write_text("{}", encoding="utf-8")
    elif damage == "symlink":
        outside = root.parent / "outside.js"
        outside.write_text("outside", encoding="utf-8")
        entry.unlink()
        try:
            entry.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable on this Windows host")
    with pytest.raises(ValueError):
        module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert len(calls) == 1
    assert runtime.is_dir()


@pytest.mark.parametrize("damage", ["credential", "http", "git", "query", "integrity", "missing_dependency", "link", "traversal", "range"])
def test_load_spec_rejects_unsafe_or_incomplete_locks(fixture, damage):
    catalog, _, _, lock, _, _ = fixture
    entry = lock["packages"]["node_modules/@example/server"]
    if damage == "credential":
        entry["resolved"] = "https://secret@registry.npmjs.org/package.tgz"
    elif damage == "http":
        entry["resolved"] = "http://registry.npmjs.org/package.tgz"
    elif damage == "git":
        entry["resolved"] = "git+https://example.com/repo"
    elif damage == "query":
        entry["resolved"] += "?token=secret"
    elif damage == "integrity":
        entry["integrity"] = "sha512-incomplete"
    elif damage == "missing_dependency":
        del lock["packages"]["node_modules/helper"]
    elif damage == "link":
        entry["link"] = True
    elif damage == "traversal":
        lock["packages"]["../escape"] = lock["packages"].pop("node_modules/helper")
    elif damage == "range":
        lock["packages"][""]["dependencies"]["@example/server"] = "^1.0.0"
    write_json(catalog / "memory/package-lock.json", lock)
    with pytest.raises(ValueError):
        loader().load_spec("memory", catalog)


def test_failed_install_does_not_publish_or_change_prior_runtime(fixture, monkeypatch):
    catalog, node, root, _, calls, install = fixture
    module = loader()
    old = root / "old-version"
    old.mkdir(parents=True)
    (old / "keep").write_text("old runtime", encoding="utf-8")

    def failure(command, **kwargs):
        install(command, **kwargs)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", failure)
    with pytest.raises(RuntimeError, match="7") as error:
        module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert "secret" not in str(error.value)
    assert (old / "keep").read_text() == "old runtime"
    assert not list(root.glob("memory-*"))


def test_concurrent_install_publishes_one_valid_runtime(fixture):
    catalog, node, root, _, _, _ = fixture
    module = loader()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: module.ensure_runtime(
            "memory", node, runtime_root=root, catalog_root=catalog), range(4)))
    assert all(result == results[0] for result in results)
    assert module.verify_runtime("memory", results[0]["root"], catalog_root=catalog) == results[0]


def test_catalog_has_four_pinned_packages():
    module = loader()
    expected = {"memory": ("@modelcontextprotocol/server-memory", "1.0.0"),
                "sequential-thinking": ("@modelcontextprotocol/server-sequential-thinking", "1.0.0"),
                "context7": ("@upstash/context7-mcp", "4.7.0"),
                "playwright": ("@playwright/mcp", "0.0.85")}
    for server, pair in expected.items():
        spec = module.load_spec(server)
        assert (spec["package"], spec["version"]) == pair


@pytest.mark.parametrize("damage", ["name", "version", "bin", "dependency", "hardlink"])
def test_invalid_installed_tree_is_not_published(fixture, monkeypatch, damage):
    catalog, node, root, _, _, install = fixture

    def broken(command, **kwargs):
        result = install(command, **kwargs)
        stage = Path(kwargs["cwd"])
        meta_path = stage / "node_modules/helper/package.json"
        if damage in ("name", "version"):
            meta = json.loads(meta_path.read_text())
            meta[damage] = "unexpected" if damage == "name" else "9.0.0"
            write_json(meta_path, meta)
        elif damage == "dependency":
            meta_path.unlink()
        elif damage == "bin":
            meta_path = stage / "node_modules/@example/server/package.json"
            meta = json.loads(meta_path.read_text())
            meta["bin"] = {"example": "../outside.js"}
            write_json(meta_path, meta)
        else:
            entry = stage / "node_modules/@example/server/dist/index.js"
            outside = root.parent / "outside.js"
            outside.write_text("outside", encoding="utf-8")
            entry.unlink()
            os.link(outside, entry)
        return result

    monkeypatch.setattr(subprocess, "run", broken)
    with pytest.raises(ValueError):
        loader().ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert not list(root.glob("memory-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_runtime_rejects_windows_junction(fixture):
    catalog, node, root, _, calls, _ = fixture
    module = loader()
    metadata = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    junction = root.parent / "junction"
    result = REAL_RUN(["cmd", "/c", "mklink", "/J", str(junction), metadata["root"]],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert result.returncode == 0
    with pytest.raises(ValueError, match="links or junctions"):
        module.verify_runtime("memory", junction, catalog_root=catalog)
    assert len(calls) == 1


def test_cli_outputs_metadata_and_sanitizes_failures(fixture, monkeypatch, capsys):
    catalog, node, root, _, _, _ = fixture
    module = loader()
    assert module.main(["ensure", "--server", "memory", "--node", str(node),
                        "--runtime-root", str(root), "--catalog-root", str(catalog)]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["package"] == "@example/server"
    assert module.main(["ensure", "--server", "secret-invalid", "--node", str(node),
                        "--catalog-root", str(catalog)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "managed Node package verification or installation failed\n"


def test_private_default_root_is_outside_skill_discovery(monkeypatch, tmp_path):
    module = loader()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert module.default_runtime_root() == tmp_path / ("local" if os.name == "nt" else "state") / "icode/mcp-node"


def test_missing_matching_npm_does_not_fall_back_to_path(fixture):
    _, node, _, _, _, _ = fixture
    module = loader()
    module.resolve_npm_cli(node).unlink()
    with pytest.raises(ValueError, match="npm-cli.js is missing"):
        module.resolve_npm_cli(node)


def test_atomic_publish_never_overwrites_an_existing_empty_directory(tmp_path):
    module = loader()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "payload").write_text("new", encoding="utf-8")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        module._publish(stage, existing)
    assert list(existing.iterdir()) == []
    assert (stage / "payload").is_file()


@pytest.mark.parametrize("field,value", [("integrity", None), ("resolved", 123),
                                         ("dependencies", []), ("peerDependenciesMeta", [])])
def test_malformed_lock_values_fail_as_validation_errors(fixture, field, value):
    catalog, _, _, lock, _, _ = fixture
    lock["packages"]["node_modules/@example/server"][field] = value
    write_json(catalog / "memory/package-lock.json", lock)
    with pytest.raises(ValueError):
        loader().load_spec("memory", catalog)


def test_new_node_reuses_package_after_install_node_is_removed(fixture):
    catalog, node, root, _, calls, _ = fixture
    module = loader()
    old = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    new_node = node.parent / "new-node.exe"
    new_node.write_bytes(node.read_bytes())
    node.unlink()
    reused = module.ensure_runtime("memory", new_node, runtime_root=root, catalog_root=catalog)
    assert reused["root"] == old["root"]
    assert reused["node"] == str(new_node.resolve())
    assert module.verify_runtime("memory", old["root"], catalog_root=catalog)["entry"] == old["entry"]
    assert len(calls) == 1


def test_unmarked_directory_is_rejected_before_hashing_user_files(fixture, monkeypatch):
    catalog, _, root, _, _, _ = fixture
    root.mkdir()
    (root / "private-user-data.json").write_text("private fixture", encoding="utf-8")
    module = loader()
    monkeypatch.setattr(module, "_files", lambda _: pytest.fail("unowned user files were inspected"))
    with pytest.raises(ValueError):
        module.verify_runtime("memory", root, catalog_root=catalog)


@pytest.mark.parametrize("upgrade", ["package_version", "transitive_lock"])
def test_owned_runtime_verifies_previous_lock_during_catalog_upgrade(fixture, monkeypatch, upgrade):
    catalog, node, root, lock, _, install = fixture
    module = loader()
    old = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    identity = {key: old[key] for key in ("package", "version", "lock_sha256", "entry")}
    before = {p.relative_to(old["root"]): p.read_bytes()
              for p in Path(old["root"]).rglob("*") if p.is_file()}
    target_version, helper_version = ("2.0.0", "2.0.0") if upgrade == "package_version" else ("1.0.0", "2.0.1")
    manifest = json.loads((catalog / "memory/package.json").read_text())
    manifest["dependencies"]["@example/server"] = target_version
    lock["packages"][""] = manifest
    lock["packages"]["node_modules/@example/server"]["version"] = target_version
    lock["packages"]["node_modules/@example/server"]["resolved"] = (
        "https://registry.npmjs.org/@example/server/-/server-" + target_version + ".tgz")
    lock["packages"]["node_modules/helper"]["version"] = helper_version
    lock["packages"]["node_modules/helper"]["resolved"] = (
        "https://registry.npmjs.org/helper/-/helper-" + helper_version + ".tgz")
    catalog_data = json.loads((catalog / "catalog.json").read_text())
    catalog_data["servers"]["memory"]["version"] = target_version
    write_json(catalog / "catalog.json", catalog_data)
    write_json(catalog / "memory/package.json", manifest)
    write_json(catalog / "memory/package-lock.json", lock)
    with pytest.raises(ValueError):
        module.verify_runtime("memory", old["root"], catalog_root=catalog)
    assert hasattr(module, "verify_owned_runtime"), "previous-lock runtime verification is missing"
    assert module.verify_owned_runtime("memory", old["root"], **identity) == old

    def upgraded_install(command, **kwargs):
        result = install(command, **kwargs)
        for name, version in (("@example/server", target_version), ("helper", helper_version)):
            path = Path(kwargs["cwd"]) / "node_modules" / name / "package.json"
            data = json.loads(path.read_text())
            data["version"] = version
            write_json(path, data)
        return result

    monkeypatch.setattr(subprocess, "run", upgraded_install)
    new = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    assert new["root"] != old["root"] and new["version"] == target_version
    assert before == {p.relative_to(old["root"]): p.read_bytes()
                      for p in Path(old["root"]).rglob("*") if p.is_file()}
    (catalog / "catalog.json").unlink()
    monkeypatch.setattr(module, "load_spec", lambda *a, **k: pytest.fail("old verification consulted current catalog"))
    node.unlink()  # A removed installation Node must not block package upgrade.
    assert module.verify_owned_runtime("memory", old["root"], **identity) == old


@pytest.mark.parametrize("damage", ["hash", "package", "version", "entry", "relative_entry", "server", "tamper", "extra", "owner"])
def test_owned_runtime_rejects_unbound_identity_or_damaged_tree(fixture, damage):
    catalog, node, root, _, _, _ = fixture
    module = loader()
    old = module.ensure_runtime("memory", node, runtime_root=root, catalog_root=catalog)
    identity = {key: old[key] for key in ("package", "version", "lock_sha256", "entry")}
    server = "memory"
    if damage == "hash":
        identity["lock_sha256"] = "0" * 64
    elif damage == "package":
        identity["package"] = "@other/server"
    elif damage == "version":
        identity["version"] = "9.0.0"
    elif damage == "entry":
        identity["entry"] = str(root.parent / "outside.js")
    elif damage == "relative_entry":
        identity["entry"] = "dist/index.js"
    elif damage == "server":
        server = "playwright"
    elif damage == "tamper":
        Path(old["entry"]).write_text("changed", encoding="utf-8")
    elif damage == "extra":
        (Path(old["root"]) / "unexpected.json").write_text("{}", encoding="utf-8")
    else:
        (Path(old["root"]) / module.MARKER).unlink()
    assert hasattr(module, "verify_owned_runtime"), "previous-lock runtime verification is missing"
    with pytest.raises(ValueError):
        module.verify_owned_runtime(server, old["root"], **identity)
