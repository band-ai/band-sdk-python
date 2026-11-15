"""Durable, independently addressed Band deliveries with explicit uncertainty."""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import Field

from band.client.rest import (
    AsyncRestClient,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
)
from bandpap.storage import (
    StateConflict,
    StateModel,
    StateStore,
    StateUnavailable,
    canonical,
)


class RoomDelivery(StateModel):
    id: str
    room: str
    proxy: str
    recipients: tuple[str, ...] = Field(min_length=1)
    grant: str
    text: str
    record: dict[str, Any] | None = Field(default=None, repr=False)


class RoomSender(Protocol):
    async def send(self, delivery: RoomDelivery) -> str: ...


class RestRoomSender:
    """One client per independently authenticated proxy; never change its identity."""

    def __init__(
        self,
        clients: dict[str, AsyncRestClient],
        *,
        authorize: Callable[[RoomDelivery], Awaitable[None]],
    ) -> None:
        self.clients = dict(clients)
        self.authorize = authorize

    async def send(self, delivery: RoomDelivery) -> str:
        client = self.clients.get(delivery.proxy)
        if client is None:
            raise StateUnavailable("Proxy has no authenticated Band client")
        identity = await client.agent_api_identity.get_agent_me(
            request_options={"max_retries": 0}
        )
        if identity.data.id != delivery.proxy:
            raise StateUnavailable("Band credential belongs to another proxy")
        await self.authorize(delivery)
        response = await client.agent_api_messages.create_agent_chat_message(
            delivery.room,
            message=ChatMessageRequest(
                content=delivery.text,
                mentions=[
                    ChatMessageRequestMentionsItem(id=recipient, kind="mention")
                    for recipient in delivery.recipients
                ],
            ),
            request_options={"max_retries": 0},
        )
        if not response.data.success or {
            item.id for item in response.data.recipients
        } != set(delivery.recipients):
            raise StateConflict(
                "Band acknowledgment does not confirm the intended recipients"
            )
        return response.data.id


class RoomOutbox:
    """Accept once locally; never automatically repeat an uncertain Band POST.

    ``authorize`` must consult current host grants and recipient membership before
    every send or private-record read. A room ID alone is never authority.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        sender: RoomSender,
        authorize: Callable[[RoomDelivery], Awaitable[None]],
    ) -> None:
        self.store = store
        self.sender = sender
        self.authorize = authorize
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS pap_room_outbox (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, reference TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL DEFAULT 'ready', remote_id TEXT
            );
        """)

    async def accept(self, delivery: RoomDelivery) -> str:
        await self.authorize(delivery)
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT * FROM pap_room_outbox WHERE id=?", (delivery.id,)
            ).fetchone()
            if row:
                if RoomDelivery.model_validate_json(row["payload"]) != delivery:
                    raise StateConflict("Room delivery ID changed content or route")
                return row["reference"]
            reference = secrets.token_urlsafe(32)
            self.store.db.execute(
                "INSERT INTO pap_room_outbox(id,payload,reference) VALUES(?,?,?)",
                (delivery.id, delivery.model_dump_json(), reference),
            )
            return reference

    async def flush(self, *, proxy: str, limit: int = 100) -> int:
        rows = self.store.db.execute(
            "SELECT id,payload FROM pap_room_outbox WHERE state='ready' ORDER BY rowid"
        ).fetchall()
        sent = 0
        attempted = 0
        blocked = {
            RoomDelivery.model_validate_json(row[0]).grant
            for row in self.store.db.execute(
                "SELECT payload FROM pap_room_outbox WHERE state='uncertain'"
            )
        }
        for row in rows:
            delivery = RoomDelivery.model_validate_json(row["payload"])
            if delivery.proxy != proxy or delivery.grant in blocked:
                continue
            if attempted >= limit:
                break
            try:
                await self.authorize(delivery)
            except Exception:  # noqa: BLE001 -- one denied or unavailable grant must not block unrelated recipients.
                blocked.add(delivery.grant)
                continue
            attempted += 1
            with self.store.transaction():
                claimed = self.store.db.execute(
                    "UPDATE pap_room_outbox SET state='uncertain' WHERE id=? AND state='ready'",
                    (delivery.id,),
                ).rowcount
            if not claimed:
                continue
            reference = self.store.db.execute(
                "SELECT reference FROM pap_room_outbox WHERE id=?", (delivery.id,)
            ).fetchone()[0]
            projected = delivery.model_copy(
                update={
                    "text": delivery.text
                    + (
                        f"\n[PAP record: {reference}]"
                        if delivery.record is not None
                        else ""
                    ),
                    "record": None,
                }
            )
            # A timeout/cancellation/crash leaves the precommitted uncertain state.
            try:
                remote_id = await self.sender.send(projected)
            except Exception:  # noqa: BLE001 -- preserve uncertain acceptance without replaying or logging remote secrets.
                blocked.add(delivery.grant)
                continue
            if not remote_id:
                blocked.add(delivery.grant)
                continue
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE pap_room_outbox SET state='delivered',remote_id=? WHERE id=? AND state='uncertain'",
                    (remote_id, delivery.id),
                )
            sent += 1
        return sent

    def state(self, delivery_id: str) -> tuple[str, str | None]:
        row = self.store.db.execute(
            "SELECT state,remote_id FROM pap_room_outbox WHERE id=?", (delivery_id,)
        ).fetchone()
        if row is None:
            raise StateUnavailable("Room delivery unavailable")
        return row[0], row[1]

    async def reconcile(
        self, delivery_id: str, *, remote_id: str | None, confirmed_absent: bool = False
    ) -> None:
        """Trusted host evidence only; lack of a response is not evidence of absence."""
        row = self.store.db.execute(
            "SELECT payload FROM pap_room_outbox WHERE id=?", (delivery_id,)
        ).fetchone()
        if (
            row is None
            or remote_id == ""
            or (remote_id is None and not confirmed_absent)
        ):
            raise StateConflict(
                "Reconciliation requires authenticated delivery evidence"
            )
        await self.authorize(RoomDelivery.model_validate_json(row[0]))
        with self.store.transaction():
            changed = self.store.db.execute(
                "UPDATE pap_room_outbox SET state=?,remote_id=? WHERE id=? AND state='uncertain'",
                ("delivered" if remote_id else "ready", remote_id, delivery_id),
            ).rowcount
            if not changed:
                raise StateConflict("Delivery is not awaiting reconciliation")

    async def record(self, reference: str, *, room: str, actor: str) -> dict[str, Any]:
        """Actor and room come from the host's authenticated request context."""
        row = self.store.db.execute(
            "SELECT payload FROM pap_room_outbox WHERE reference=?", (reference,)
        ).fetchone()
        if row is None:
            raise StateUnavailable("Private record unavailable")
        delivery = RoomDelivery.model_validate_json(row[0])
        if delivery.room != room or actor not in delivery.recipients:
            raise StateUnavailable("Private record unavailable")
        await self.authorize(delivery)
        return json.loads(canonical(delivery.record or {}))

    def health(self, *, proxy: str) -> dict[str, int]:
        counts = {"ready": 0, "uncertain": 0, "delivered": 0}
        for row in self.store.db.execute("SELECT payload,state FROM pap_room_outbox"):
            if RoomDelivery.model_validate_json(row[0]).proxy == proxy:
                counts[row[1]] += 1
        return counts
