"""A failed startup must not leak a running owned client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.core.exceptions import BandConfigError
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    requires_copilot_sdk,
)

pytestmark = requires_copilot_sdk


class TestStartupFailure:
    class UnauthenticatedClient(FakeCopilotClient):
        async def get_auth_status(self) -> Any:
            return SimpleNamespace(
                isAuthenticated=False, statusMessage="Not authenticated"
            )

    @pytest.mark.asyncio
    async def test_auth_failure_stops_owned_client(self):
        client = self.UnauthenticatedClient()
        adapter = CopilotSDKAdapter(client_factory=lambda: client)

        with pytest.raises(BandConfigError, match="Not authenticated"):
            await adapter.on_started("Copilot Agent", "desc")

        assert client.started
        assert client.stopped

    @pytest.mark.asyncio
    async def test_auth_failure_leaves_borrowed_client_running(self):
        client = self.UnauthenticatedClient()
        adapter = CopilotSDKAdapter(client=client)

        with pytest.raises(BandConfigError, match="Not authenticated"):
            await adapter.on_started("Copilot Agent", "desc")

        assert not client.stopped  # its owner decides its lifecycle
