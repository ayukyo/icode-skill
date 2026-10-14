"""Observable DOC snapshot behavior in isolated repositories and libraries."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools" / "doc_snapshot.py"
spec = importlib.util.spec_from_file_location("doc_snapshot", TOOL)
doc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = doc
spec.loader.exec_module(doc)

GIT = shutil.which("git")
URL = "https://example.invalid/team/module.git"


@unittest.skipUnless(GIT, "Git is required for isolated source fixtures")
class SnapshotBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="icode-doc-test-")
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.root / "source with spaces"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "source.py").write_text("answer = 1\n", encoding="utf-8")
        self.fixture_revision()
        self.library = self.root / "library"

    def git(self, *args, data=None):
        result = subprocess.run([GIT, "-C", str(self.repo), *args], input=data,
                                capture_output=True, check=True)
        return result.stdout.strip().decode("utf-8")

    def fixture_revision(self):
        # Construct history objects only in the owned temporary fixture; the
        # real ICODE checkout is never committed or assigned a new reference.
        self.git("add", "--all")
        tree = self.git("write-tree")
        parent = subprocess.run([GIT, "-C", str(self.repo), "rev-parse", "--verify", "HEAD"],
                                capture_output=True)
        parent_line = b"parent " + parent.stdout.strip() + b"\n" if parent.returncode == 0 else b""
        raw = (f"tree {tree}\n".encode() + parent_line +
               b"author Fixture <fixture@example.invalid> 1 +0000\n"
               b"committer Fixture <fixture@example.invalid> 1 +0000\n\nfixture\n")
        oid = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=raw)
        self.git("update-ref", "HEAD", oid)
        return oid

    def capture(self, repo=None, dependencies=()):
        return doc.capture(repo or self.repo, URL, "main", git_executable=GIT,
                           dependencies=dependencies)

    def stage(self, identity, suffix="", scope="full"):
        stage = self.root / ("stage" + suffix)
        stage.mkdir()
        for name in ("00_overview.md", "90_glossary.md", "99_code_facts_audit.md"):
            (stage / name).write_text(f"# {name}\n\n{identity['source_id']} {suffix}\n", encoding="utf-8")
        review = {
            "source_id": identity["source_id"], "template_version": "v2.0.0",
            "status": "verified", "scope": scope,
            "chapter_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in stage.glob("*.md")},
            "evidence": ["isolated review fixture"],
        }
        return stage, review

    def publish(self, identity=None, suffix="", scope="full"):
        identity = identity or self.capture()
        stage, review = self.stage(identity, suffix, scope)
        binding = doc.publish(self.library, identity, stage, review, git_executable=GIT)
        return identity, binding

    def parent_fixture(self):
        project = self.root / "parent source"
        project.mkdir()
        module = project / "module a"
        shutil.copytree(self.repo, module)
        self.repo = module
        metadata = self.root / "project docs" / "_meta.json"
        metadata.parent.mkdir()
        metadata.write_text(json.dumps({"project_id": "parent", "head_commit": "old-parent-baseline",
                                        "unknown": {"preserve": True}, "module_deps": []}), encoding="utf-8")
        return project, metadata

    def test_parent_binding_preserves_unknown_fields_and_whole_project_baseline(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        result = doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                     project, "module a", "module-a", git_executable=GIT)
        parent = json.loads(metadata.read_text())
        self.assertEqual(parent["unknown"], {"preserve": True})
        self.assertEqual(parent["head_commit"], "old-parent-baseline")
        self.assertEqual(parent["module_deps"][0]["snapshot_id"], version["snapshot_id"])
        self.assertTrue(result["generated"])

    def test_malformed_identity_source_path_is_a_structured_error(self):
        identity = self.capture()
        for bad in (None, [], 123, "relative/path"):
            with self.subTest(bad=bad), self.assertRaises(doc.SnapshotError):
                doc.resolve(self.library, {**identity, "repo_root": bad})

    def test_malformed_parent_dependency_path_preserves_original_metadata(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        metadata.write_text('{"module_deps":[{"path":null}]}', encoding="utf-8")
        original = metadata.read_bytes()
        with self.assertRaises(doc.SnapshotError):
            doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                project, "module a", "module-a", git_executable=GIT)
        self.assertEqual(metadata.read_bytes(), original)

    def test_publication_library_inside_source_is_rejected_before_writing(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        inside = self.repo / "ignored-doc-library"
        with self.assertRaises(doc.SnapshotError):
            doc.publish(inside, identity, stage, review, git_executable=GIT)
        self.assertFalse(inside.exists())

    def test_binding_metadata_inside_module_outside_parent_is_rejected(self):
        ignored = self.repo / ".peer-meta"
        (self.repo / ".gitignore").write_text(".peer-meta/\n", encoding="utf-8")
        identity, version = self.publish(suffix="-ignore")
        project = self.root / "logical project"
        project.mkdir()
        link = project / "module-link"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.repo)],
                           check=True, capture_output=True)
        else:
            link.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(doc.SnapshotError):
            doc.bind_dependency(self.library, identity, version["snapshot_id"], ignored / "_meta.json",
                                project, "module-link", "module", git_executable=GIT)
        self.assertFalse(ignored.exists())

    def test_foreign_host_recorded_path_does_not_change_reusable_content(self):
        identity, binding = self.publish()
        path = Path(binding["directory"]) / "_meta.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["source_identity"]["repo_root"] = "/home/example/source" if os.name == "nt" else "C:\\Users\\example\\source"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assertEqual(doc.resolve(self.library, identity, binding["snapshot_id"])["status"], "match")

    def test_parent_two_paths_of_same_family_bind_distinct_versions(self):
        project, metadata = self.parent_fixture()
        first, version_a = self.publish(suffix="-a")
        other = project / "module b"
        shutil.copytree(self.repo, other)
        (other / "source.py").write_text("answer = 2\n", encoding="utf-8")
        second, version_b = self.publish(self.capture(other), suffix="-b")
        for identity, version, path in ((first, version_a, "module a"), (second, version_b, "module b")):
            doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                project, path, path, git_executable=GIT)
        deps = json.loads(metadata.read_text())["module_deps"]
        self.assertEqual({d["snapshot_id"] for d in deps}, {version_a["snapshot_id"], version_b["snapshot_id"]})
        self.assertEqual({d["path"] for d in deps}, {"module a/", "module b/"})

    def test_legacy_path_alias_updates_existing_binding_and_keeps_private_notes(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        aliases = ["./module a/", "module a\\", "module a//"]
        if os.name == "nt":
            aliases.append("MODULE A/")
        for alias in aliases:
            metadata.write_text(json.dumps({"module_deps": [{"path": alias, "private_note": "keep"}]}), encoding="utf-8")
            doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                project, "module a", "module-a", git_executable=GIT)
            entries = json.loads(metadata.read_text(encoding="utf-8"))["module_deps"]
            with self.subTest(alias=alias):
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["private_note"], "keep")
                self.assertEqual(entries[0]["path"], "module a/")

    def test_duplicate_physical_binding_aliases_preserve_old_data(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        metadata.write_text('{"module_deps":[{"path":"module a/","note":"one"},{"path":"./module a/","note":"two"}]}', encoding="utf-8")
        original = metadata.read_bytes()
        with self.assertRaises(doc.SnapshotError):
            doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                project, "module a", "module-a", git_executable=GIT)
        self.assertEqual(metadata.read_bytes(), original)

    def test_missing_snapshot_never_updates_parent_generation_status(self):
        project, metadata = self.parent_fixture()
        identity = self.capture()
        original = metadata.read_bytes()
        with self.assertRaises(doc.SnapshotError):
            doc.bind_dependency(self.library, identity, "0" * 64, metadata,
                                project, "module a", "module-a", git_executable=GIT)
        self.assertEqual(metadata.read_bytes(), original)

    def test_partial_binding_remains_not_generated(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish(scope="partial")
        result = doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                     project, "module a", "module-a", git_executable=GIT)
        self.assertFalse(result["generated"])
        self.assertEqual(json.loads(metadata.read_text())["head_commit"], "old-parent-baseline")

    def test_external_parent_metadata_edit_is_preserved_on_write_conflict(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        original_fsync = doc.os.fsync
        outside_edit = b'{"manual":"new value","module_deps":[]}'
        def edit_before_replace(fd):
            original_fsync(fd)
            metadata.write_bytes(outside_edit)
        with patch.object(doc.os, "fsync", side_effect=edit_before_replace):
            with self.assertRaises(doc.SnapshotError):
                doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                    project, "module a", "module-a", git_executable=GIT)
        self.assertEqual(metadata.read_bytes(), outside_edit)
        self.assertFalse((metadata.parent / ".doc-binding.lock").exists())

    def test_parent_binding_rejects_escape_and_source_pollution(self):
        project, metadata = self.parent_fixture()
        identity, version = self.publish()
        for relative in ("../module a", "/module a", "C:module a", "module\\a"):
            with self.subTest(relative=relative), self.assertRaises(doc.SnapshotError):
                doc.bind_dependency(self.library, identity, version["snapshot_id"], metadata,
                                    project, relative, "module-a", git_executable=GIT)
        bad_metadata = self.repo / "_meta.json"
        with self.assertRaises(doc.SnapshotError):
            doc.bind_dependency(self.library, identity, version["snapshot_id"], bad_metadata,
                                project, "module a", "module-a", git_executable=GIT)
        self.assertFalse(bad_metadata.exists())

    def test_two_commits_coexist_and_each_binding_resolves_its_own_body(self):
        first, binding_a = self.publish(suffix="-a")
        second_repo = self.root / "second project source"
        shutil.copytree(self.repo, second_repo)
        self.repo = second_repo
        (self.repo / "source.py").write_text("answer = 2\n", encoding="utf-8")
        self.fixture_revision()
        second, binding_b = self.publish(suffix="-b")
        self.assertEqual(first["module_key"], second["module_key"])
        self.assertNotEqual(first["source_id"], second["source_id"])
        for identity, binding in ((first, binding_a), (second, binding_b)):
            result = doc.resolve(self.library, identity, binding["snapshot_id"])
            self.assertEqual(result["status"], "match")
            self.assertEqual(result["source_id"], identity["source_id"])
            self.assertIn(identity["source_id"], (Path(result["directory"]) / "00_overview.md").read_text())

    def test_current_workspace_refuses_binding_for_another_commit(self):
        old, binding = self.publish()
        (self.repo / "source.py").write_text("answer = 2\n", encoding="utf-8")
        self.fixture_revision()
        result = doc.resolve(self.library, self.capture(), binding["snapshot_id"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertNotIn("directory", result)

    def test_same_head_different_dirty_files_do_not_reuse(self):
        clean = self.capture()
        (self.repo / "source.py").write_text("answer = 2\n", encoding="utf-8")
        dirty = self.capture()
        self.assertEqual(clean["commit"], dirty["commit"])
        self.assertNotEqual(clean["source_id"], dirty["source_id"])
        self.assertEqual(dirty["source_mode"], "working-tree")

    def test_same_content_in_different_checkout_reuses_source_identity(self):
        other = self.root / "other checkout"
        shutil.copytree(self.repo, other)
        self.assertEqual(self.capture()["source_id"], self.capture(other)["source_id"])

    def test_staged_unstaged_untracked_and_deleted_inputs_are_observed(self):
        original = self.capture()
        (self.repo / "source.py").write_text("answer = 3\n", encoding="utf-8")
        self.git("add", "source.py")
        staged = self.capture()
        (self.repo / "source.py").write_text("answer = 4\n", encoding="utf-8")
        unstaged = self.capture()
        extra = self.repo / "new source.py"
        extra.write_text("new = 1\n", encoding="utf-8")
        untracked = self.capture()
        extra.unlink()
        (self.repo / "source.py").unlink()
        deleted = self.capture()
        self.assertEqual(len({x["source_id"] for x in (original, staged, unstaged, untracked, deleted)}), 5)

    def test_dependency_change_invalidates_unchanged_module_source(self):
        dependency = self.root / "dep"
        shutil.copytree(self.repo, dependency)
        first = self.capture(dependencies=[self.capture(dependency)])
        (dependency / "source.py").write_text("answer = 7\n", encoding="utf-8")
        second = self.capture(dependencies=[self.capture(dependency)])
        self.assertNotEqual(first["source_id"], second["source_id"])

    def test_two_same_family_dependency_versions_have_explicit_roles(self):
        left = self.root / "left dependency"
        right = self.root / "right dependency"
        shutil.copytree(self.repo, left)
        shutil.copytree(self.repo, right)
        (right / "source.py").write_text("answer = 2\n", encoding="utf-8")
        dep_a = {**self.capture(left), "input_role": "left-sdk"}
        dep_b = {**self.capture(right), "input_role": "right-sdk"}
        original = self.capture(dependencies=[dep_a, dep_b])
        reordered = self.capture(dependencies=[dep_b, dep_a])
        swapped = self.capture(dependencies=[{**dep_a, "input_role": "right-sdk"},
                                             {**dep_b, "input_role": "left-sdk"}])
        self.assertEqual(original["source_id"], reordered["source_id"])
        self.assertNotEqual(original["source_id"], swapped["source_id"])
        with self.assertRaises(doc.SnapshotError):
            self.capture(dependencies=[self.capture(left), self.capture(right)])

    def test_identical_dependency_copies_do_not_change_content_identity(self):
        other = self.root / "same dependency"
        shutil.copytree(self.repo, other)
        first = self.capture(other)
        second = self.capture()
        self.assertEqual(self.capture(dependencies=[first])["source_id"],
                         self.capture(dependencies=[first, second])["source_id"])

    def test_source_drift_during_generation_prevents_publication(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        (self.repo / "source.py").write_text("changed during generation\n", encoding="utf-8")
        with self.assertRaises(doc.SnapshotError):
            doc.publish(self.library, identity, stage, review, git_executable=GIT)
        self.assertFalse(list(self.library.glob("**/_meta.json")))

    def test_changed_chapter_after_review_is_not_published(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        (stage / "00_overview.md").write_text("changed after review", encoding="utf-8")
        with self.assertRaises(doc.SnapshotError):
            doc.publish(self.library, identity, stage, review, git_executable=GIT)

    def test_legacy_body_and_unknown_metadata_survive_new_publication(self):
        identity = self.capture()
        family = self.library / identity["module_key"]
        family.mkdir(parents=True)
        body = family / "00_overview.md"
        body.write_bytes(b"hand edited old body")
        meta = family / "_meta.json"
        meta.write_text('{"unknown":{"keep":true},"current_commit":"old"}', encoding="utf-8")
        before = body.read_bytes(), meta.read_bytes()
        self.publish(identity)
        self.assertEqual(before, (body.read_bytes(), meta.read_bytes()))

    def test_legacy_matching_commit_without_content_evidence_is_summary_only(self):
        identity = self.capture()
        family = self.library / identity["module_key"]
        family.mkdir(parents=True)
        (family / "00_overview.md").write_text("legacy body", encoding="utf-8")
        (family / "_meta.json").write_text(json.dumps({"current_commit": identity["commit"]}))
        result = doc.resolve(self.library, identity)
        self.assertEqual(result["status"], "legacy_summary_only")
        self.assertNotIn("directory", result)

    def test_manual_edit_even_with_preserved_mtime_blocks_body_retrieval(self):
        identity, binding = self.publish()
        chapter = Path(binding["directory"]) / "00_overview.md"
        old_stat = chapter.stat()
        chapter.write_text("edited manually", encoding="utf-8")
        os.utime(chapter, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertEqual(doc.resolve(self.library, identity, binding["snapshot_id"])["status"], "modified")

    def test_partial_snapshot_never_resolves_as_complete(self):
        identity, binding = self.publish(scope="partial")
        self.assertEqual(doc.resolve(self.library, identity, binding["snapshot_id"])["status"], "partial")

    def test_same_source_new_reviewed_revision_preserves_old_snapshot(self):
        identity, first = self.publish(suffix="-one")
        _, second = self.publish(identity, suffix="-two")
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(doc.resolve(self.library, identity, first["snapshot_id"])["status"], "match")
        self.assertEqual(doc.resolve(self.library, identity, second["snapshot_id"])["status"], "match")
        self.assertEqual(doc.resolve(self.library, identity)["status"], "ambiguous")

    def test_failed_directory_publish_never_advertises_snapshot(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        with patch.object(doc.os, "replace", side_effect=OSError("injected publication failure")):
            with self.assertRaises(OSError):
                doc.publish(self.library, identity, stage, review, git_executable=GIT)
        result = doc.resolve(self.library, identity)
        self.assertEqual(result["status"], "missing")

    def test_path_traversal_snapshot_identifiers_are_rejected(self):
        identity = self.capture()
        for unsafe in ("../outside", "/absolute", "C:relative", "file:stream", "a\\b"):
            with self.subTest(unsafe=unsafe), self.assertRaises(doc.SnapshotError):
                doc.resolve(self.library, identity, unsafe)

    def test_source_changes_during_copy_do_not_publish(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        copy = doc.shutil.copyfile
        def changing_copy(src, target):
            result = copy(src, target)
            (self.repo / "source.py").write_text("changed during copying\n", encoding="utf-8")
            return result
        with patch.object(doc.shutil, "copyfile", side_effect=changing_copy):
            with self.assertRaises(doc.SnapshotError):
                doc.publish(self.library, identity, stage, review, git_executable=GIT)
        self.assertEqual(doc.resolve(self.library, self.capture())["status"], "missing")

    def test_corrupted_source_and_review_metadata_cannot_enable_retrieval(self):
        identity, binding = self.publish()
        path = Path(binding["directory"]) / "_meta.json"
        original = json.loads(path.read_text())
        for field, value in (("commit", "0" * 40), ("review", {"status": "verified"})):
            altered = {**original, field: value}
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(doc.SnapshotError):
                doc.resolve(self.library, identity, binding["snapshot_id"])
        path.write_text(json.dumps(original), encoding="utf-8")

    def test_parallel_publications_keep_both_versions_and_no_partial_reader(self):
        from concurrent.futures import ThreadPoolExecutor
        identity = self.capture()
        first, review_a = self.stage(identity, "-parallel-a")
        second, review_b = self.stage(identity, "-parallel-b")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: doc.publish(self.library, identity, *pair, git_executable=GIT),
                                    ((first, review_a), (second, review_b))))
        for binding in results:
            self.assertEqual(doc.resolve(self.library, identity, binding["snapshot_id"])["status"], "match")
        self.assertEqual(doc.resolve(self.library, identity)["status"], "ambiguous")

    def test_mixed_source_generation_is_rejected_before_identity_is_returned(self):
        first = self.repo / "a.py"
        second = self.repo / "b.py"
        first.write_text("a = 1\n", encoding="utf-8")
        second.write_text("b = 1\n", encoding="utf-8")
        original_sha = doc._file_sha
        changed = False
        def changing_sha(path):
            nonlocal changed
            result = original_sha(path)
            if path == first and not changed:
                changed = True
                first.write_text("a = 9\n", encoding="utf-8")
                second.write_text("b = 9\n", encoding="utf-8")
            return result
        with patch.object(doc, "_file_sha", side_effect=changing_sha):
            with self.assertRaises(doc.SnapshotError):
                self.capture()

    def test_valid_json_with_wrong_top_level_type_reports_cli_error(self):
        identity = self.capture()
        stage, review = self.stage(identity)
        ipath = self.root / "identity.json"
        rpath = self.root / "review.json"
        ipath.write_text(json.dumps(identity), encoding="utf-8")
        rpath.write_text("[]", encoding="utf-8")
        args = [sys.executable, "-B", str(TOOL), "--git-executable", GIT, "publish",
                "--identity", str(ipath), "--stage", str(stage), "--review", str(rpath),
                "--library-root", str(self.library)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))

        rpath.write_text(json.dumps(review), encoding="utf-8")
        binding = doc.publish(self.library, identity, stage, review, git_executable=GIT)
        (Path(binding["directory"]) / "_meta.json").write_text("[]", encoding="utf-8")
        args = [sys.executable, "-B", str(TOOL), "resolve", "--identity", str(ipath),
                "--library-root", str(self.library), "--snapshot-id", binding["snapshot_id"]]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))

    def test_stale_identity_file_cannot_retrieve_current_workspace_body(self):
        identity, binding = self.publish()
        (self.repo / "source.py").write_text("dirty after identity capture\n", encoding="utf-8")
        result = doc.resolve(self.library, identity, binding["snapshot_id"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertNotIn("directory", result)

    def test_reserved_windows_chapter_names_fail_before_writing(self):
        if os.name == "nt":
            self.skipTest("Reserved files cannot be staged on Windows; Linux tests portable publication")
        identity = self.capture()
        for name in ("CON.md", "nul.md", "LPT1.md"):
            stage, review = self.stage(identity, "-" + name)
            path = stage / name
            path.write_text("nonportable chapter", encoding="utf-8")
            review["chapter_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(name=name), self.assertRaises(doc.SnapshotError):
                doc.publish(self.library, identity, stage, review, git_executable=GIT)
        self.assertFalse(self.library.exists())


    def test_cache_and_resume_revalidate_source_and_bound_snapshot(self):
        identity, binding = self.publish()
        resolved = doc.resolve(self.library, identity, binding["snapshot_id"])
        item = {"status": "done", "source_id": identity["source_id"], "snapshot_id": binding["snapshot_id"]}
        self.assertTrue(doc.can_resume(item, identity, resolved))
        old_key = doc.cache_key(str(self.repo), ["api", "frame"], identity, [binding])
        self.assertEqual(old_key, doc.cache_key(str(self.repo), ["frame", "api"], identity, [binding]))
        (self.repo / "source.py").write_text("answer = 99\n", encoding="utf-8")
        changed = self.capture()
        self.assertNotEqual(old_key, doc.cache_key(str(self.repo), ["api", "frame"], changed, [binding]))
        self.assertFalse(doc.can_resume(item, changed, resolved))

    def test_cache_identity_includes_template_without_module_bindings(self):
        identity = self.capture()
        first = doc.cache_key(str(self.repo), ["api"], identity, [], template_version="v2.0.0")
        second = doc.cache_key(str(self.repo), ["api"], identity, [], template_version="v2.1.0")
        self.assertNotEqual(first, second)


class DefaultLibraryBehavior(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX empty HOME fixture")
    def test_empty_home_uses_system_account_home(self):
        import pwd
        expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".claude/icode_data/module_docs"
        with patch.dict(os.environ, {"HOME": ""}):
            self.assertEqual(doc.default_library_root(), expected)


if __name__ == "__main__":
    unittest.main()
