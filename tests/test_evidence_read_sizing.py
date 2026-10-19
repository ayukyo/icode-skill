"""Bound actual descriptor read requests without weakening evidence checks."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class EvidenceReadSizingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "evidence_read_sizing_test", ROOT / "tools/evidence_bundle.py")
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def observe(self, reader, name, *, mutate=None, expected_hash=None):
        original_fdopen = self.api.os.fdopen
        requests = []

        class Observed:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                requests.append(size)
                # Mutate the real file after the descriptor identity check.
                if mutate is not None:
                    mutate()
                return self.stream.read(size)

        with mock.patch.object(self.api.os, "fdopen", side_effect=lambda *args, **kw:
                               Observed(original_fdopen(*args, **kw))):
            data = reader.read(name, expected_hash=expected_hash)
        return data, requests

    def test_small_files_request_at_most_observed_size_plus_growth_probe(self):
        for size in (0, 1, 65536):
            with self.subTest(bytes=size):
                raw = b"x" * size
                (self.root / "evidence.bin").write_bytes(raw)
                reader = self.api.Reader(self.root)
                data, requests = self.observe(reader, "evidence.bin",
                    expected_hash="sha256:" + hashlib.sha256(raw).hexdigest())
                self.assertEqual(data, raw)
                self.assertEqual(len(requests), 1)
                self.assertLessEqual(requests[0], size + 1,
                    "Small evidence files must not request the unrelated global byte limit")
                reader.finish()

    def test_concurrent_growth_and_shrink_are_still_rejected(self):
        for operation in ("grow", "shrink"):
            with self.subTest(operation=operation):
                path = self.root / "evidence.bin"
                path.write_bytes(b"abc")

                def mutate():
                    if operation == "grow":
                        with path.open("ab") as stream:
                            stream.write(b"d")
                    else:
                        path.write_bytes(b"a")

                with self.assertRaises(self.api.EvidenceBundleError):
                    self.observe(self.api.Reader(self.root), "evidence.bin", mutate=mutate)

    def test_byte_budget_is_enforced_before_descriptor_read(self):
        (self.root / "evidence.bin").write_bytes(b"abcdef")
        reader = self.api.Reader(self.root, limits={"max_file_bytes": 5})
        with mock.patch.object(self.api.os, "fdopen",
                               side_effect=AssertionError("Over-budget evidence was read")):
            with self.assertRaises(self.api.EvidenceBundleError):
                reader.read("evidence.bin")

    def test_wrong_expected_hash_is_rejected(self):
        (self.root / "evidence.bin").write_bytes(b"abc")
        with self.assertRaises(self.api.EvidenceBundleError):
            self.observe(self.api.Reader(self.root), "evidence.bin",
                         expected_hash="sha256:" + "0" * 64)


if __name__ == "__main__":
    unittest.main()
