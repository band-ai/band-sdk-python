# /// script
# requires-python = ">=3.11"
# dependencies = ["band-pap>=0.1.0.dev0", "httpx>=0.28,<1"]
# ///
"""Explicitly simulated Band transport and in-process HTTPS for the offline demo."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from bandpap.http import Response, Transport
from bandpap.room import RoomDelivery
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.turn import Turn
from band.core.types import PlatformMessage

ROOM = "pap-demo-room"


class Identity(BaseModel):
    model_config = ConfigDict(frozen=True)
    participant: str
    credential: SecretStr = Field(repr=False)


class AdapterTurn:
    """The offline transport supplies the SDK's real turn ledger, without tools."""

    def __init__(self) -> None:
        self.turn = Turn()


async def receive(adapter: SimpleAdapter[Any], message: PlatformMessage) -> None:
    context = AdapterTurn()
    await adapter.on_message(
        message,
        cast(AgentToolsProtocol, context),
        [],
        None,
        None,
        is_session_bootstrap=False,
        room_id=ROOM,
    )
    if not context.turn.complete:
        raise RuntimeError("The PAP adapter did not durably settle its input")


def plain_text(text: str) -> str:
    return text.partition("\n[PAP record: ")[0]


def reference(message: PlatformMessage) -> str:
    marker = "\n[PAP record: "
    if marker not in message.content or not message.content.endswith("]"):
        raise ValueError("The message has no protected PAP record reference")
    return message.content.rsplit(marker, 1)[1][:-1]


class SimulatedRoom:
    """Local identity capabilities stand in for independently authenticated Band links.

    This simulator supplies addressed delivery, not a live Band connection. The
    caller cannot choose its authenticated sender through message content.
    """

    def __init__(self, authorize: Callable[[RoomDelivery], Awaitable[None]]) -> None:
        self.id = ROOM
        self.authorize = authorize
        self.identities: dict[str, Identity] = {}
        self.handlers: dict[str, Callable[[PlatformMessage], Awaitable[None]]] = {}
        self.messages: list[PlatformMessage] = []

    def register(
        self, participant: str, handler: Callable[[PlatformMessage], Awaitable[None]]
    ) -> Identity:
        if participant in self.identities:
            raise ValueError("A simulated participant can be registered only once")
        identity = Identity(
            participant=participant, credential=SecretStr(secrets.token_urlsafe(32))
        )
        self.identities[participant], self.handlers[participant] = identity, handler
        return identity

    async def post(self, identity: Identity, delivery: RoomDelivery) -> str:
        registered = self.identities.get(identity.participant)
        if (
            registered is None
            or not hmac.compare_digest(
                registered.credential.get_secret_value(),
                identity.credential.get_secret_value(),
            )
            or delivery.proxy != identity.participant
        ):
            raise PermissionError("Invalid simulated participant credential")
        await self.authorize(delivery)
        if (
            delivery.room != ROOM
            or not delivery.recipients
            or any(recipient not in self.handlers for recipient in delivery.recipients)
        ):
            raise PermissionError("Delivery is outside the registered demo room")
        message = PlatformMessage(
            id="room_" + secrets.token_hex(12),
            room_id=ROOM,
            content=delivery.text,
            sender_id=identity.participant,
            sender_type="Agent",
            sender_name=identity.participant,
            message_type="text",
            metadata={
                "mentions": [
                    {"id": recipient, "kind": "mention"}
                    for recipient in delivery.recipients
                ]
            },
            created_at=datetime.now(UTC),
        )
        self.messages.append(message)
        for recipient in delivery.recipients:
            await self.handlers[recipient](message)
        return message.id

    def transcript(self) -> list[dict[str, Any]]:
        return [
            {
                "sender": message.sender_id,
                "recipients": [
                    mention["id"] for mention in message.metadata["mentions"]
                ],
                "text": plain_text(message.content),
            }
            for message in self.messages
        ]


class RoomSender:
    def __init__(self, room: SimulatedRoom, identities: dict[str, Identity]) -> None:
        self.room, self.identities = room, identities

    async def send(self, delivery: RoomDelivery) -> str:
        identity = self.identities.get(delivery.proxy)
        if identity is None:
            raise PermissionError("Proxy has no registered simulated credential")
        return await self.room.post(identity, delivery)


class LocalHTTPS(Transport):
    """HTTP requests remain in ASGI; JWT/DPoP verification is not bypassed."""

    def __init__(self, app: Any, origin: str) -> None:
        super().__init__()
        self.origin = origin
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url=origin,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        document: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        if not url.startswith(self.origin + "/") or form is not None:
            raise PermissionError("Offline HTTP destination is not registered")
        response = await self.client.request(
            method, url, headers=headers, json=document
        )
        return Response(response.status_code, dict(response.headers), response.content)

    async def close(self) -> None:
        await self.client.aclose()
