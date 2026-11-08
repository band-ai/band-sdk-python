"""Host-provisioned room grants and addressed routing, independent of chat text."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import Field

from bandpap.storage import (
    Route,
    Session,
    StateConflict,
    StateModel,
    StateStore,
    StateUnavailable,
    canonical,
)
from bandpap.wire import SendMessage


class Grant(StateModel):
    id: str = Field(min_length=1)
    session: Session
    connection: str = Field(min_length=1)
    binding: str = Field(min_length=1)
    route: Route
    sender_type: Literal["Agent", "User"]
    visible_participants: tuple[str, ...] = ()


class Grants:
    """Provision only through authenticated host control, never message metadata.

    A grant selects exactly one downstream owner/client for one authenticated
    source and proxy in a room. Revoked IDs cannot be reused to revive work.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS pap_grants (
                id TEXT PRIMARY KEY, room TEXT NOT NULL, proxy TEXT NOT NULL,
                source TEXT NOT NULL, sender_type TEXT NOT NULL, payload TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pap_commands (
                grant_id TEXT NOT NULL, message_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(grant_id,message_id)
            );
        """)

    def provision(self, grant: Grant) -> None:
        if grant.id != grant.route.grant or grant.session.scope.direction != "outbound":
            raise ValueError("Grant identity or direction disagrees with route")
        if grant.route.proxy == grant.route.source:
            raise ValueError("A proxy cannot authorize its own input")
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT payload,revoked FROM pap_grants WHERE id=?", (grant.id,)
            ).fetchone()
            if old is not None:
                if old["revoked"] or Grant.model_validate_json(old["payload"]) != grant:
                    raise StateConflict("Grant cannot be rebound or revived")
                return
            scope = grant.session.scope.key()
            connection = self.store.db.execute(
                "SELECT * FROM connections WHERE scope=? AND id=?",
                (scope, grant.connection),
            ).fetchone()
            if connection is not None and (
                connection["revoked"]
                or Route.model_validate_json(connection["route"]) != grant.route
            ):
                raise StateConflict("Connection cannot change route or revive")
            conversation = self.store.db.execute(
                "SELECT * FROM conversations WHERE scope=? AND binding=?",
                (scope, grant.binding),
            ).fetchone()
            if conversation is not None and (
                conversation["connection"] != grant.connection
                or conversation["account"] != grant.session.account
            ):
                raise StateConflict(
                    "Conversation belongs to another connection or account"
                )
            self.store.db.execute(
                "INSERT OR IGNORE INTO connections(scope,id,route,session,account) VALUES(?,?,?,?,?)",
                (
                    scope,
                    grant.connection,
                    canonical(grant.route.model_dump()),
                    grant.session.session_id,
                    grant.session.account,
                ),
            )
            self.store.db.execute(
                "INSERT OR IGNORE INTO conversations(scope,binding,connection,account) VALUES(?,?,?,?)",
                (scope, grant.binding, grant.connection, grant.session.account),
            )
            self.store.db.execute(
                "INSERT INTO pap_grants(id,room,proxy,source,sender_type,payload) VALUES(?,?,?,?,?,?)",
                (
                    grant.id,
                    grant.route.room,
                    grant.route.proxy,
                    grant.route.source,
                    grant.sender_type,
                    grant.model_dump_json(),
                ),
            )

    def get(self, grant_id: str) -> Grant:
        row = self.store.db.execute(
            "SELECT payload FROM pap_grants WHERE id=? AND revoked=0 AND paused=0",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise StateUnavailable("Grant unavailable")
        grant = Grant.model_validate_json(row[0])
        self.store.connection(grant.session.scope, grant.connection)
        return grant

    def addressed(
        self,
        *,
        room: str,
        proxy: str,
        source: str,
        sender_type: str,
        metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> Grant:
        mentions = metadata.get("mentions")
        if not isinstance(mentions, list) or not any(
            isinstance(item, dict)
            and item.get("id") == proxy
            and item.get("kind", "mention") == "mention"
            for item in mentions
        ):
            raise StateUnavailable("No structured address to this proxy")
        rows = self.store.db.execute(
            "SELECT id FROM pap_grants WHERE room=? AND proxy=? AND source=? AND sender_type=? AND revoked=0 AND paused=0",
            (room, proxy, source, sender_type),
        ).fetchall()
        if message_id is not None:
            staged = [
                row
                for row in rows
                if self.store.db.execute(
                    "SELECT 1 FROM pap_commands WHERE grant_id=? AND message_id=?",
                    (row[0], message_id),
                ).fetchone()
            ]
            if staged:
                rows = staged
        if len(rows) != 1:
            raise StateUnavailable("Source needs an unambiguous connection grant")
        return self.get(rows[0][0])

    def for_proxy(self, proxy: str) -> list[Grant]:
        rows = self.store.db.execute(
            "SELECT id FROM pap_grants WHERE proxy=? AND revoked=0 AND paused=0",
            (proxy,),
        ).fetchall()
        return [self.get(row[0]) for row in rows]

    def pause_room(self, proxy: str, room: str) -> None:
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE pap_grants SET paused=1 WHERE room=? AND proxy=?", (room, proxy)
            )

    def resume(self, grant_id: str) -> None:
        """Host-authorized resumption; a message cannot resume paused work."""
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE pap_grants SET paused=0 WHERE id=? AND revoked=0", (grant_id,)
            )

    def revoke(self, grant_id: str) -> None:
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT payload FROM pap_grants WHERE id=?", (grant_id,)
            ).fetchone()
            if row is None:
                return
            grant = Grant.model_validate_json(row[0])
            self.store.db.execute(
                "UPDATE pap_grants SET revoked=1 WHERE id=?", (grant_id,)
            )
            self.store.db.execute(
                "UPDATE connections SET revoked=1 WHERE scope=? AND id=?",
                (grant.session.scope.key(), grant.connection),
            )

    def stage(self, grant_id: str, request: SendMessage) -> None:
        """Attach structured input through the trusted host, keyed by original ID."""
        self.get(grant_id)
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT payload FROM pap_commands WHERE grant_id=? AND message_id=?",
                (grant_id, request.message.id),
            ).fetchone()
            if row is not None and SendMessage.model_validate_json(row[0]) != request:
                raise StateConflict("Original structured input cannot change")
            self.store.db.execute(
                "INSERT OR IGNORE INTO pap_commands VALUES(?,?,?)",
                (grant_id, request.message.id, canonical(request.wire())),
            )

    def command(self, grant_id: str, message_id: str) -> SendMessage | None:
        self.get(grant_id)
        row = self.store.db.execute(
            "SELECT payload FROM pap_commands WHERE grant_id=? AND message_id=?",
            (grant_id, message_id),
        ).fetchone()
        return SendMessage.model_validate_json(row[0]) if row else None

    def roster(
        self, grant_id: str, participants: Iterable[dict[str, str]]
    ) -> tuple[dict[str, str], ...]:
        allowed = self.get(grant_id).visible_participants
        return tuple(
            {
                key: value
                for key, value in participant.items()
                if key in {"id", "name", "handle"}
            }
            for participant in participants
            if participant.get("id") in allowed
        )
