"""Agent initialization precedes immediate message delivery."""

from unittest.mock import MagicMock

import pytest

from band.agent import Agent
from tests.agent.startupkit import ImmediateMessageRuntime, TrackingAdapter


class TestStartupRaceCondition:
    @pytest.mark.asyncio
    async def test_adapter_on_started_before_first_message(
        self, mock_rest_client: MagicMock
    ) -> None:
        call_order: list[str] = []
        adapter = TrackingAdapter(call_order)
        runtime = ImmediateMessageRuntime(mock_rest_client)
        async with Agent(runtime=runtime, adapter=adapter):
            assert call_order == ["on_started", "on_message"]
