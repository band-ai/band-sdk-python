"""PAP wire fixtures and deterministic HTTPS transport doubles."""

from __future__ import annotations

import asyncio
import ssl
from collections import deque
from typing import Any

import httpcore
from bandpap.storage import Route, Scope, Session, StateStore
from bandpap.wire import EventPage, Message, SendMessage


def session(**changes: Any) -> Session:
    fields = {
        "tenant": "tenant",
        "user": "user",
        "client": "client",
        "issuer": "https://company.example",
        "direction": "outbound",
    }
    fields.update({key: changes.pop(key) for key in list(changes) if key in fields})
    return Session(
        scope=Scope(**fields),
        session_id=changes.pop("session_id", "session"),
        **changes,
    )


def route(**changes: str) -> Route:
    return Route(
        **(
            {
                "room": "room",
                "proxy": "proxy",
                "source": "source",
                "recipient": "source",
                "grant": "grant",
            }
            | changes
        )
    )


def bind(
    store: StateStore,
    caller: Session,
    binding: str = "local",
    remote: str | None = "cnv_one",
) -> None:
    store.connect(caller, binding, route())
    store.create_conversation(
        caller, binding=binding, connection=binding, remote_id=remote
    )


def request(message_id: str = "msg_one", **content: Any) -> SendMessage:
    return SendMessage(
        message=Message(id=message_id, sender="agent", **(content or {"text": "hello"}))
    )


def event(event_id: str = "evt_z", **changes: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "state",
        "created_at": "2026-10-16T20:00:00Z",
        "status": "idle",
        "responder": "agent",
    } | changes


def page(
    *events: dict[str, Any], cursor: str | None = None, conversation_id: str = "cnv_one"
) -> EventPage:
    return EventPage.model_validate(
        {
            "conversation_id": conversation_id,
            "status": "idle",
            "responder": "agent",
            "has_more": False,
            "events": list(events),
            "cursor": events[-1]["id"] if events else cursor,
        }
    )


def operation(**changes: Any) -> dict[str, Any]:
    return {
        "operation_id": "op_one",
        "revision": 1,
        "state": "proposed",
        "summary": "Application-defined action",
        "terms": {"custom": [1, {"amount": "1.00"}]},
        "expires_at": "2026-10-17T20:00:00Z",
        "confirmation": None,
        "result": None,
    } | changes


class HttpStream(httpcore.AsyncNetworkStream):
    def __init__(self, response: bytes, delay: float = 0) -> None:
        self.response = response
        self.written = bytearray()
        self.hostname: str | None = None
        self.delay = delay
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        await asyncio.sleep(self.delay)
        data, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written.extend(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> HttpStream:
        assert ssl_context.check_hostname
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        self.hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class HttpNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, *responses: bytes, delay: float = 0) -> None:
        self.responses = deque(responses)
        self.streams: list[HttpStream] = []
        self.hosts: list[str] = []
        self.delay = delay

    async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> HttpStream:
        self.hosts.append(host)
        stream = HttpStream(self.responses.popleft(), self.delay)
        self.streams.append(stream)
        return stream


def response(body: bytes = b"{}", status: int = 200, **headers: str) -> bytes:
    fields = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Connection": "close",
    } | headers
    head = f"HTTP/1.1 {status} Status\r\n" + "".join(
        f"{key}: {value}\r\n" for key, value in fields.items()
    )
    return head.encode() + b"\r\n" + body
