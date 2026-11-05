"""Qualify device-grant polling and refresh through an external OAuth peer."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from bandteams.auth import OAuth
from bandteams.settings import HOSTED_AUTH, SCOPES


class TestAuth:
    @pytest.mark.looptime
    async def test_pending_and_slowdown_complete_then_refresh_without_losing_grant(
        self,
    ) -> None:
        polls = iter(
            (
                {"error": "authorization_pending"},
                {"error": "slow_down"},
                {
                    "access_token": "first",
                    "refresh_token": "refresh",
                    "expires_in": 600,
                },
            )
        )
        requests = []

        def peer(request: Any) -> Any:
            requests.append(request)
            match request.url.path:
                case "/.well-known/openid-configuration":
                    return httpx.Response(
                        200,
                        json={
                            "token_endpoint": HOSTED_AUTH + "/oauth2/token",
                            "device_authorization_endpoint": HOSTED_AUTH
                            + "/oauth2/device",
                        },
                    )
                case "/oauth2/device":
                    assert parse_qs(request.content.decode()) == {
                        "client_id": ["public-client"],
                        "scope": [SCOPES],
                    }
                    return httpx.Response(
                        200,
                        json={
                            "device_code": "device",
                            "user_code": "user-code",
                            "verification_uri": HOSTED_AUTH + "/login/device",
                            "expires_in": 600,
                            "interval": 1,
                        },
                    )
                case "/oauth2/token":
                    form = parse_qs(request.content.decode())
                    if form["grant_type"] == ["refresh_token"]:
                        assert form["refresh_token"] == ["refresh"]
                        return httpx.Response(
                            200, json={"access_token": "second", "expires_in": 600}
                        )
                    result = next(polls)
                    return httpx.Response(
                        400 if "error" in result else 200, json=result
                    )
                case _:
                    raise AssertionError("Unexpected OAuth request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as http:
            oauth = OAuth(http, "public-client")
            code = await oauth.start()
            tokens = await oauth.finish(code)
            refreshed = await oauth.refresh(tokens)
        assert (
            refreshed.access_token.get_secret_value(),
            refreshed.refresh_token.get_secret_value(),
        ) == ("second", "refresh")
        assert len(requests) == 6
        assert "user-code" not in repr(code)

    async def test_discovery_cannot_redirect_credentials_to_another_host(self) -> None:

        def peer(request: Any) -> Any:
            assert request.url.path == "/.well-known/openid-configuration"
            return httpx.Response(
                200,
                json={
                    "token_endpoint": "https://foreign.test/token",
                    "device_authorization_endpoint": HOSTED_AUTH + "/device",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as http:
            with pytest.raises(ValueError, match="approved host"):
                await OAuth(http, "public-client").start()
