"""NoopReporter behavior at the CrewAI tool boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestNoopReporter:
    @pytest.mark.asyncio
    async def test_never_calls_send_event(self, builder_mod):
        reporter = builder_mod.NoopReporter()
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()
