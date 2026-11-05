"""Exercise the released REST client without API keys or transport retries."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from bandteams.auth import Tokens
from bandteams.proposals import Rejected
from bandteams.transport import Band


class TestTransport:
    async def test_confirmed_send_uses_bearer_and_exact_content(self) -> None:
        requests = []

        def peer(request: Any) -> Any:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "success": True,
                        "recipients": [],
                    }
                },
            )

        band = Band(
            Tokens(access_token="transient-token", expires_in=600),
            transport=httpx.MockTransport(peer),
        )
        try:
            result = await band.send("room", "recipient", "Exact body")
            assert result == "00000000-0000-0000-0000-000000000001"
            assert len(requests) == 1
            request = requests[0]
            assert (
                request.method,
                request.url.path,
                request.headers.get("authorization"),
                request.headers.get("x-api-key"),
            ) == (
                "POST",
                "/api/v1/me/chats/room/messages",
                "Bearer transient-token",
                None,
            )
            assert json.loads(request.content) == {
                "message": {"content": "Exact body", "mentions": [{"id": "recipient"}]}
            }
        finally:
            await band.close()

    async def test_definite_rejection_is_not_retried(self) -> None:
        requests = []

        def peer(request: Any) -> Any:
            requests.append(request)
            return httpx.Response(429, json={"error": "rate limited"})

        band = Band(
            Tokens(access_token="transient-token", expires_in=600),
            transport=httpx.MockTransport(peer),
        )
        try:
            with pytest.raises(Rejected):
                await band.send("room", "recipient", "body")
            assert len(requests) == 1
        finally:
            await band.close()
