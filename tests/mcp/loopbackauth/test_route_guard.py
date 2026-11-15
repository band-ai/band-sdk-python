"""Every loopback MCP route but health requires the server's bearer token."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from band.integrations.mcp.local_server import LOCAL_MCP_AUTH_SCHEME
from tests.mcp.loopbackauth.kit import (
    HEALTH,
    MULTI_ROOM_HTTP,
    MULTI_ROOM_SSE,
    REFUSED_CREDENTIALS,
    ROOM_HTTP,
    ROOM_SSE,
    SSE_MESSAGE,
    Credential,
    Route,
    guarded_servers,
)

GUARDED_ROUTES = {
    "room-http": ROOM_HTTP,
    "room-sse": ROOM_SSE,
    "sse-message": SSE_MESSAGE,
    "multi-room-http": MULTI_ROOM_HTTP,
    "multi-room-sse": MULTI_ROOM_SSE,
}


def accepted(status: HTTPStatus) -> tuple[HTTPStatus, None]:
    """An answer that lets the request through, with no challenge."""
    return status, None


# A refusal names the scheme a client can retry with.
REFUSED = (HTTPStatus.UNAUTHORIZED, LOCAL_MCP_AUTH_SCHEME)

CASES = [
    pytest.param(
        Credential.VALID, ROOM_HTTP, accepted(HTTPStatus.OK), id="valid-room-http"
    ),
    pytest.param(
        Credential.VALID, ROOM_SSE, accepted(HTTPStatus.OK), id="valid-room-sse"
    ),
    pytest.param(
        Credential.VALID,
        SSE_MESSAGE,
        accepted(HTTPStatus.ACCEPTED),
        id="valid-sse-message",
    ),
    pytest.param(
        Credential.VALID,
        MULTI_ROOM_HTTP,
        accepted(HTTPStatus.OK),
        id="valid-multi-room-http",
    ),
    pytest.param(
        Credential.VALID,
        MULTI_ROOM_SSE,
        accepted(HTTPStatus.OK),
        id="valid-multi-room-sse",
    ),
    pytest.param(
        Credential.LOWERCASE_SCHEME,
        ROOM_HTTP,
        accepted(HTTPStatus.OK),
        id="lowercase-scheme-room-http",
    ),
    *(
        pytest.param(credential, route, REFUSED, id=f"{credential}-{name}")
        for credential in REFUSED_CREDENTIALS
        for name, route in GUARDED_ROUTES.items()
    ),
    *(
        pytest.param(
            credential, HEALTH, accepted(HTTPStatus.OK), id=f"{credential}-health"
        )
        for credential in Credential
    ),
]


class TestRouteGuard:
    @pytest.mark.timeout(90)
    @pytest.mark.parametrize(("credential", "route", "expected"), CASES)
    async def test_route_answers_the_credential(
        self,
        credential: Credential,
        route: Route,
        expected: tuple[HTTPStatus, str | None],
    ) -> None:
        async with guarded_servers() as guarded:
            answer = await guarded.answer(route, credential)

        assert answer == expected
