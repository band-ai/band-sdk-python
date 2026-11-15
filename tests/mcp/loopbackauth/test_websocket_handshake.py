"""A websocket client upgrading to the SSE message endpoint, a raw ASGI mount,
without the bearer token gets its handshake rejected with HTTP 403 by a real
served server."""

from __future__ import annotations

import pytest
from starlette.status import HTTP_403_FORBIDDEN
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from tests.mcp.loopbackauth.kit import (
    REFUSED_CREDENTIALS,
    SSE_MESSAGE,
    Credential,
    credential_headers,
    guarded_servers,
)


class TestWebsocketHandshake:
    @pytest.mark.parametrize("credential", REFUSED_CREDENTIALS)
    async def test_an_unauthorized_upgrade_is_rejected_with_403(
        self, credential: Credential
    ) -> None:
        async with guarded_servers() as guarded:
            with pytest.raises(InvalidStatus) as rejected:
                async with connect(
                    guarded.url(SSE_MESSAGE, scheme="ws"),
                    additional_headers=credential_headers(credential),
                ):
                    pass

        assert rejected.value.response.status_code == HTTP_403_FORBIDDEN
