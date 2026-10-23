"""Independent durable conversation pumps; no Band turn is held during network work."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Literal
from urllib.parse import urlencode

from pydantic import ValidationError

from bandpap.auth import AuthError
from bandpap.http import ProtocolError
from bandpap.oauth import OAuthClient
from bandpap.room import RoomDelivery, RoomOutbox
from bandpap.routing import Grant, Grants
from bandpap.storage import (
    RetryFailure,
    Session,
    StateConflict,
    StateUnavailable,
)
from bandpap.wire import (
    ControlResponse,
    ConversationResponse,
    ErrorBody,
    EventPage,
    Message,
    MessageEvent,
    SendMessage,
)

logger = logging.getLogger(__name__)


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Connection:
    oauth: OAuthClient
    current_session: Callable[[], Awaitable[Session]]


class Connections:
    def __init__(self) -> None:
        self.entries: dict[str, Connection] = {}

    def add(self, grant: Grant, connection: Connection) -> None:
        if (
            connection.oauth.scope != grant.session.scope
            or connection.oauth.connection != grant.connection
        ):
            raise ValueError("OAuth connection does not belong to this grant")
        self.entries[grant.id] = connection

    def get(self, grant: Grant) -> Connection:
        connection = self.entries.get(grant.id)
        if connection is None:
            raise StateUnavailable("Connection runtime is not configured")
        return connection


class ConversationWorker:
    def __init__(
        self,
        *,
        grants: Grants,
        connections: Connections,
        outbox: RoomOutbox,
        proxy: str,
        interval: float = 1,
    ) -> None:
        if interval <= 0:
            raise ValueError("Worker interval must be positive")
        self.grants, self.connections, self.outbox = grants, connections, outbox
        self.store = grants.store
        self.proxy, self.interval = proxy, interval
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS pap_worker_state (
                grant_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'idle',
                responder TEXT NOT NULL DEFAULT 'agent', next_poll REAL NOT NULL DEFAULT 0,
                error TEXT, blocked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pap_projected (
                grant_id TEXT NOT NULL,event_id TEXT NOT NULL,PRIMARY KEY(grant_id,event_id)
            );
            CREATE TABLE IF NOT EXISTS pap_direct (
                grant_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL
            );
        """)

    async def session(
        self, grant: Grant
    ) -> tuple[OAuthClient, Session, str, str | None]:
        connection = self.connections.get(grant)
        channel = next(
            (
                item
                for item in (
                    connection.oauth.discovery.document.agent.protocols
                    if connection.oauth.discovery.document.agent
                    else ()
                )
                if item.type == "poppy"
            ),
            None,
        )
        if channel is None:
            raise StateUnavailable("Company does not advertise PAP conversations")
        await connection.oauth.token(resource=channel.resource)
        session = await self.current_session(grant)
        return connection.oauth, session, channel.endpoint, channel.resource

    async def current_session(self, grant: Grant) -> Session:
        connection = self.connections.get(grant)
        session = await connection.current_session()
        _, credentials = connection.oauth.record()
        if session.scope != grant.session.scope or (
            credentials.get("session") is not None
            and (
                session.session_id != credentials["session"]
                or bool(session.account) != bool(credentials.get("signed_in"))
            )
        ):
            raise StateUnavailable("Authoritative session disagrees with credentials")
        self.grants.get(grant.id)
        self.store.conversation(session, grant.binding)
        if session.account is not None:
            self.store.bind_account(session, grant.binding)
        self.store.connect(session, grant.connection, grant.route)
        return session

    async def check_authority(self, grant: Grant) -> None:
        await self.current_session(grant)

    def status(self, grant_id: str) -> tuple[str, str, str | None]:
        row = self.store.db.execute(
            "SELECT status,responder,error FROM pap_worker_state WHERE grant_id=?",
            (grant_id,),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else ("idle", "agent", None)

    def remember(
        self, grant_id: str, response: ConversationResponse, *, delay: float = 0
    ) -> None:
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO pap_worker_state(grant_id,status,responder,next_poll) VALUES(?,?,?,?) ON CONFLICT(grant_id) DO UPDATE SET status=excluded.status,responder=excluded.responder,next_poll=excluded.next_poll,error=NULL,blocked=0",
                (grant_id, response.status, response.responder, time.time() + delay),
            )

    def fail(
        self, grant_id: str, code: str, *, delay: float = 5, blocked: bool = False
    ) -> None:
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO pap_worker_state(grant_id,next_poll,error,blocked) VALUES(?,?,?,?) ON CONFLICT(grant_id) DO UPDATE SET next_poll=excluded.next_poll,error=excluded.error,blocked=excluded.blocked",
                (grant_id, time.time() + delay, code, int(blocked)),
            )

    async def send(
        self,
        grant: Grant,
        oauth: OAuthClient,
        session: Session,
        endpoint: str,
        resource: str | None,
    ) -> None:
        conversation = self.store.conversation(session, grant.binding)
        delivery = self.store.claim(
            session, grant.binding, now=time.time(), lease_seconds=120
        )
        if delivery is None:
            return
        assert delivery.lease is not None
        path = (
            endpoint
            if conversation.remote_id is None
            else endpoint.rstrip("/") + "/" + conversation.remote_id + "/messages"
        )
        document = dict(delivery.payload)
        direct = self.store.db.execute(
            "SELECT parent_id FROM pap_direct WHERE grant_id=?", (grant.id,)
        ).fetchone()
        if direct and conversation.remote_id is None:
            document["parent_conversation_id"] = direct[0]
        try:
            self.grants.get(grant.id)
            response = await oauth.request(
                "POST",
                path,
                resource=resource,
                document=document,
                authorize=partial(self.check_authority, grant),
            )
            if response.status not in {201, 202}:
                try:
                    error = ErrorBody.model_validate(response.json())
                except (ValidationError, ValueError):
                    error = ErrorBody(error="protocol_error")
                self.store.settle(
                    session,
                    delivery.message_id,
                    lease=delivery.lease,
                    now=time.time(),
                    failure=RetryFailure(
                        status_code=response.status,
                        retry_at=time.time()
                        + max(response.retry_delay(time.time()), self.interval),
                        error=error,
                    ),
                )
                blocked = response.status not in {408, 429, 500, 502, 503, 504}
                self.fail(
                    grant.id,
                    error.error,
                    delay=max(response.retry_delay(time.time()), self.interval),
                    blocked=blocked,
                )
                if blocked:
                    await self.outbox.accept(
                        RoomDelivery(
                            id=stable_id(grant.id, delivery.message_id, "refused"),
                            room=grant.route.room,
                            proxy=grant.route.proxy,
                            recipients=(grant.route.recipient,),
                            grant=grant.id,
                            text="PAP could not accept this message. The connection needs attention.",
                            record={
                                "error": error.error,
                                "message_id": delivery.message_id,
                            },
                        )
                    )
                return
            accepted = ConversationResponse.model_validate(response.json())
            self.grants.get(grant.id)
            self.store.settle(
                session,
                delivery.message_id,
                lease=delivery.lease,
                now=time.time(),
                response=accepted.wire(),
                status_code=response.status,
            )
            self.remember(grant.id, accepted)
        except BaseException:
            # PAP deduplicates the immutable message ID even if acceptance was lost.
            self.store.settle(
                session, delivery.message_id, lease=delivery.lease, now=time.time()
            )
            raise

    async def poll(
        self,
        grant: Grant,
        oauth: OAuthClient,
        session: Session,
        endpoint: str,
        resource: str | None,
    ) -> None:
        conversation = self.store.conversation(session, grant.binding)
        if conversation.remote_id is None:
            return
        query = {"wait": "0"}
        if conversation.cursor is not None:
            query["cursor"] = conversation.cursor
        response = await oauth.request(
            "GET",
            endpoint.rstrip("/")
            + "/"
            + conversation.remote_id
            + "/events?"
            + urlencode(query),
            resource=resource,
            authorize=partial(self.check_authority, grant),
        )
        if response.status != 200:
            error = ProtocolError(response)
            if error.code == "cursor_expired" and conversation.cursor:
                self.store.cursor_expired(
                    session, grant.binding, expected_cursor=conversation.cursor
                )
                await self.outbox.accept(
                    RoomDelivery(
                        id=stable_id(grant.id, "gap", conversation.cursor),
                        room=grant.route.room,
                        proxy=grant.route.proxy,
                        recipients=(grant.route.recipient,),
                        grant=grant.id,
                        text="Part of this PAP conversation is no longer available from the company.",
                    )
                )
            self.fail(
                grant.id,
                error.code,
                delay=max(response.retry_delay(time.time()), self.interval),
            )
            return
        page = EventPage.model_validate(response.json())
        self.grants.get(grant.id)
        self.store.ingest(
            session, grant.binding, page, expected_cursor=conversation.cursor
        )
        self.remember(grant.id, page, delay=0 if page.has_more else self.interval)

    async def project(self, grant: Grant, session: Session) -> None:
        for event in self.store.events(session, grant.binding):
            if self.store.db.execute(
                "SELECT 1 FROM pap_projected WHERE grant_id=? AND event_id=?",
                (grant.id, event.id),
            ).fetchone():
                continue
            text = None
            if isinstance(event, MessageEvent) and event.message.role == "company":
                text = event.message.text or "PAP structured message"
            elif event.type in {
                "state",
                "authorization",
                "user_requested",
                "direct_opened",
                "direct_closed",
            }:
                text = "PAP conversation update: " + event.type
            if text is not None:
                self.grants.get(grant.id)
                await self.outbox.accept(
                    RoomDelivery(
                        id=stable_id(grant.id, event.id),
                        room=grant.route.room,
                        proxy=grant.route.proxy,
                        recipients=(grant.route.recipient,),
                        grant=grant.id,
                        text=text,
                        record=event.model_dump(mode="json"),
                    )
                )
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT OR IGNORE INTO pap_projected VALUES(?,?)",
                    (grant.id, event.id),
                )

    async def tick(self, grant_id: str) -> None:
        grant = self.grants.get(grant_id)
        if grant.route.proxy != self.proxy:
            raise StateUnavailable("Worker cannot impersonate another proxy")
        row = self.store.db.execute(
            "SELECT next_poll,blocked FROM pap_worker_state WHERE grant_id=?",
            (grant_id,),
        ).fetchone()
        if row and (row[0] > time.time() or row[1]):
            await self.outbox.flush(proxy=self.proxy)
            return
        oauth, session, endpoint, resource = await self.session(grant)
        await self.project(grant, session)
        await self.send(grant, oauth, session, endpoint, resource)
        blocked = self.store.db.execute(
            "SELECT blocked FROM pap_worker_state WHERE grant_id=?", (grant.id,)
        ).fetchone()
        if blocked and blocked[0]:
            await self.outbox.flush(proxy=self.proxy)
            return
        await self.poll(grant, oauth, session, endpoint, resource)
        await self.project(grant, session)
        await self.outbox.flush(proxy=self.proxy)

    def retry(self, grant_id: str) -> None:
        """Trusted host resumes a refused request after resolving its prerequisite."""
        grant = self.grants.get(grant_id)
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE pap_worker_state SET blocked=0,next_poll=0,error=NULL WHERE grant_id=?",
                (grant_id,),
            )
            self.store.db.execute(
                "UPDATE deliveries SET available_at=0 WHERE scope=? AND binding=? AND state='ready'",
                (grant.session.scope.key(), grant.binding),
            )

    async def control(
        self, grant_id: str, action: Literal["handoff", "close"]
    ) -> ConversationResponse:
        grant = self.grants.get(grant_id)
        oauth, session, endpoint, resource = await self.session(grant)
        conversation = self.store.conversation(session, grant.binding)
        if conversation.remote_id is None:
            raise StateUnavailable("Conversation has not started")
        response = await oauth.request(
            "POST",
            endpoint.rstrip("/") + "/" + conversation.remote_id + "/" + action,
            resource=resource,
            document={},
            authorize=partial(self.check_authority, grant),
        )
        if response.status not in {200, 202}:
            raise ProtocolError(response)
        ack = ControlResponse.model_validate(response.json())
        if ack.conversation_id not in {None, conversation.remote_id}:
            raise StateConflict("Control response belongs to another conversation")
        result = ConversationResponse(
            conversation_id=conversation.remote_id,
            status=ack.status,
            responder=ack.responder,
        )
        self.remember(grant.id, result)
        return result

    async def direct(self, parent_grant_id: str, child: Grant, first: Message) -> None:
        parent = self.grants.get(parent_grant_id)
        if (
            first.sender != "human"
            or child.sender_type != "User"
            or child.session.scope != parent.session.scope
            or child.route.room != parent.route.room
            or child.route.proxy != parent.route.proxy
        ):
            raise StateUnavailable(
                "Direct conversation requires a separately authorized human in the same room"
            )
        session = await self.current_session(parent)
        if child.session.account != session.account:
            raise StateUnavailable(
                "Direct conversation must retain parent account ownership"
            )
        conversation = self.store.conversation(session, parent.binding)
        if conversation.remote_id is None:
            raise StateUnavailable("Parent conversation has not started")
        self.grants.provision(child)
        with self.store.transaction():
            previous = self.store.db.execute(
                "SELECT parent_id FROM pap_direct WHERE grant_id=?", (child.id,)
            ).fetchone()
            if previous is not None and previous[0] != conversation.remote_id:
                raise StateConflict("Direct conversation cannot change its parent")
            self.store.db.execute(
                "INSERT OR IGNORE INTO pap_direct VALUES(?,?)",
                (child.id, conversation.remote_id),
            )
        self.store.enqueue(child.session, child.binding, SendMessage(message=first))

    async def pump(self, grant_id: str) -> None:
        while True:
            try:
                await self.tick(grant_id)
            except asyncio.CancelledError:
                raise
            except (
                StateUnavailable,
                AuthError,
                StateConflict,
                ProtocolError,
                ValueError,
            ):
                self.fail(grant_id, "connection_unavailable")
            except Exception:  # noqa: BLE001 -- transport failures must preserve the durable queue without logging secrets.
                # A transport error contains credentials or remote content; do not log it.
                self.fail(grant_id, "transport_unavailable")
            await asyncio.sleep(self.interval)

    async def run(self) -> None:
        try:
            while True:
                active = {grant.id for grant in self.grants.for_proxy(self.proxy)}
                for grant_id in active:
                    if grant_id not in self.tasks or self.tasks[grant_id].done():
                        self.tasks[grant_id] = asyncio.create_task(self.pump(grant_id))
                for grant_id in tuple(self.tasks):
                    if grant_id not in active:
                        task = self.tasks.pop(grant_id)
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                await asyncio.sleep(self.interval)
        finally:
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()
