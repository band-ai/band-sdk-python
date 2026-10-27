"""Every loopback MCP route but health requires the server's bearer token."""

from __future__ import annotations

from http import HTTPStatus

import pytest

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

CASES = [
    pytest.param(Credential.VALID, ROOM_HTTP, HTTPStatus.OK, id="valid-room-http"),
    pytest.param(Credential.VALID, ROOM_SSE, HTTPStatus.OK, id="valid-room-sse"),
    pytest.param(
        Credential.VALID, SSE_MESSAGE, HTTPStatus.ACCEPTED, id="valid-sse-message"
    ),
    pytest.param(
        Credential.VALID, MULTI_ROOM_HTTP, HTTPStatus.OK, id="valid-multi-room-http"
    ),
    pytest.param(
        Credential.VALID, MULTI_ROOM_SSE, HTTPStatus.OK, id="valid-multi-room-sse"
    ),
    *(
        pytest.param(
            credential, route, HTTPStatus.UNAUTHORIZED, id=f"{credential}-{name}"
        )
        for credential in REFUSED_CREDENTIALS
        for name, route in GUARDED_ROUTES.items()
    ),
    *(
        pytest.param(credential, HEALTH, HTTPStatus.OK, id=f"{credential}-health")
        for credential in Credential
    ),
]


class TestRouteGuard:
    @pytest.mark.timeout(90)
    @pytest.mark.parametrize(("credential", "route", "expected_status"), CASES)
    async def test_route_answers_the_credential(
        self,
        credential: Credential,
        route: Route,
        expected_status: HTTPStatus,
    ) -> None:
        async with guarded_servers() as guarded:
            status = await guarded.status(route, credential)

        assert status == expected_status
