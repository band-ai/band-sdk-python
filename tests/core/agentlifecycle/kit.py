"""Agents over a mocked runtime, for adapter lifecycle wiring tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.agent import Agent
from band.runtime.types import SessionConfig


def make_runtime() -> AsyncMock:
    runtime = AsyncMock()
    runtime.stop.return_value = True
    runtime.cleanup_pending = False
    runtime.session_config = SessionConfig()
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    return runtime


async def make_agent(adapter: object) -> Agent:
    agent = Agent(runtime=make_runtime(), adapter=adapter)  # type: ignore[arg-type]
    await agent.start()
    return agent
