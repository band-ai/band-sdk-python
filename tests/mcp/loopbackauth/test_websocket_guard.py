"""A websocket upgrade reaches the guarded app only with the bearer token."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.status import WS_1008_POLICY_VIOLATION
from starlette.types import Message, Receive, Scope, Send

from band.integrations.mcp.local_server import (
    LOCAL_MCP_MESSAGE_PATH,
    BearerTokenGuard,
)
from tests.mcp.loopbackauth.kit import (
    AUTH_TOKEN,
    REFUSED_CREDENTIALS,
    SERVER_NAME,
    Credential,
    credential_headers,
)

REACHED = {"type": "websocket.accept"}


async def accepting_app(scope: Scope, receive: Receive, send: Send) -> None:
    """The guarded app: accepts whatever upgrade reaches it."""
    await send(REACHED)


def websocket_scope(credential: Credential) -> dict[str, Any]:
    """A websocket upgrade to the SSE message endpoint presenting ``credential``."""
    headers = [
        (name.lower().encode(), value.encode())
        for name, value in credential_headers(credential).items()
    ]
    return {
        "type": "websocket",
        "path": LOCAL_MCP_MESSAGE_PATH,
        "headers": headers,
    }


async def upgrade(credential: Credential) -> list[Message]:
    """What the guard sends back to an upgrade presenting ``credential``."""
    guard = BearerTokenGuard(
        accepting_app,
        token=AUTH_TOKEN,
        open_paths=frozenset(),
        routes=frozenset({LOCAL_MCP_MESSAGE_PATH}),
        server_name=SERVER_NAME,
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await guard(websocket_scope(credential), receive, send)
    return sent


class TestWebsocketGuard:
    @pytest.mark.parametrize("credential", REFUSED_CREDENTIALS)
    async def test_an_unauthorized_upgrade_is_closed_before_the_app(
        self, credential: Credential
    ) -> None:
        assert await upgrade(credential) == [
            {"type": "websocket.close", "code": WS_1008_POLICY_VIOLATION, "reason": ""}
        ]

    async def test_an_authorized_upgrade_reaches_the_app(self) -> None:
        assert await upgrade(Credential.VALID) == [REACHED]
