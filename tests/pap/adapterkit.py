"""Band adapter harness using the public turn and message surface."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from bandpap.adapter import PAPAdapter, PAPAdapterConfig
from bandpap.discovery import AgentDiscovery, ConversationEndpoint
from bandpap.room import RoomDelivery, RoomOutbox
from bandpap.routing import Grant, Grants
from bandpap.storage import Route, StateStore, StateUnavailable
from bandpap.worker import Connection, Connections, ConversationWorker

from band.core.types import PlatformMessage
from tests.pap.channelkit import ChannelTransport, connection
from tests.pap.kit import session


class Sender:
    def __init__(self) -> None:
        self.sent: list[RoomDelivery] = []
        self.error: Exception | None = None

    async def send(self, delivery: RoomDelivery) -> str:
        self.sent.append(delivery)
        if self.error:
            raise self.error
        return "band_" + delivery.id


def grant(**changes: Any) -> Grant:
    return Grant(
        **(
            {
                "id": "grant",
                "session": session(),
                "connection": "connection",
                "binding": "thread",
                "route": Route(
                    room="room",
                    proxy="target",
                    source="muse",
                    recipient="muse",
                    grant="grant",
                ),
                "sender_type": "Agent",
                "visible_participants": ("muse", "target"),
            }
            | changes
        )
    )


def incoming(**changes: Any) -> PlatformMessage:
    return PlatformMessage(
        **(
            {
                "id": "original",
                "room_id": "room",
                "content": "hello",
                "sender_id": "muse",
                "sender_type": "Agent",
                "sender_name": "display name",
                "message_type": "text",
                "metadata": {"mentions": [{"id": "target", "kind": "mention"}]},
                "created_at": datetime.now(UTC),
            }
            | changes
        )
    )


def turn() -> Any:
    return SimpleNamespace(turn=SimpleNamespace(settle=Mock()))


@dataclass
class Harness:
    store: StateStore
    grants: Grants
    transport: ChannelTransport
    sender: Sender
    outbox: RoomOutbox
    worker: ConversationWorker
    adapter: PAPAdapter

    async def ingest(
        self, message: PlatformMessage | None = None, *, history: Any = None
    ) -> Any:
        tools = turn()
        await self.adapter.on_message(
            message or incoming(),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room",
        )
        return tools


def harness(path: Path) -> Harness:
    oauth, transport, store = connection(path)
    doc = oauth.discovery.document.model_copy(
        update={
            "agent": AgentDiscovery(
                protocols=(
                    ConversationEndpoint(
                        type="poppy", endpoint="https://company.example/conversations"
                    ),
                )
            )
        }
    )
    oauth.discovery = replace(oauth.discovery, document=doc)
    grants = Grants(store)
    binding = grant()
    grants.provision(binding)
    sender = Sender()

    async def authorize(delivery: RoomDelivery) -> None:
        current = grants.get(delivery.grant)
        if (delivery.proxy, delivery.room, delivery.recipients) != (
            current.route.proxy,
            current.route.room,
            (current.route.recipient,),
        ):
            raise StateUnavailable("wrong room delivery")

    outbox = RoomOutbox(store, sender=sender, authorize=authorize)
    connections = Connections()
    connections.add(
        binding,
        Connection(oauth=oauth, current_session=AsyncMock(return_value=session())),
    )
    worker = ConversationWorker(
        grants=grants, connections=connections, outbox=outbox, proxy="target"
    )
    adapter = PAPAdapter(
        PAPAdapterConfig(proxy_id="target"), grants=grants, worker=worker
    )
    return Harness(store, grants, transport, sender, outbox, worker, adapter)


def accepted() -> dict[str, Any]:
    return {"conversation_id": "cnv_one", "status": "working", "responder": "agent"}


def reply() -> dict[str, Any]:
    return {
        "conversation_id": "cnv_one",
        "status": "idle",
        "responder": "agent",
        "cursor": "evt_reply",
        "has_more": False,
        "events": [
            {
                "id": "evt_reply",
                "type": "message",
                "created_at": "2026-10-16T20:00:00Z",
                "message": {
                    "id": "reply",
                    "role": "company",
                    "sender": "agent",
                    "text": "private offer",
                    "data": {"price": 123},
                },
            }
        ],
    }
