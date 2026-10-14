from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
REAL_CONNECT = sqlite3.connect


class CommitFailureConnection(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError("injected commit failure")


class LocalIndexLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = REPO / "mcp" / "icode-local-index" / "server.py"
        spec = importlib.util.spec_from_file_location("test_local_index_lifecycle_server", path)
        assert spec and spec.loader
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.source = self.docs / "note.md"
        self.source.write_text("first needle : content\n", encoding="utf-8")
        self.data = self.root / "index"
        config = self.root / "index-config.json"
        config.write_text(json.dumps({
            "allowed_roots": [str(self.root)],
            "data_dir": str(self.data),
            "extensions": [".md"],
            "exclude_dirs": [".git"],
        }), encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"ICODE_LOCAL_INDEX_CONFIG": str(config)})
        environment.start()
        self.addCleanup(environment.stop)
        self.connections = []

    def track_connections(self, *, factory=sqlite3.Connection, denied_operation=None, denied_table=None):
        def connect(database, *args, **kwargs):
            kwargs["factory"] = factory
            connection = REAL_CONNECT(database, *args, **kwargs)
            # Keep a real connection alive so GC cannot hide a missing close().
            self.connections.append(connection)
            self.addCleanup(connection.close)
            if denied_operation is not None:
                connection.set_authorizer(
                    lambda action, table, *_: sqlite3.SQLITE_DENY
                    if action == denied_operation and (denied_table is None or table == denied_table)
                    else sqlite3.SQLITE_OK
                )
            return connection

        return mock.patch.object(self.module.sqlite3, "connect", side_effect=connect)

    def assert_connections_closed(self):
        self.assertTrue(self.connections, "the operation must open a real SQLite connection")
        for connection in self.connections:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")

    def build_database(self):
        result = self.module.build_index(str(self.docs))
        self.assertNotIn("error_code", result)
        return Path(result["answer"]["database"])

    def assert_no_temporary_database(self):
        self.assertEqual(list(self.data.glob("*.tmp*")), [])

    def test_status_success_closes_database(self):
        database = self.build_database()
        with self.track_connections():
            result = self.module.index_status(str(self.docs))
        self.assertTrue(result["answer"]["exists"])
        self.assert_connections_closed()
        database.unlink()

    def test_query_success_closes_database(self):
        database = self.build_database()
        with self.track_connections():
            result = self.module.query_index(str(self.docs), "needle")
        self.assertEqual(result["answer"]["count"], 1)
        self.assert_connections_closed()
        database.unlink()

    def test_query_fts_error_falls_back_and_closes_database(self):
        self.build_database()
        with self.track_connections():
            result = self.module.query_index(str(self.docs), ":")
        self.assertEqual(result["answer"]["count"], 1)
        self.assert_connections_closed()

    def test_read_errors_close_corrupt_database(self):
        database = self.build_database()
        database.write_bytes(b"not a sqlite database")
        for name, arguments, error_code in (
            ("index_status", (str(self.docs),), "index_status_failed"),
            ("query_index", (str(self.docs), "needle"), "index_query_failed"),
        ):
            with self.subTest(tool=name), self.track_connections():
                result = getattr(self.module, name)(*arguments)
                self.assertEqual(result["error_code"], error_code)
                self.assert_connections_closed()

    def test_metadata_errors_close_database(self):
        database = self.build_database()
        connection = REAL_CONNECT(database)
        try:
            connection.execute("UPDATE meta SET value = 'invalid-json' WHERE key = 'mode'")
            connection.commit()
        finally:
            connection.close()
        for name, arguments, error_code in (
            ("index_status", (str(self.docs),), "index_status_failed"),
            ("query_index", (str(self.docs), "needle"), "index_query_failed"),
        ):
            with self.subTest(tool=name), self.track_connections():
                result = getattr(self.module, name)(*arguments)
                self.assertEqual(result["error_code"], error_code)
                self.assert_connections_closed()

    def test_schema_error_closes_connection_before_temp_cleanup(self):
        with self.track_connections(denied_operation=sqlite3.SQLITE_CREATE_TABLE):
            result = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assertIn("not authorized", result["error"])
        self.assert_no_temporary_database()

    def test_source_read_error_closes_connection_and_preserves_old_index(self):
        database = self.build_database()
        previous = database.read_bytes()
        original_read = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == self.source:
                raise OSError("injected source read failure")
            return original_read(path, *args, **kwargs)

        with self.track_connections(), mock.patch.object(Path, "read_text", read_text):
            result = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assertIn("injected source read failure", result["error"])
        self.assertEqual(database.read_bytes(), previous)
        self.assert_no_temporary_database()

    def test_insert_error_closes_connection_before_temp_cleanup(self):
        with self.track_connections(denied_operation=sqlite3.SQLITE_INSERT, denied_table="files"):
            result = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assert_no_temporary_database()

    def test_commit_error_closes_connection_and_preserves_old_index(self):
        database = self.build_database()
        previous = database.read_bytes()
        with self.track_connections(factory=CommitFailureConnection):
            result = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assertIn("injected commit failure", result["error"])
        self.assertEqual(database.read_bytes(), previous)
        self.assert_no_temporary_database()

    def test_replace_error_closes_connection_and_preserves_old_index(self):
        database = self.build_database()
        previous = database.read_bytes()
        with self.track_connections(), mock.patch.object(
            self.module.os, "replace", side_effect=OSError("injected replace failure")
        ):
            result = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assertEqual(database.read_bytes(), previous)
        self.assert_no_temporary_database()

    def test_rebuild_after_reads_closes_all_connections_and_publishes_new_content(self):
        self.build_database()
        with self.track_connections():
            self.assertEqual(self.module.query_index(str(self.docs), "needle")["answer"]["count"], 1)
            self.assertTrue(self.module.index_status(str(self.docs))["answer"]["exists"])
            self.source.write_text("replacement marker\n", encoding="utf-8")
            rebuilt = self.module.build_index(str(self.docs))
        self.assert_connections_closed()
        self.assertNotIn("error_code", rebuilt)
        self.assert_no_temporary_database()
        self.assertEqual(self.module.query_index(str(self.docs), "replacement")["answer"]["count"], 1)

    def test_connect_failure_removes_unopened_temporary_database(self):
        with mock.patch.object(
            self.module.sqlite3, "connect", side_effect=sqlite3.OperationalError("cannot connect")
        ):
            result = self.module.build_index(str(self.docs))
        self.assertEqual(result["error_code"], "index_build_failed")
        self.assert_no_temporary_database()


if __name__ == "__main__":
    unittest.main()
