"""Optional local SQLite snapshots with explicit transaction boundaries.

One JSON document holds a complete tracker checkpoint. Writers are serialized
with ``BEGIN IMMEDIATE`` so a read/modify/write operation cannot overwrite a
concurrent instance's committed state. This favors correctness and small local
workloads over high-throughput event storage. No model credentials are part of
the storage API, and no pickle or executable deserialization is used.
"""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
import sqlite3
from threading import RLock, get_ident
from typing import Dict, Optional


class StorageError(RuntimeError):
    """A checkpoint could not be safely read or committed."""


class StorageVersionError(StorageError):
    """The database is not a supported tracker storage version."""


def _database_error(exc: sqlite3.Error) -> StorageError:
    # Do not expose paths, SQL parameters or payloads from driver exceptions.
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        return StorageError("SQLite store is busy; retry after the active transaction finishes")
    return StorageError("SQLite store operation failed; the checkpoint was not accepted")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _decode(payload: str) -> Dict:
    try:
        state = json.loads(payload, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        if not isinstance(state, dict):
            raise ValueError("snapshot must be a JSON object")
        _validate_json(state)
        return state
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise StorageError("Stored checkpoint is not a valid JSON object") from None


def _validate_json(value):
    """Reject coercions such as integer object keys and tuple-to-list changes."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError("nonfinite number")
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _validate_json(item)
        return
    raise ValueError("unsupported JSON value")


class SQLiteTransaction:
    """An active transaction yielded by :meth:`SQLiteStore.transaction`."""

    def __init__(self, store, write, state, revision):
        self._store = store
        self._write = write
        self._state = state
        self._revision = revision
        self._thread = get_ident()
        self._active = True
        self._failed = False

    @property
    def revision(self) -> int:
        """Checkpoint revision loaded/saved in this transaction (zero if empty)."""
        return self._revision

    def _check_active(self):
        if not self._active or self._thread != get_ident():
            raise StorageError("Storage transaction is no longer active in this thread")

    def load(self) -> Optional[Dict]:
        self._check_active()
        return deepcopy(self._state)

    def save(self, state: Dict) -> int:
        """Stage one snapshot; durability is established only on context exit."""
        self._check_active()
        try:
            if not self._write:
                raise StorageError("Cannot save from a read-only storage transaction")
            if type(state) is not dict:
                raise StorageError("Checkpoint must be a JSON object")
            try:
                _validate_json(state)
                payload = json.dumps(state, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(",", ":"))
                encoded = payload.encode("utf-8")
                # Decode to detach the caller's data and verify round-trip safety.
                detached = _decode(payload)
            except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError):
                raise StorageError("Checkpoint contains unsupported JSON values") from None
            checksum = hashlib.sha256(encoded).hexdigest()
            revision = self._revision + 1
            if self._revision == 0:
                self._store._connection.execute(
                    "INSERT INTO relationship_state (id, revision, payload, checksum) VALUES (1, ?, ?, ?)",
                    (revision, payload, checksum),
                )
                # This marker commits with the first checkpoint. A deleted
                # checkpoint must never look like a fresh, safe-to-reset store.
                self._store._connection.execute(
                    f"PRAGMA application_id = {self._store.APPLICATION_ID}"
                )
            else:
                cursor = self._store._connection.execute(
                    "UPDATE relationship_state SET revision = ?, payload = ?, checksum = ? "
                    "WHERE id = 1 AND revision = ?",
                    (revision, payload, checksum, self._revision),
                )
                if cursor.rowcount != 1:
                    raise StorageError("Checkpoint revision changed unexpectedly")
            self._revision = revision
            self._state = detached
            return revision
        except sqlite3.Error as exc:
            self._failed = True
            raise _database_error(exc) from None
        except BaseException:
            self._failed = True
            raise


class SQLiteStore:
    """Durable JSON snapshots on a local filesystem, using only ``sqlite3``.

    A store is safe to share across threads. Separate instances/processes may
    use the same file; each mutation must load and save inside one write
    transaction. Network filesystems and distributed coordination are outside
    this contract. A transaction is intentionally not reentrant.
    """

    SCHEMA_VERSION = 1
    APPLICATION_ID = 0x41525231  # ARR1: a committed relationship checkpoint exists.

    def __init__(self, path, timeout: float = 5.0):
        try:
            filename = os.fspath(path)
        except TypeError:
            raise ValueError("storage path must be a local filesystem path") from None
        if (not isinstance(filename, str) or not filename.strip() or "\x00" in filename
                or filename == ":memory:" or filename.startswith("file:")):
            raise ValueError("storage path must name a local SQLite file, not an in-memory database or URI")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 <= timeout <= 3600 or not math.isfinite(timeout)):
            raise ValueError("storage timeout must be finite and between 0 and 3600 seconds")
        self._lock = RLock()
        self._closed = False
        self._in_transaction = False
        self._connection = None
        try:
            self._connection = sqlite3.connect(
                filename, timeout=float(timeout), isolation_level=None,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA synchronous = FULL")
            journal = self._connection.execute("PRAGMA journal_mode").fetchone()[0]
            if journal not in ("delete", "truncate", "persist", "wal"):
                raise StorageError("SQLite store requires a durable journal mode")
            self._initialize()
        except sqlite3.Error as exc:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            raise _database_error(exc) from None
        except BaseException:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            raise

    def _schema_identity(self):
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        objects = self._connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return version, objects

    def _validate_schema(self):
        version, objects = self._schema_identity()
        if version != self.SCHEMA_VERSION:
            raise StorageVersionError("Unsupported SQLite store schema version")
        if objects != [("table", "relationship_state")]:
            raise StorageError("SQLite store schema is missing or unexpected")
        columns = self._connection.execute("PRAGMA table_info(relationship_state)").fetchall()
        shape = [(c[1], c[2].upper(), c[3], c[5]) for c in columns]
        expected = [("id", "INTEGER", 0, 1), ("revision", "INTEGER", 1, 0),
                    ("payload", "TEXT", 1, 0), ("checksum", "TEXT", 1, 0)]
        if shape != expected:
            raise StorageError("SQLite store schema is incompatible")
        identity = self._connection.execute("PRAGMA application_id").fetchone()[0]
        if identity not in (0, self.APPLICATION_ID):
            raise StorageVersionError("Unsupported SQLite store identity")

    def _initialize(self):
        try:
            self._connection.execute("BEGIN")
            version, objects = self._schema_identity()
            empty = version == 0 and not objects
            if empty:
                self._connection.execute("ROLLBACK")
                self._connection.execute("BEGIN IMMEDIATE")
                # Another process may have initialized the file meanwhile.
                version, objects = self._schema_identity()
                if version == 0 and not objects:
                    self._connection.execute(
                        "CREATE TABLE relationship_state ("
                        "id INTEGER PRIMARY KEY CHECK (id = 1), "
                        "revision INTEGER NOT NULL CHECK (revision >= 1), "
                        "payload TEXT NOT NULL, checksum TEXT NOT NULL)"
                    )
                    self._connection.execute("PRAGMA user_version = 1")
            self._validate_schema()
            self._read_snapshot()
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _read_snapshot(self):
        application_id = self._connection.execute("PRAGMA application_id").fetchone()[0]
        rows = self._connection.execute(
            "SELECT id, revision, payload, checksum FROM relationship_state"
        ).fetchall()
        if not rows:
            if application_id != 0:
                raise StorageError("Previously committed checkpoint is missing")
            return None, 0
        if application_id != self.APPLICATION_ID:
            raise StorageError("Stored checkpoint initialization marker is missing")
        if len(rows) != 1:
            raise StorageError("SQLite store contains more than one checkpoint")
        identity, revision, payload, checksum = rows[0]
        if (identity != 1 or type(revision) is not int or revision < 1
                or not isinstance(payload, str) or not isinstance(checksum, str)):
            raise StorageError("Stored checkpoint metadata is invalid")
        try:
            actual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except UnicodeError:
            raise StorageError("Stored checkpoint encoding is invalid") from None
        if checksum != actual:
            raise StorageError("Stored checkpoint checksum does not match")
        return _decode(payload), revision

    def _require_open(self):
        if self._closed:
            raise StorageError("SQLite store is closed")

    @contextmanager
    def transaction(self, write: bool = False):
        if type(write) is not bool:
            raise ValueError("write must be a boolean")
        with self._lock:
            self._require_open()
            if self._in_transaction:
                raise StorageError("Nested storage transactions are not supported")
            self._in_transaction = True
            transaction = None
            try:
                self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                self._validate_schema()
                state, revision = self._read_snapshot()
                transaction = SQLiteTransaction(self, write, state, revision)
                yield transaction
                if transaction._failed:
                    raise StorageError("Storage transaction failed and cannot be committed")
                self._connection.execute("COMMIT")
            except BaseException as exc:
                if self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        self._connection.close()
                        self._closed = True
                if isinstance(exc, sqlite3.Error):
                    raise _database_error(exc) from None
                raise
            finally:
                if transaction is not None:
                    transaction._active = False
                self._in_transaction = False

    @property
    def revision(self) -> int:
        with self.transaction() as transaction:
            return transaction.revision

    def close(self):
        with self._lock:
            if self._in_transaction:
                raise StorageError("Cannot close a store inside an active transaction")
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
