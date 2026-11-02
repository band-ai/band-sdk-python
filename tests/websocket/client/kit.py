"""Shared payloads, doubles and helpers for the WebSocketClient tests."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from band.client.streaming import WebSocketClient
from band.client.streaming.errors import CONNECTION_CONFLICT_CODE
from band.client.streaming.session_decider import VERDICT_LOG
from band.client.streaming.session_decider import logger as decider_logger
from tests.toolkit.timecontrol import ControlledClock

VALID_MESSAGE_CREATED_PAYLOAD: dict = {
    "id": "msg-123",
    "content": "@TestBot hi",
    "message_type": "text",
    "metadata": {
        "mentions": [{"id": "agent-123", "handle": "testbot", "name": "TestBot"}],
        "status": "sent",
    },
    "sender_id": "user-456",
    "sender_type": "User",
    "chat_room_id": "room-123",
    "thread_id": None,
    "inserted_at": "2025-11-17T11:20:10.284136Z",
    "updated_at": "2025-11-17T11:20:10.284136Z",
}

# A message_updated frame as observed from the real backend: same shape as
# message_created, with per-recipient processing state under
# metadata.delivery_status keyed by the recipient (agent) id.
VALID_MESSAGE_UPDATED_PAYLOAD: dict = {
    "id": "msg-123",
    "content": "@TestBot hi",
    "message_type": "text",
    "metadata": {
        "mentions": [{"id": "agent-123", "handle": "testbot", "name": "TestBot"}],
        "status": "sent",
        "delivery_status": {
            "agent-123": {
                "status": "processed",
                "delivered_at": "2025-11-17T11:20:11.000000Z",
                "processed_at": "2025-11-17T11:20:13.000000Z",
                "attempts": [{"attempt_number": 1, "status": "success"}],
            }
        },
    },
    "sender_id": "user-456",
    "sender_type": "User",
    "chat_room_id": "room-123",
    "inserted_at": "2025-11-17T11:20:10.284136Z",
    "updated_at": "2025-11-17T11:20:13.000000Z",
}


async def dispatch(client: WebSocketClient, event: str, payload: dict) -> Any:
    """Feed one event through _handle_events via a single registered
    callback; return what that callback received (None if never called)."""
    callback = AsyncMock()
    await client._handle_events(
        SimpleNamespace(event=event, payload=payload), {event: callback}
    )
    return callback.await_args.args[0] if callback.await_args else None


def upgrade_exception(
    status_code: int, body: bytes, headers: dict[str, str] | None = None
) -> InvalidStatus:
    return InvalidStatus(
        Response(
            status_code=status_code,
            reason_phrase="error",
            headers=Headers(headers or {}),
            body=body,
        )
    )


def connection_conflict_error_body(*, request_id: str = "req-409") -> bytes:
    return json.dumps(
        {
            "error": {
                "code": CONNECTION_CONFLICT_CODE,
                "message": "already connected",
                "request_id": request_id,
            }
        }
    ).encode()


def upgrade_error_body(code: str, *, status: int, retry_after: object = None) -> bytes:
    """The platform's JSON refusal body, with an optional ``retry_after``."""
    error: dict[str, object] = {
        "code": code,
        "message": f"refused {code}",
        "request_id": f"req-{status}",
    }
    if retry_after is not None:
        error["retry_after"] = retry_after
    return json.dumps({"error": error}).encode()


@asynccontextmanager
async def phoenix_peer(*, ack_heartbeats: bool = True):
    """In-process peer that completes the WS upgrade and, when
    ack_heartbeats, replies to every V2 heartbeat frame (topic "phoenix")
    with a matching phx_reply -- a real wire heartbeat round-trip drives
    on_heartbeat_ack, nothing about it is mocked. Yields ``(ws_url,
    connected)``, where ``connected`` resolves with the first accepted
    ServerConnection.
    """
    connected: asyncio.Future[ServerConnection] = (
        asyncio.get_running_loop().create_future()
    )

    async def handler(conn: ServerConnection) -> None:
        if not connected.done():
            connected.set_result(conn)
        async for raw in conn:
            if not ack_heartbeats:
                continue
            join_ref, ref, topic, _event, _payload = json.loads(raw)
            if topic == "phoenix":
                await conn.send(
                    json.dumps(
                        [
                            join_ref,
                            ref,
                            "phoenix",
                            "phx_reply",
                            {"status": "ok", "response": {}},
                        ]
                    )
                )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}/socket/websocket", connected


class RetryRecorder(logging.Handler):
    """Queues the delay of each retry the session decides, as it logs it."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.delays: asyncio.Queue[float] = asyncio.Queue()
        # Kept for the caller to assert: raising here would escape the
        # decider's log call and end the session instead.
        self.unexpected: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == VERDICT_LOG:
            return
        match record.args:
            case (_, float(delay_s)):
                self.delays.put_nowait(delay_s)
            case _:
                self.unexpected.append(record.getMessage())


def expire_after(clock: ControlledClock, deadline: float, hops: int) -> None:
    """Move the clock to ``deadline`` ``hops`` loop iterations from now."""
    loop = clock.loop

    def hop(left: int) -> None:
        if left:
            loop.call_soon(hop, left - 1)
        else:
            clock.advance(deadline - loop.time())

    hop(hops)


@contextmanager
def observe_retries() -> Iterator[asyncio.Queue[float]]:
    """Each retry delay the session decides on, in order. A decision is final
    by the time it is queued: the transport is then waiting out that delay."""
    recorder = RetryRecorder()
    decider_logger.addHandler(recorder)
    try:
        yield recorder.delays
    finally:
        decider_logger.removeHandler(recorder)
    assert recorder.unexpected == []
