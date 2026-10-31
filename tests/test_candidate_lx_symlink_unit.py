"""Read-only candidate identity for Windows-hosted Linux links; no device I/O."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "candidate_lx_test", Path(__file__).resolve().parents[1] / "tools/icode_candidate.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
LX_TAG = 0xA000001D


def reparse(target=b"library.so.1", *, tag=LX_TAG, version=2, reserved=0):
    payload = struct.pack("<I", version) + target
    return struct.pack("<IHH", tag, len(payload), reserved) + payload


class LxLinkTests(unittest.TestCase):
    def test_parser_keeps_exact_linux_target_bytes(self):
        for target in (b"library.so.1", b"../lib/library.so", b"name-\xff"):
            with self.subTest(target=target):
                self.assertEqual(api._parse_lx_symlink(reparse(target)), target)

    def test_parser_rejects_malformed_or_other_reparse_points(self):
        for raw in (b"", reparse()[:-1], reparse() + b"x", reparse(tag=0xA000000C),
                    reparse(version=1), reparse(reserved=1), reparse(b""),
                    reparse(b"bad\0target")):
            with self.subTest(raw=raw):
                with self.assertRaises(api.CandidateError):
                    api._parse_lx_symlink(raw)

    def test_link_classification_does_not_treat_all_reparse_points_as_links(self):
        for mode, tag, expected in ((stat.S_IFLNK, 0, True), (stat.S_IFREG, LX_TAG, True),
                                    (stat.S_IFREG, 0x80000017, False), (stat.S_IFREG, 0, False)):
            self.assertEqual(api._is_candidate_link(SimpleNamespace(st_mode=mode, st_reparse_tag=tag)), expected)

    def capture(self, *, changed=False, budget=None, linked_parent=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "library.so"
            path.write_bytes(b"placeholder")
            original = path.lstat()
            root_info = root.stat()
            target = b"library.so.1"
            oid = hashlib.sha1(b"blob " + str(len(target)).encode() + b"\0" + target).hexdigest()
            fake = dict(st_mode=original.st_mode, st_dev=original.st_dev, st_ino=original.st_ino,
                        st_size=0, st_mtime_ns=original.st_mtime_ns, st_ctime_ns=original.st_ctime_ns,
                        st_reparse_tag=LX_TAG)
            count = 0
            real_lstat = Path.lstat

            def lstat(item, *args, **kwargs):
                nonlocal count
                if item == path:
                    count += 1
                    return SimpleNamespace(**dict(fake, st_ino=fake["st_ino"] + int(changed and count > 1)))
                if linked_parent and item == root:
                    return SimpleNamespace(**fake)
                return real_lstat(item, *args, **kwargs)

            class Provider(api.CandidateGitProvider):
                workspace = root

                def assert_context(self):
                    return None

                def query(self, request, *, limit):
                    identity = {"common_git_dir": str(root), "checkout_realpath": str(root),
                                "object_format": "sha1", "common_git_device": root_info.st_dev,
                                "common_git_inode": root_info.st_ino, "checkout_device": root_info.st_dev,
                                "checkout_inode": root_info.st_ino}
                    data = {"facts": json.dumps({"identity": identity, "base": "1" * 40,
                            "head": "1" * 40, "head_tree": "2" * 40}).encode(),
                            "tree": ("120000 blob " + oid + "\tlibrary.so\0").encode(),
                            "index": ("120000 " + oid + " 0\tlibrary.so\0").encode(), "others": b""}
                    return api.CandidateReadResult(data[request.operation.value], 0, False)

            with patch.object(Path, "lstat", lstat), patch.object(api, "_read_lx_symlink", return_value=target):
                return api.capture_candidate(root, base="1" * 40, git_provider=Provider(),
                                             require_git_provider=True, limits=budget)

    def test_capture_hashes_link_itself_instead_of_target_file(self):
        row = self.capture()["manifest"][0]
        self.assertEqual((row["mode"], row["kind"], row["size"]), ("120000", "symlink", 12))
        self.assertEqual(row["sha256"], hashlib.sha256(b"library.so.1").hexdigest())

    def test_capture_rejects_link_replacement(self):
        with self.assertRaisesRegex(api.CandidateError, "changed"):
            self.capture(changed=True)

    def test_capture_rejects_linked_parent(self):
        with self.assertRaisesRegex(api.CandidateError, "symlink parent"):
            self.capture(linked_parent=True)

    def test_link_target_bytes_remain_in_content_budget(self):
        with self.assertRaisesRegex(api.CandidateError, "max_bytes"):
            self.capture(budget={"max_bytes": 3})


if __name__ == "__main__":
    unittest.main()
