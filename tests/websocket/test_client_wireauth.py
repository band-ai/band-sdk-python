from __future__ import annotations

import pytest

from band.client.streaming import (
    WebSocketClient,
    WebSocketUpgradeError,
)
from band.credentials import PROXY_MANAGED_API_KEY
from band.testing import fake_phoenix_server


class TestClientWireauth:
    async def test_upgrade_carries_api_key_only_in_x_api_key_header(self):
        """Header authentication keeps the proxy-managed sentinel out of the URL."""
        async with (
            fake_phoenix_server() as server,
            WebSocketClient(server.url, PROXY_MANAGED_API_KEY, "agent-xyz"),
        ):
            pass

        [upgrade] = server.upgrades
        assert "api_key" not in upgrade.query
        assert upgrade.query["agent_id"] == "agent-xyz"
        assert upgrade.query.get(
            "vsn"
        )  # protocol version retained alongside the sentinel
        assert upgrade.headers["x-api-key"] == PROXY_MANAGED_API_KEY

    async def test_refused_upgrade_is_read_with_the_same_credentials_as_the_attempt(
        self,
    ):
        """The probe that reads a refusal's HTTP error authenticates through the
        `x-api-key` header like the real upgrade. Under proxy-managed custody the
        query holds only the sentinel, so a header-less probe is turned away
        unauthenticated and the platform's refusal is never seen."""
        async with fake_phoenix_server(refuse_upgrades=True) as server:
            with pytest.raises(WebSocketUpgradeError):
                async with WebSocketClient(
                    server.url, PROXY_MANAGED_API_KEY, "agent-xyz"
                ):
                    pass

        _attempt, probe = server.upgrades
        assert probe.headers.get("x-api-key") == PROXY_MANAGED_API_KEY
