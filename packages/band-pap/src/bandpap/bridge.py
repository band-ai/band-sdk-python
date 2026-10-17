"""Band gateway handoffs and explicitly correlated, authenticated company replies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field
from typing_extensions import Unpack

from band.core.adapterconfig import BaseAdapterConfig
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import FeatureKwargs, PlatformMessage, metadata_to_dict
from bandpap.auth import Access
from bandpap.gateway import (
    Action,
    GatewayConversation,
    GatewayDelivery,
    GatewayGrant,
    PAPGateway,
)
from bandpap.room import RoomDelivery, RoomOutbox
from bandpap.storage import StateConflict, StateModel, StateStore, StateUnavailable
from bandpap.wire import MessageEvent, RecordedMessage
from bandpap.worker import stable_id


class GatewayBridge:
    def __init__(
        self,
        *,
        outbox: RoomOutbox,
        authorize: Callable[
            [Access, Action, GatewayConversation | None], Awaitable[GatewayGrant]
        ],
    ) -> None:
        self.outbox = outbox
        self.authorization = authorize

    async def authorize(
        self, access: Access, action: Action, conversation: GatewayConversation | None
    ) -> GatewayGrant:
        return await self.authorization(access, action, conversation)

    async def dispatch(self, work: GatewayDelivery) -> None:
        text = work.message.text if work.message is not None else None
        await self.outbox.accept(
            RoomDelivery(
                id=stable_id(work.owner.scope.key(), work.id),
                room=work.grant.room,
                proxy=work.grant.proxy,
                recipients=work.grant.recipients,
                grant=work.grant.id,
                text=text or "PAP " + work.kind,
                record=work.model_dump(mode="json"),
            )
        )

    async def run(self, *, proxy: str, interval: float = 1) -> None:
        """Run beside the gateway lifespan, independently for each proxy credential."""
        if interval <= 0:
            raise ValueError("Worker interval must be positive")
        while True:
            await self.outbox.flush(proxy=proxy)
            await asyncio.sleep(interval)


class ReplyBinding(StateModel):
    access: Access
    conversation_id: str
    room: str
    proxy: str
    source: str
    message_id: str
    data: dict[str, Any] | None = Field(default=None, repr=False)


class GatewayReplies:
    """Trusted application capability correlates a Band reply to its PAP thread.

    The company obtains the conversation ID from the addressed private record.
    Chat prose and unfiltered metadata never select an external conversation.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS pap_gateway_replies (room TEXT,proxy TEXT,message_id TEXT,payload TEXT NOT NULL,PRIMARY KEY(room,proxy,message_id))"
        )

    def stage(self, reply: ReplyBinding) -> None:
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT payload FROM pap_gateway_replies WHERE room=? AND proxy=? AND message_id=?",
                (reply.room, reply.proxy, reply.message_id),
            ).fetchone()
            if old and ReplyBinding.model_validate_json(old[0]) != reply:
                raise StateConflict("Band reply correlation cannot change")
            self.store.db.execute(
                "INSERT OR IGNORE INTO pap_gateway_replies VALUES(?,?,?,?)",
                (reply.room, reply.proxy, reply.message_id, reply.model_dump_json()),
            )

    def resolve(
        self, *, room: str, proxy: str, message_id: str, source: str
    ) -> ReplyBinding:
        row = self.store.db.execute(
            "SELECT payload FROM pap_gateway_replies WHERE room=? AND proxy=? AND message_id=?",
            (room, proxy, message_id),
        ).fetchone()
        if row is None:
            raise StateUnavailable("Reply requires a trusted conversation binding")
        reply = ReplyBinding.model_validate_json(row[0])
        if reply.source != source:
            raise StateUnavailable("Reply belongs to another authenticated source")
        return reply


class PAPGatewayAdapterConfig(BaseAdapterConfig):
    proxy_id: str = Field(min_length=1)


class PAPGatewayAdapter(SimpleAdapter[Any]):
    def __init__(
        self,
        config: PAPGatewayAdapterConfig,
        *,
        gateway: PAPGateway,
        replies: GatewayReplies,
        history_converter: Any = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        super().__init__(history_converter=history_converter, **features)
        self.config, self.gateway, self.replies = config, gateway, replies

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        if self.require_platform().agent_id != self.config.proxy_id:
            raise ValueError("Gateway proxy requires its own Band credential")

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        metadata = metadata_to_dict(msg.metadata)
        mentions = metadata.get("mentions")
        addressed = isinstance(mentions, list) and any(
            isinstance(item, dict)
            and item.get("id") == self.config.proxy_id
            and item.get("kind", "mention") == "mention"
            for item in mentions
        )
        if (
            not addressed
            or msg.room_id != room_id
            or msg.sender_type not in {"Agent", "User"}
            or msg.message_type != "text"
        ):
            tools.turn.settle()
            return
        reply = self.replies.resolve(
            room=room_id,
            proxy=self.config.proxy_id,
            message_id=msg.id,
            source=msg.sender_id,
        )
        event = MessageEvent.model_validate(
            {
                "id": stable_id(msg.id),
                "type": "message",
                "created_at": msg.created_at.isoformat(),
                "message": RecordedMessage(
                    id=stable_id(msg.id),
                    role="company",
                    sender="human" if msg.sender_type == "User" else "agent",
                    text=msg.content,
                    data=reply.data,
                ).wire(),
            }
        )
        await self.gateway.publish(
            reply.access,
            reply.conversation_id,
            event,
            source_id=msg.sender_id,
            source_type="User" if msg.sender_type == "User" else "Agent",
            recipients=(self.config.proxy_id,),
        )
        tools.turn.settle()
