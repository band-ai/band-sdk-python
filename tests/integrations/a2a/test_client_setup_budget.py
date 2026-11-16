"""A2A client setup budget abandons waiting without leaking a late client."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAdapterConfig


class TestClientSetupBudget:
    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_setup_budget_closes_a_late_client_and_clears_transport(self) -> None:
        adapter = A2AAdapter(
            A2AAdapterConfig(remote_url="http://a2a.test", transition_timeout_s=0.05)
        )
        closed = {"client": False}
        client = MagicMock()

        async def close_client() -> None:
            closed["client"] = True

        client.close = close_client

        async def late_create(url: str) -> MagicMock:
            del url
            # Past the setup budget, still inside the abandoned-close budget.
            await asyncio.sleep(0.06)
            return client

        with patch("band.integrations.a2a.adapter.ClientFactory") as factory_type:
            factory_type.return_value.create_from_url = late_create
            with pytest.raises(TimeoutError, match="A2A client setup"):
                await adapter.on_started("Agent", "Description")

        assert closed == {"client": True}
        assert adapter._client is None
        assert adapter._http_client is None
