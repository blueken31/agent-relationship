"""Durability checks for the optional local SQLite snapshot store."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from agent_relationship.storage import SQLiteStore, StorageError, StorageVersionError


def _increment_worker(path, start, results, count):
    try:
        start.wait(timeout=10)
        with SQLiteStore(path) as store:
            for _ in range(count):
                with store.transaction(write=True) as transaction:
                    state = transaction.load() or {"count": 0}
                    state["count"] += 1
                    transaction.save(state)
        results.put(None)
    except Exception as exc:
        results.put(type(exc).__name__ + ": " + str(exc))


class TestSQLiteStore(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"

    def _save(self, state):
        with SQLiteStore(self.path) as store:
            with store.transaction(write=True) as transaction:
                return transaction.save(state)

    def _load(self):
        with SQLiteStore(self.path) as store:
            with store.transaction() as transaction:
                return transaction.load(), transaction.revision

    def _replace_payload(self, payload, checksum=None):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE relationship_state SET payload = ?, checksum = ? WHERE id = 1",
                (payload, checksum or hashlib.sha256(payload.encode("utf-8")).hexdigest()),
            )

    def test_empty_store_and_reopen_do_not_create_a_checkpoint(self):
        with SQLiteStore(self.path) as store:
            with store.transaction() as transaction:
                self.assertIsNone(transaction.load())
                self.assertEqual(transaction.revision, 0)
        original = self.path.read_bytes()
        self.assertEqual(self._load(), (None, 0))
        self.assertEqual(self.path.read_bytes(), original)

    def test_roundtrip_revisions_and_reads_are_detached(self):
        state = {"unicode": "原任务", "items": [{"status": None}], "values": [True, 2, 0.5]}
        self.assertEqual(self._save(state), 1)
        with SQLiteStore(self.path) as store:
            with store.transaction() as transaction:
                returned = transaction.load()
                returned["items"][0]["status"] = "modified"
                self.assertEqual(transaction.load(), state)
            self.assertEqual(store.revision, 1)
            with store.transaction(write=True) as transaction:
                current = transaction.load()
                current["new"] = "value"
                self.assertEqual(transaction.save(current), 2)
                current["new"] = "caller-mutated"
                self.assertEqual(transaction.load()["new"], "value")
        self.assertEqual(self._load()[1], 2)
        self.assertEqual(self._load()[0]["new"], "value")

    def test_exception_after_save_rolls_back_payload_and_revision(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as store:
            with self.assertRaisesRegex(RuntimeError, "abort"):
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 2})
                    raise RuntimeError("abort")
            with store.transaction() as transaction:
                self.assertEqual(transaction.load(), {"count": 1})
                self.assertEqual(transaction.revision, 1)

    def test_failed_save_cannot_be_caught_to_commit_earlier_changes(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as store:
            with self.assertRaises(StorageError):
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 2})
                    try:
                        transaction.save({"invalid": float("nan")})
                    except StorageError:
                        pass
        self.assertEqual(self._load(), ({"count": 1}, 1))

    def test_commit_failure_rolls_back_the_staged_checkpoint(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as store:
            connection = store._connection
            class FailCommit:
                def __getattr__(self, name):
                    return getattr(connection, name)
                def execute(self, sql, *args):
                    if sql == "COMMIT":
                        raise sqlite3.OperationalError("private-driver-detail")
                    return connection.execute(sql, *args)
            store._connection = FailCommit()
            with self.assertRaises(StorageError) as caught:
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 2})
            self.assertNotIn("private-driver-detail", str(caught.exception))
            store._connection = connection
            self.assertEqual(store.revision, 1)
        self.assertEqual(self._load(), ({"count": 1}, 1))

    def test_readonly_save_is_rejected_without_changes(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as store:
            with self.assertRaises(StorageError):
                with store.transaction() as transaction:
                    transaction.save({"count": 2})
        self.assertEqual(self._load(), ({"count": 1}, 1))

    def test_nested_transactions_are_rejected_and_outer_can_rollback(self):
        with SQLiteStore(self.path) as store:
            with self.assertRaises(StorageError):
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 1})
                    with store.transaction():
                        pass
            self.assertEqual(store.revision, 0)

    def test_transaction_cannot_escape_or_cross_threads(self):
        with SQLiteStore(self.path) as store:
            with store.transaction(write=True) as transaction:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(transaction.save, {"count": 1})
                    with self.assertRaises(StorageError):
                        future.result()
                transaction.save({"count": 2})
            with self.assertRaises(StorageError):
                transaction.load()
            with self.assertRaises(StorageError):
                transaction.save({"count": 3})
        self.assertEqual(self._load(), ({"count": 2}, 1))

    def test_close_is_idempotent_but_closed_store_cannot_be_used(self):
        store = SQLiteStore(self.path)
        store.close()
        store.close()
        with self.assertRaises(StorageError):
            with store.transaction():
                pass
        with self.assertRaises(StorageError):
            with store:
                pass

    def test_closing_inside_transaction_is_rejected(self):
        with SQLiteStore(self.path) as store:
            with self.assertRaises(StorageError):
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 1})
                    store.close()
            self.assertEqual(store.revision, 0)

    def test_invalid_json_payloads_are_rejected_without_exposing_contents(self):
        invalid = [[], {1: "value"}, {"bad": (1, 2)}, {"bad": float("nan")},
                   {"bad": float("inf")}, {"bad": object()}, {"bad": "\ud800"}]
        circular = {}; circular["self"] = circular; invalid.append(circular)
        with SQLiteStore(self.path) as store:
            for state in invalid:
                with self.subTest(state_type=type(state).__name__):
                    with self.assertRaises(StorageError):
                        with store.transaction(write=True) as transaction:
                            transaction.save(state)
                    self.assertEqual(store.revision, 0)

    def test_checksum_corruption_is_not_silently_overwritten(self):
        self._save({"count": 1})
        self._replace_payload('{"count":2}', checksum="invalid")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(StorageError, "checksum"):
            SQLiteStore(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_json_corruption_is_rejected_even_with_matching_checksum(self):
        for payload in ('{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
                        '{"x":1,"x":2}', '[1,2]', '{invalid'):
            with self.subTest(payload=payload):
                if not self.path.exists():
                    self._save({"count": 1})
                self._replace_payload(payload)
                with self.assertRaises(StorageError):
                    SQLiteStore(self.path)

    def test_unsupported_version_and_unrelated_database_are_rejected(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        with self.assertRaises(StorageVersionError):
            SQLiteStore(self.path)
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE unrelated")
            connection.execute("PRAGMA user_version = 999")
        with self.assertRaises(StorageVersionError):
            SQLiteStore(self.path)

    def test_missing_or_altered_schema_is_not_reinitialized(self):
        self._save({"count": 1})
        with sqlite3.connect(self.path) as connection:
            connection.execute("ALTER TABLE relationship_state ADD COLUMN extra TEXT")
        with self.assertRaises(StorageError):
            SQLiteStore(self.path)
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE relationship_state")
        with self.assertRaises(StorageError):
            SQLiteStore(self.path)

    def test_existing_connection_rechecks_external_corruption(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as store:
            self._replace_payload('{"count":2}', checksum="wrong")
            with self.assertRaises(StorageError):
                with store.transaction(write=True) as transaction:
                    transaction.save({"count": 3})

    def test_deleted_checkpoint_is_not_mistaken_for_a_new_store(self):
        self._save({"pending_issue": "must remain visible"})
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM relationship_state")
        with self.assertRaisesRegex(StorageError, "missing"):
            SQLiteStore(self.path)

    def test_missing_initialization_marker_is_rejected(self):
        self._save({"count": 1})
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA application_id = 0")
        with self.assertRaisesRegex(StorageError, "marker"):
            SQLiteStore(self.path)

    def test_busy_writer_times_out_without_overwriting(self):
        self._save({"count": 1})
        with SQLiteStore(self.path) as first, SQLiteStore(self.path, timeout=0.01) as second:
            with first.transaction(write=True) as transaction:
                transaction.save({"count": 2})
                with self.assertRaisesRegex(StorageError, "busy"):
                    with second.transaction(write=True):
                        pass
            with second.transaction() as transaction:
                self.assertEqual(transaction.load(), {"count": 2})

    def test_processes_initialize_and_increment_without_lost_updates(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [context.Process(target=_increment_worker,
                                     args=(str(self.path), start, results, 12)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=15)
                self.assertFalse(process.is_alive(), "writer failed to finish")
                self.assertEqual(process.exitcode, 0)
            self.assertEqual([results.get(timeout=2) for _ in processes], [None, None])
            self.assertEqual(self._load(), ({"count": 24}, 24))
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate(); process.join(timeout=3)
            results.close()

    def test_process_exit_before_commit_preserves_last_committed_checkpoint(self):
        self._save({"count": 1})
        code = """
import os, sys
from agent_relationship.storage import SQLiteStore
with SQLiteStore(sys.argv[1]) as store:
    with store.transaction(write=True) as transaction:
        transaction.save({'count': 999})
        os._exit(23)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self._load(), ({"count": 1}, 1))

    def test_invalid_database_errors_do_not_echo_file_content_or_path(self):
        self.path.write_bytes(b"private-invalid-database-content")
        with self.assertRaises(StorageError) as caught:
            SQLiteStore(self.path)
        self.assertNotIn("private-invalid", str(caught.exception))
        self.assertNotIn(str(self.path), str(caught.exception))

    def test_path_and_timeout_contract(self):
        for path in (None, "", "  ", ":memory:", "file:state?mode=memory", b"state", "bad\x00path"):
            with self.subTest(path_type=type(path).__name__):
                with self.assertRaises(ValueError):
                    SQLiteStore(path)
        for timeout in (-1, True, float("nan"), float("inf"), 10**1000, "1"):
            with self.assertRaises(ValueError):
                SQLiteStore(self.path, timeout=timeout)


if __name__ == "__main__":
    unittest.main()
