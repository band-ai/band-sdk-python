"""One SQLite database for the event journal, derived state, and snapshots."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

from remote_agent.capabilities import exclusive_lock
from remote_agent.hostkey import HostKey
from remote_agent.retention import anchor, retain
from remote_agent.storage import (
    StoreError,
    canonical,
    digest,
    private_directory,
    private_file,
    sync_directory,
)
from remote_agent.types import JsonObject, SourcePath


class Store:
    def __init__(
        self,
        path: SourcePath,
        *,
        max_records: int = 100_000,
        max_record_bytes: int = 1_048_576,
        max_database_bytes: int = 67_108_864,
        durable: bool = True,
    ) -> None:
        supplied = Path(path).absolute()
        if type(durable) is not bool:
            raise StoreError("durable must be a boolean")
        self.durable = durable
        self.path = (
            private_directory(supplied.parent) / supplied.name if durable else supplied
        )
        self.max_records = max_records
        self.max_record_bytes = max_record_bytes
        self.max_database_bytes = max_database_bytes
        if any(
            type(n) is not int or n <= 0
            for n in (max_records, max_record_bytes, max_database_bytes)
        ):
            raise StoreError("Invalid storage limits")
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | None = None
        self.owner = None
        self._ephemeral_host_key: bytes | None = None
        try:
            if self.durable:
                self.owner = os.fdopen(private_file(str(self.path) + ".owner"), "a+b")
                try:
                    exclusive_lock(self.owner.fileno(), nonblocking=True)
                except BlockingIOError as error:
                    self.owner.close()
                    self.owner = None
                    raise StoreError(
                        "The database already has a controller owner"
                    ) from error
                os.close(private_file(self.path))
                for suffix in ("-wal", "-shm", "-journal"):
                    companion = Path(str(self.path) + suffix)
                    if companion.exists() or companion.is_symlink():
                        os.close(private_file(companion, create=False))
            self.db = sqlite3.connect(
                self.path if self.durable else ":memory:",
                isolation_level=None,
                check_same_thread=False,
            )
            self.db.row_factory = sqlite3.Row
            self.connection.execute(
                "PRAGMA journal_mode=" + ("WAL" if self.durable else "MEMORY")
            )
            self.connection.execute("PRAGMA temp_store=MEMORY")
            self.connection.execute("PRAGMA secure_delete=ON")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA wal_autocheckpoint=64")
            self.connection.execute("PRAGMA journal_size_limit=0")
            self.connection.execute("PRAGMA foreign_keys=ON")
            page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
            self.connection.execute(
                f"PRAGMA max_page_count={max(16, max_database_bytes // page_size)}"
            )
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StoreError("Unsupported database schema")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS journal (
                    sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                    data TEXT NOT NULL, changes TEXT NOT NULL,
                    previous TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    sequence INTEGER PRIMARY KEY REFERENCES journal(sequence),
                    head TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StoreError("SQLite integrity check failed")
            self._verify()
            self._verify_snapshots()
        except Exception:
            self.close()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        if self.db is None:
            raise StoreError("The database is closed")
        return self.db

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None
            if self.owner is not None:
                self.owner.close()
                self.owner = None

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.connection.execute("COMMIT")
            except BaseException:
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise

    def commit(
        self,
        kind: str,
        data: object,
        *,
        updates: JsonObject | None = None,
        snapshot: JsonObject | None = None,
    ) -> JsonObject:
        updates = updates or {}
        try:
            with self.transaction():
                frame = self._append(kind, data, updates)
                for key, value in updates.items():
                    self.connection.execute(
                        "INSERT OR REPLACE INTO current_state VALUES (?,?)",
                        (key, canonical(value).decode()),
                    )
                if snapshot is not None:
                    self._snapshot(frame, snapshot)
                return frame
        except sqlite3.DatabaseError as error:
            raise StoreError(f"SQLite commit failed: {error}") from error

    def _append(
        self, kind: str, data: object, updates: JsonObject | None = None
    ) -> JsonObject:
        updates = updates or {}
        if (
            not isinstance(kind, str)
            or not kind
            or len(kind) > 100
            or not isinstance(updates, dict)
        ):
            raise StoreError("Invalid journal entry")
        encoded = canonical(data)
        changed = canonical(updates)
        if len(encoded) + len(changed) > self.max_record_bytes:
            raise StoreError("Journal record capacity exceeded")
        row = self.connection.execute(
            "SELECT sequence,hash FROM journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence, previous = (row[0] + 1, row[1]) if row else (1, "0" * 64)
        if (
            self.connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
            >= self.max_records
        ):
            raise StoreError("Journal capacity exceeded")
        frame = {
            "sequence": sequence,
            "kind": kind,
            "data": data,
            "updates": updates,
            "previous": previous,
        }
        frame["hash"] = digest(frame)
        self.connection.execute(
            "INSERT INTO journal VALUES (?,?,?,?,?,?)",
            (
                sequence,
                kind,
                encoded.decode(),
                changed.decode(),
                previous,
                frame["hash"],
            ),
        )
        return frame

    def _snapshot(self, frame: JsonObject, snapshot: JsonObject) -> None:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "program",
            "schema",
            "state",
        }:
            raise StoreError("Invalid snapshot")
        encoded = canonical(snapshot)
        if len(encoded) > self.max_record_bytes:
            raise StoreError("Snapshot capacity exceeded")
        checksum = digest(
            {"sequence": frame["sequence"], "head": frame["hash"], "data": snapshot}
        )
        self.connection.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?)",
            (frame["sequence"], frame["hash"], encoded.decode(), checksum),
        )

    def records(self, *, after: int = 0, limit: int | None = None) -> list[JsonObject]:
        if (
            type(after) is not int
            or after < 0
            or (limit is not None and (type(limit) is not int or limit < 1))
        ):
            raise StoreError("Invalid journal page")
        with self.lock:
            return [
                self._decode(row)
                for row in self.connection.execute(
                    "SELECT * FROM journal WHERE sequence>? ORDER BY sequence LIMIT ?",
                    (after, limit or -1),
                )
            ]

    def tail_records(self, *, limit: int = 30) -> tuple[list[JsonObject], int]:
        """Newest records in journal order and the retained count from the same lock."""
        if type(limit) is not int or limit < 1:
            raise StoreError("Invalid journal tail limit")
        with self.lock:
            count = self.connection.execute("SELECT COUNT(*) FROM journal").fetchone()[
                0
            ]
            records = [
                self._decode(row)
                for row in self.connection.execute(
                    "SELECT * FROM journal ORDER BY sequence DESC LIMIT ?", (limit,)
                )
            ]
            return records[::-1], count

    def search(
        self,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int = 50,
        kind: str | None = None,
        query: str = "",
    ) -> JsonObject:
        """Literal, case-sensitive search with stable head and bounded pages.

        Page cursors are journal sequences, not offsets. New arrivals cannot move a
        page pinned to ``through``. This reads at most one response page plus one
        lookahead record and never interpolates caller text into SQL.
        """
        valid_sequence = lambda n: type(n) is int and 0 <= n <= 9_007_199_254_740_991
        if (
            not valid_sequence(after)
            or (through is not None and not valid_sequence(through))
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (
                kind is not None
                and (not isinstance(kind, str) or not 1 <= len(kind) <= 100)
            )
            or not isinstance(query, str)
        ):
            raise StoreError("Invalid journal search page")
        try:
            if len(query.encode("utf-8")) > 256:
                raise StoreError("Journal search text exceeds 256 bytes")
        except UnicodeError as error:
            raise StoreError("Invalid journal search encoding") from error
        with self.lock:
            head = self.journal_head()["sequence"]
            through = head if through is None else through
            if through > head or after > through:
                raise StoreError("Journal search cursor is outside its retained head")
            anchor = self.anchor()
            if anchor and through < anchor["sequence"]:
                raise StoreError(
                    "Journal search head has expired from the retained window"
                )
            clauses, values = ["sequence>?", "sequence<=?"], [after, through]
            if kind is not None:
                clauses.append("kind=?")
                values.append(kind)
            if query:
                clauses.append(
                    "(instr(kind,?)>0 OR instr(data,?)>0 OR instr(changes,?)>0)"
                )
                values.extend([query] * 3)
            sql = (
                "SELECT * FROM journal WHERE "
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?"
            )
            records, size, has_more = [], 0, False
            for row in self.connection.execute(sql, [*values, limit + 1]):
                frame = self._decode(row)
                frame_size = len(canonical(frame))
                if frame_size > 2_097_152:
                    raise StoreError("Journal record exceeds search response capacity")
                if len(records) == limit or size + frame_size > 2_097_152:
                    has_more = True
                    break
                records.append(frame)
                size += frame_size
            return {
                "profile": "remote-agent/journal-page/1",
                "records": records,
                "cursor": records[-1]["sequence"] if records else after,
                "through": through,
                "has_more": has_more,
                "history_truncated": anchor is not None and after < anchor["sequence"],
                "earliest_sequence": anchor["sequence"] + 1 if anchor else 1,
            }

    def journal_head(self) -> JsonObject:
        with self.lock:
            row = self.connection.execute(
                "SELECT sequence,hash FROM journal ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            return {
                "sequence": row[0] if row else 0,
                "hash": row[1] if row else "0" * 64,
            }

    def metrics(self) -> JsonObject:
        """Bounded counters only; no journal content or arbitrary state leaves here."""
        with self.lock:
            records = self.connection.execute(
                "SELECT COUNT(*) FROM journal"
            ).fetchone()[0]
            page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
            database_bytes = (
                self.connection.execute("PRAGMA page_count").fetchone()[0] * page_size
            )
            free_bytes = (
                self.connection.execute("PRAGMA freelist_count").fetchone()[0]
                * page_size
            )
            record_limit, database_limit = (
                getattr(self, "max_records", None),
                getattr(self, "max_database_bytes", None),
            )
            record_percent = records * 100 // record_limit if record_limit else None
            database_percent = (
                database_bytes * 100 // database_limit if database_limit else None
            )
            try:
                wal_bytes = (
                    Path(str(self.path) + "-wal").stat().st_size if self.durable else 0
                )
            except FileNotFoundError:
                wal_bytes = 0
            pressure = max(record_percent or 0, database_percent or 0)
            return {
                "durable": self.durable,
                "records": records,
                "record_limit": record_limit,
                "record_percent": record_percent,
                "database_bytes": database_bytes,
                "database_limit_bytes": database_limit,
                "database_percent": database_percent,
                "free_bytes": free_bytes,
                "wal_bytes": wal_bytes,
                "pressure": (
                    "unknown"
                    if record_limit is None and database_limit is None
                    else "critical"
                    if pressure >= 95
                    else "warning"
                    if pressure >= 80
                    else "normal"
                ),
            }

    @staticmethod
    def _decode(row: sqlite3.Row) -> JsonObject:
        try:
            return {
                "sequence": row["sequence"],
                "kind": row["kind"],
                "data": json.loads(row["data"]),
                "updates": json.loads(row["changes"]),
                "previous": row["previous"],
                "hash": row["hash"],
            }
        except (ValueError, TypeError) as error:
            raise StoreError("Invalid journal encoding") from error

    def anchor(self) -> JsonObject | None:
        with self.lock:
            return anchor(self)

    def retain(
        self, *, target_bytes: int = 100_000_000, minimum_steps: int = 100
    ) -> JsonObject:
        result = retain(self, target_bytes=target_bytes, minimum_steps=minimum_steps)
        if result["pruned"] and self.durable:
            # Freed pages are reusable. Reclaim them after the atomic logical
            # cut; a busy external reader may defer physical reclamation.
            try:
                with self.lock:
                    self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self.connection.execute("VACUUM")
                    self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                result["reclaimed"] = True
            except sqlite3.DatabaseError:
                result["reclaimed"] = False
        return result

    def _verify(self) -> None:
        anchor = self.anchor()
        previous, expected, state = (
            (anchor["head"], anchor["sequence"] + 1, dict(anchor["state"]))
            if anchor
            else ("0" * 64, 1, {})
        )
        for row in self.connection.execute("SELECT * FROM journal ORDER BY sequence"):
            frame = self._decode(row)
            checksum = frame.pop("hash")
            if (
                frame["sequence"] != expected
                or frame["previous"] != previous
                or digest(frame) != checksum
            ):
                raise StoreError("Corrupt journal sequence or hash")
            previous, expected = checksum, expected + 1
            state.update(frame["updates"])
        actual = {
            row[0]: json.loads(row[1])
            for row in self.connection.execute("SELECT key,value FROM current_state")
        }
        if actual != state:
            raise StoreError("Current state diverges from the journal")

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM current_state WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else default

    def prefixed(self, prefix: str) -> dict[str, Any]:
        """Current values whose keys start with a nonempty ``prefix``, in key order.

        Keys are returned without the prefix.
        """
        if not prefix:
            raise StoreError("A state key prefix is required")
        end = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        with self.lock:
            rows = self.connection.execute(
                "SELECT key,value FROM current_state WHERE key>=? AND key<? ORDER BY key",
                (prefix, end),
            ).fetchall()
            return {key[len(prefix) :]: json.loads(value) for key, value in rows}

    def latest_snapshot(self) -> JsonObject | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM snapshots ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                anchor = self.anchor()
                return anchor["snapshot"] if anchor else None
            return self._decode_snapshot(row)

    def _decode_snapshot(self, row: sqlite3.Row) -> JsonObject:
        try:
            data = json.loads(row["data"])
            anchor = self.connection.execute(
                "SELECT hash FROM journal WHERE sequence=?", (row["sequence"],)
            ).fetchone()
            core = {"sequence": row["sequence"], "head": row["head"], "data": data}
            if not anchor or anchor[0] != row["head"] or digest(core) != row["hash"]:
                raise StoreError("Corrupt snapshot or journal anchor")
            return dict(data, sequence=row["sequence"], head=row["head"])
        except (ValueError, TypeError) as error:
            raise StoreError("Invalid snapshot encoding") from error

    def _verify_snapshots(self) -> None:
        for row in self.connection.execute("SELECT * FROM snapshots ORDER BY sequence"):
            self._decode_snapshot(row)

    @staticmethod
    def _backup_key(connection: sqlite3.Connection) -> bytes | None:
        """Backup-only credential extension; never accessed by journal export."""
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='private_host_key'"
        ).fetchone():
            return None
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(private_host_key)")
        ]
        rows = connection.execute("SELECT * FROM private_host_key").fetchmany(2)
        if (
            columns != ["singleton", "secret"]
            or len(rows) != 1
            or rows[0][0] != 1
            or not isinstance(rows[0][1], bytes)
            or len(rows[0][1]) != 32
        ):
            raise StoreError("Invalid backup host ID credential")
        return rows[0][1]

    @classmethod
    def inspect_backup(cls, path: SourcePath) -> JsonObject:
        """Read-only validation of every record and snapshot in a standalone backup."""
        path = Path(path).absolute()
        if path.is_symlink() or not path.is_file():
            raise StoreError("Backup must be an existing regular file")
        try:
            connection = sqlite3.connect(
                path.as_uri() + "?mode=ro", uri=True, isolation_level=None
            )
            try:
                connection.row_factory = sqlite3.Row
                if (
                    connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA journal_mode").fetchone()[0]
                    != "delete"
                ):
                    raise StoreError(
                        "Backup integrity/schema/journal mode check failed"
                    )
                verifier = cls.__new__(cls)
                verifier.db = connection
                verifier.lock = threading.RLock()
                verifier._verify()
                verifier._verify_snapshots()
                cls._backup_key(connection)
                row = connection.execute(
                    "SELECT sequence,hash FROM journal ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                return {
                    "records": row[0] if row else 0,
                    "head": row[1] if row else "0" * 64,
                    "snapshots": connection.execute(
                        "SELECT count(*) FROM snapshots"
                    ).fetchone()[0],
                }
            finally:
                connection.close()
        except (OSError, sqlite3.DatabaseError, ValueError) as error:
            raise StoreError("Invalid backup") from error

    @classmethod
    def _copy_verified(
        cls,
        source: sqlite3.Connection,
        destination: SourcePath,
        *,
        host_key: bytes | None = None,
        restore_credentials: bool = False,
    ) -> JsonObject:
        destination = Path(destination).absolute()
        directory = private_directory(destination.parent)
        destination = directory / destination.name
        if destination.exists() or destination.is_symlink():
            raise StoreError("Destination already exists")
        for suffix in ("-wal", "-shm", "-journal"):
            companion = Path(str(destination) + suffix)
            if companion.exists() or companion.is_symlink():
                raise StoreError(
                    "Destination has stale SQLite companions; use a fresh destination"
                )
        descriptor, temporary = tempfile.mkstemp(
            prefix=".database-copy-", dir=directory
        )
        os.close(descriptor)
        temporary = Path(temporary)
        restored_key = None
        installed_key = None
        published = False
        try:
            copy = sqlite3.connect(temporary, isolation_level=None)
            try:
                source.backup(copy)
                copy.execute("PRAGMA journal_mode=DELETE")
                if host_key is not None:
                    copy.execute("DROP TABLE IF EXISTS private_host_key")
                    copy.execute(
                        "CREATE TABLE private_host_key (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                        "secret BLOB NOT NULL CHECK(length(secret)=32))"
                    )
                    copy.execute(
                        "INSERT INTO private_host_key VALUES(1,?)", (host_key,)
                    )
                if restore_credentials:
                    restored_key = cls._backup_key(copy)
                    if restored_key is not None:
                        copy.execute("PRAGMA secure_delete=ON")
                        copy.execute("DROP TABLE private_host_key")
                        copy.execute("VACUUM")
            finally:
                copy.close()
            result = cls.inspect_backup(temporary)
            with temporary.open("rb") as completed:
                os.fsync(completed.fileno())
            if restored_key is not None:
                pending_key = HostKey(SimpleNamespace(path=destination))
                pending_key.create(restored_key)
                installed_key = pending_key
            # A hard-link publication is atomic and refuses a raced existing destination.
            os.link(temporary, destination)
            published = True
            temporary.unlink()
            sync_directory(directory)
            return result
        except (OSError, sqlite3.DatabaseError) as error:
            raise StoreError("Database copy failed") from error
        finally:
            temporary.unlink(missing_ok=True)
            if installed_key is not None and not published:
                installed_key.path.unlink(missing_ok=True)
                sync_directory(directory)

    @classmethod
    def restore(cls, source: SourcePath, destination: SourcePath) -> JsonObject:
        cls.inspect_backup(source)
        connection = sqlite3.connect(
            Path(source).absolute().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
        )
        try:
            host = HostKey(SimpleNamespace(path=Path(destination).absolute()))
            with host.locked():
                if host.path.exists() or host.path.is_symlink():
                    raise StoreError("Destination host ID key already exists")
                return cls._copy_verified(
                    connection, destination, restore_credentials=True
                )
        finally:
            connection.close()

    def backup(self, destination: SourcePath) -> JsonObject:
        if not self.durable:
            raise StoreError("Ephemeral execution state cannot be backed up")
        host = HostKey(self)
        with host.locked():
            key = host.read()
            with self.lock:
                return self._copy_verified(self.connection, destination, host_key=key)
