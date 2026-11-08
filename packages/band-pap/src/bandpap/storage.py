"""Transactional PAP state. Callers supply authenticated, authorized scope inputs."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from bandpap.discovery import Issuer
from bandpap.wire import (
    ConversationResponse,
    ErrorBody,
    Event,
    EventPage,
    Operation,
    SendMessage,
    parse_event,
)


class StateConflict(ValueError):
    """A retry or compare-and-set conflicts with already persisted state."""


class StateUnavailable(ValueError):
    """State is absent, revoked, or outside the supplied scope."""


class StateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Scope(StateModel):
    tenant: str = Field(min_length=1)
    user: str = Field(min_length=1)
    client: str = Field(min_length=1)
    issuer: Issuer
    direction: Literal["inbound", "outbound"]

    def key(self) -> str:
        return canonical(self.model_dump())


class Session(StateModel):
    scope: Scope
    session_id: str = Field(min_length=1)
    account: str | None = Field(default=None, min_length=1)


class Route(StateModel):
    room: str = Field(min_length=1)
    proxy: str = Field(min_length=1)
    source: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    grant: str = Field(min_length=1)


class Conversation(StateModel):
    binding: str
    connection: str
    remote_id: str | None
    account: str | None
    cursor: str | None
    gap: bool


class RetryFailure(StateModel):
    status_code: int = Field(ge=400, le=599, strict=True)
    retry_at: float = Field(allow_inf_nan=False)
    error: ErrorBody | None = None


class Delivery(StateModel):
    message_id: str
    binding: str
    payload: dict[str, Any]
    state: Literal["ready", "leased", "uncertain", "delivered"]
    lease: str | None = None
    response: dict[str, Any] | None = None
    status_code: int | None = None
    failure: RetryFailure | None = None


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    scope TEXT NOT NULL, id TEXT NOT NULL, route TEXT NOT NULL,
    session TEXT NOT NULL, account TEXT, revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(scope, id)
);
CREATE TABLE IF NOT EXISTS conversations (
    scope TEXT NOT NULL, binding TEXT NOT NULL, connection TEXT NOT NULL,
    remote_id TEXT, account TEXT, cursor TEXT, gap INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(scope, binding), UNIQUE(scope, remote_id),
    FOREIGN KEY(scope, connection) REFERENCES connections(scope, id)
);
CREATE TABLE IF NOT EXISTS deliveries (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, id TEXT NOT NULL, binding TEXT NOT NULL, payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'ready', lease TEXT, lease_until REAL,
    response TEXT, status_code INTEGER, failure TEXT, available_at REAL NOT NULL DEFAULT 0,
    UNIQUE(scope, id),
    FOREIGN KEY(scope, binding) REFERENCES conversations(scope, binding)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE(scope, binding, id),
    FOREIGN KEY(scope, binding) REFERENCES conversations(scope, binding)
);
CREATE TABLE IF NOT EXISTS operations (
    scope TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, account TEXT,
    revision INTEGER NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(scope, id)
);
CREATE TABLE IF NOT EXISTS revisions (
    scope TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, terms TEXT NOT NULL,
    PRIMARY KEY(scope, id, revision),
    FOREIGN KEY(scope, id) REFERENCES operations(scope, id)
);
"""


class StateStore:
    """Synchronous local transactions; never hold one across network I/O.

    The host must protect the database and serialize calls on each handle.
    Independent handles/processes coordinate through SQLite transactions.
    Stored grants are references, not authentication or approval evidence.
    """

    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            self.db.close()
            raise StateConflict("Unsupported PAP store schema")
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA user_version=1")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Generator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def connect(self, session: Session, connection: str, route: Route) -> None:
        with self.transaction():
            previous = self.db.execute(
                "SELECT * FROM connections WHERE scope=? AND id=?",
                (session.scope.key(), connection),
            ).fetchone()
            route_json = canonical(route.model_dump())
            if previous is not None and (
                previous["route"] != route_json or previous["revoked"]
            ):
                raise StateConflict(
                    "A connection cannot change routing or revive a revoked grant"
                )
            self.db.execute(
                """INSERT INTO connections(scope,id,route,session,account) VALUES(?,?,?,?,?)
                ON CONFLICT(scope,id) DO UPDATE SET session=excluded.session, account=excluded.account""",
                (
                    session.scope.key(),
                    connection,
                    route_json,
                    session.session_id,
                    session.account,
                ),
            )

    def revoke(self, scope: Scope, connection: str) -> None:
        with self.transaction():
            self.db.execute(
                "UPDATE connections SET revoked=1 WHERE scope=? AND id=?",
                (scope.key(), connection),
            )

    def route(self, scope: Scope, connection: str) -> Route:
        return Route.model_validate_json(self.connection(scope, connection)["route"])

    def connection(self, scope: Scope, connection: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM connections WHERE scope=? AND id=? AND revoked=0",
            (scope.key(), connection),
        ).fetchone()
        if row is None:
            raise StateUnavailable("Connection unavailable")
        return row

    def create_conversation(
        self,
        session: Session,
        *,
        binding: str,
        connection: str,
        remote_id: str | None = None,
    ) -> Conversation:
        with self.transaction():
            self.connection(session.scope, connection)
            previous = self.db.execute(
                "SELECT * FROM conversations WHERE scope=? AND binding=?",
                (session.scope.key(), binding),
            ).fetchone()
            if previous is not None:
                if (
                    previous["connection"] != connection
                    or previous["remote_id"] != remote_id
                ):
                    raise StateConflict("Conversation binding already exists")
                return self.conversation(session, binding)
            try:
                self.db.execute(
                    "INSERT INTO conversations(scope,binding,connection,remote_id,account) VALUES(?,?,?,?,?)",
                    (
                        session.scope.key(),
                        binding,
                        connection,
                        remote_id,
                        session.account,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StateConflict("Remote conversation already bound") from error
            return self.conversation(session, binding)

    def conversation(self, session: Session, binding: str) -> Conversation:
        row = self.db.execute(
            "SELECT * FROM conversations WHERE scope=? AND binding=?",
            (session.scope.key(), binding),
        ).fetchone()
        if row is None:
            raise StateUnavailable("Conversation unavailable")
        self.connection(session.scope, row["connection"])
        if row["account"] is not None and row["account"] != session.account:
            raise StateUnavailable("Conversation requires its bound account")
        return Conversation(
            binding=row["binding"],
            connection=row["connection"],
            remote_id=row["remote_id"],
            account=row["account"],
            cursor=row["cursor"],
            gap=bool(row["gap"]),
        )

    def bind_account(self, session: Session, binding: str) -> None:
        if session.account is None:
            raise StateConflict("Account use requires a signed-in session")
        with self.transaction():
            self.conversation(session, binding)
            self.db.execute(
                "UPDATE conversations SET account=? WHERE scope=? AND binding=?",
                (session.account, session.scope.key(), binding),
            )

    def enqueue(self, session: Session, binding: str, request: SendMessage) -> Delivery:
        payload = canonical(request.wire())
        with self.transaction():
            self.conversation(session, binding)
            previous = self.db.execute(
                "SELECT * FROM deliveries WHERE scope=? AND id=?",
                (session.scope.key(), request.message.id),
            ).fetchone()
            if previous is not None:
                if previous["payload"] != payload:
                    raise StateConflict("message_id_conflict")
                self.conversation(session, previous["binding"])
                return self.delivery(previous)
            self.db.execute(
                "INSERT INTO deliveries(scope,id,binding,payload) VALUES(?,?,?,?)",
                (session.scope.key(), request.message.id, binding, payload),
            )
            return self.get_delivery(session, request.message.id)

    def get_delivery(self, session: Session, message_id: str) -> Delivery:
        row = self.db.execute(
            "SELECT * FROM deliveries WHERE scope=? AND id=?",
            (session.scope.key(), message_id),
        ).fetchone()
        if row is None:
            raise StateUnavailable("Delivery unavailable")
        self.conversation(session, row["binding"])
        return self.delivery(row)

    @staticmethod
    def delivery(row: sqlite3.Row) -> Delivery:
        return Delivery(
            message_id=row["id"],
            binding=row["binding"],
            payload=json.loads(row["payload"]),
            state=row["state"],
            lease=row["lease"],
            response=json.loads(row["response"]) if row["response"] else None,
            status_code=row["status_code"],
            failure=RetryFailure.model_validate_json(row["failure"])
            if row["failure"]
            else None,
        )

    def claim(
        self, session: Session, binding: str, *, now: float, lease_seconds: float
    ) -> Delivery | None:
        if (
            not math.isfinite(now)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
            or not math.isfinite(now + lease_seconds)
        ):
            raise ValueError("Lease duration must be positive")
        with self.transaction():
            self.conversation(session, binding)
            row = self.db.execute(
                """SELECT * FROM deliveries WHERE scope=? AND binding=? AND state!='delivered'
                ORDER BY sequence LIMIT 1""",
                (session.scope.key(), binding),
            ).fetchone()
            if (
                row is None
                or row["available_at"] > now
                or (row["state"] == "leased" and row["lease_until"] > now)
            ):
                return None
            lease = uuid4().hex
            self.db.execute(
                "UPDATE deliveries SET state='leased',lease=?,lease_until=? WHERE sequence=?",
                (lease, now + lease_seconds, row["sequence"]),
            )
            return self.get_delivery(session, row["id"])

    def settle(
        self,
        session: Session,
        message_id: str,
        *,
        lease: str,
        now: float,
        response: dict[str, Any] | None = None,
        status_code: int | None = None,
        remote_id: str | None = None,
        failure: RetryFailure | None = None,
    ) -> None:
        if failure is not None and (
            response is not None or status_code is not None or failure.retry_at < now
        ):
            raise ValueError(
                "Retry failure requires a future retry time and no accepted response"
            )
        if response is not None:
            accepted = ConversationResponse.model_validate(response)
            if remote_id is not None and remote_id != accepted.conversation_id:
                raise StateConflict(
                    "Accepted response disagrees with remote conversation"
                )
            remote_id = accepted.conversation_id
        if not math.isfinite(now):
            raise ValueError("Settlement time must be finite")
        if status_code is not None and status_code not in {201, 202}:
            raise ValueError("Only accepted PAP messages can settle delivery")
        if (response is None) != (status_code is None):
            raise ValueError("Response and status code must be recorded together")
        with self.transaction():
            delivery = self.get_delivery(session, message_id)
            row = self.db.execute(
                "SELECT lease_until FROM deliveries WHERE scope=? AND id=?",
                (session.scope.key(), message_id),
            ).fetchone()
            if (
                delivery.state != "leased"
                or delivery.lease != lease
                or row["lease_until"] <= now
            ):
                raise StateConflict("Expired or superseded delivery lease")
            conversation = self.conversation(session, delivery.binding)
            if remote_id is not None:
                if conversation.remote_id not in {None, remote_id}:
                    raise StateConflict("Remote conversation cannot change")
                self.db.execute(
                    "UPDATE conversations SET remote_id=? WHERE scope=? AND binding=?",
                    (remote_id, session.scope.key(), delivery.binding),
                )
            self.db.execute(
                """UPDATE deliveries SET state=?,response=?,status_code=?,failure=?,available_at=?,lease=NULL,lease_until=NULL
                WHERE scope=? AND id=?""",
                (
                    "delivered"
                    if response is not None
                    else ("ready" if failure else "uncertain"),
                    canonical(response) if response is not None else None,
                    status_code,
                    failure.model_dump_json() if failure else None,
                    failure.retry_at if failure else now,
                    session.scope.key(),
                    message_id,
                ),
            )

    def ingest(
        self,
        session: Session,
        binding: str,
        page: EventPage,
        *,
        expected_cursor: str | None,
    ) -> None:
        with self.transaction():
            conversation = self.conversation(session, binding)
            if conversation.remote_id != page.conversation_id:
                raise StateConflict("Event page belongs to another conversation")
            if conversation.cursor != expected_cursor:
                raise StateConflict("Cursor changed concurrently")
            if not page.events and page.cursor != expected_cursor:
                raise StateConflict("Empty page cannot advance the cursor")
            for event in page.events:
                payload = canonical(event.wire())
                previous = self.db.execute(
                    "SELECT payload FROM events WHERE scope=? AND binding=? AND id=?",
                    (session.scope.key(), binding, event.id),
                ).fetchone()
                if previous is not None:
                    if previous["payload"] != payload:
                        raise StateConflict("Event ID changed content")
                    continue
                self.db.execute(
                    "INSERT INTO events(scope,binding,id,payload) VALUES(?,?,?,?)",
                    (session.scope.key(), binding, event.id, payload),
                )
            self.db.execute(
                "UPDATE conversations SET cursor=? WHERE scope=? AND binding=?",
                (page.cursor, session.scope.key(), binding),
            )

    def events(self, session: Session, binding: str) -> list[Event]:
        self.conversation(session, binding)
        return [
            parse_event(json.loads(row[0]))
            for row in self.db.execute(
                "SELECT payload FROM events WHERE scope=? AND binding=? ORDER BY sequence",
                (session.scope.key(), binding),
            )
        ]

    def cursor_expired(
        self, session: Session, binding: str, *, expected_cursor: str
    ) -> None:
        with self.transaction():
            if self.conversation(session, binding).cursor != expected_cursor:
                raise StateConflict("Cursor changed concurrently")
            self.db.execute(
                "UPDATE conversations SET cursor=NULL,gap=1 WHERE scope=? AND binding=?",
                (session.scope.key(), binding),
            )

    def operation(self, session: Session, operation_id: str) -> Operation:
        row = self.db.execute(
            "SELECT * FROM operations WHERE scope=? AND id=?",
            (session.scope.key(), operation_id),
        ).fetchone()
        if (
            row is None
            or (row["account"] is None and row["session"] != session.session_id)
            or (row["account"] is not None and row["account"] != session.account)
        ):
            raise StateUnavailable("operation_not_found")
        return Operation.model_validate_json(row["payload"])

    def save_operation(
        self, session: Session, operation: Operation, *, expected: Operation | None
    ) -> None:
        """Record an authenticated observation, not an approval or execution decision."""
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM operations WHERE scope=? AND id=?",
                (session.scope.key(), operation.operation_id),
            ).fetchone()
            previous = self.operation(session, operation.operation_id) if row else None
            if previous != expected:
                raise StateConflict("Operation changed concurrently")
            terms = canonical(
                {
                    "summary": operation.summary,
                    "terms": operation.terms,
                    "expires_at": operation.wire().get("expires_at"),
                    "user_approval_required": operation.user_approval_required,
                }
            )
            if previous is not None:
                if (
                    previous.state not in {"proposed", "in_progress"}
                    and operation.state != previous.state
                ):
                    raise StateConflict("Final operation cannot change")
                if operation.revision < previous.revision:
                    raise StateConflict("Operation revision cannot go backwards")
                if previous.state != "proposed" and (
                    operation.revision != previous.revision
                    or operation.confirmation != previous.confirmation
                    or operation.state == "proposed"
                ):
                    raise StateConflict("Confirmed operation cannot be reproposed")
            revision = self.db.execute(
                "SELECT terms FROM revisions WHERE scope=? AND id=? AND revision=?",
                (session.scope.key(), operation.operation_id, operation.revision),
            ).fetchone()
            if revision is not None:
                saved_terms = json.loads(revision["terms"])
                observed_terms = json.loads(terms)
                if (
                    operation.state != "proposed"
                    and "expires_at" not in operation.model_fields_set
                ):
                    observed_terms["expires_at"] = saved_terms["expires_at"]
                if saved_terms != observed_terms:
                    raise StateConflict("Operation revision terms are immutable")
            self.db.execute(
                """INSERT INTO operations(scope,id,session,account,revision,payload) VALUES(?,?,?,?,?,?)
                ON CONFLICT(scope,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload""",
                (
                    session.scope.key(),
                    operation.operation_id,
                    session.session_id,
                    session.account,
                    operation.revision,
                    canonical(operation.wire()),
                ),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO revisions(scope,id,revision,terms) VALUES(?,?,?,?)",
                (
                    session.scope.key(),
                    operation.operation_id,
                    operation.revision,
                    terms,
                ),
            )
