"""TestStartFailureCleansUpAdapter"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.agent import Agent
from tests.core.agentlifecycle.kit import make_runtime


class TestStartFailureCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_runtime_start_failure_rolls_back_adapter(self):
        """on_started may spawn resources; a failed runtime.start must free them."""
        adapter = AsyncMock()
        runtime = make_runtime()
        runtime.start.side_effect = RuntimeError("websocket refused")
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="websocket refused"):
            await agent.start()

        adapter.cleanup_all.assert_awaited_once()
