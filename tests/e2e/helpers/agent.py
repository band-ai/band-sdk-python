"""Agent lifecycle for E2E tests: start an agent with rate-limit-aware reconnect.

Use the ``running_agent`` context manager to start/stop an agent around a test.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from band.agent import Agent
from band.client.streaming.errors import WebSocketUpgradeError
from band.core.simple_adapter import SimpleAdapter

from tests.e2e.helpers.log import log_step

if TYPE_CHECKING:
    from tests.e2e.conftest import E2ESettings

# The platform rate-limits how often one agent_id may reopen its WebSocket after
# a recent supersede (HTTP 429); a fresh agent is built per attempt so a partial
# start never leaves a half-connected agent behind.
_RETRYABLE_WS_STATUS = frozenset({429, 503})
_MAX_CONNECT_ATTEMPTS = 6


async def _connect_agent(
    adapter: SimpleAdapter[Any],
    *,
    agent_id: str,
    api_key: str,
    config: E2ESettings,
) -> Agent:
    """Create and start an agent, retrying rate-limited (HTTP 429/503) connects.

    Waits the server-supplied ``retry_after`` (else exponential backoff) for up
    to ``_MAX_CONNECT_ATTEMPTS`` tries.
    """
    for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
        agent = Agent.create(
            adapter=adapter,
            agent_id=agent_id,
            api_key=api_key,
            ws_url=config.band_ws_url,
            rest_url=config.band_base_url,
        )
        try:
            await agent.start()
            return agent
        except WebSocketUpgradeError as exc:
            with contextlib.suppress(Exception):
                await agent.stop()
            last_attempt = attempt == _MAX_CONNECT_ATTEMPTS
            if exc.status_code not in _RETRYABLE_WS_STATUS or last_attempt:
                raise
            cooldown = float(exc.retry_after or min(2**attempt, 30))
            log_step(
                "retry",
                f"WebSocket rate-limited (HTTP {exc.status_code}); cooling down "
                f"{cooldown:.0f}s before attempt {attempt + 1}",
            )
            await asyncio.sleep(cooldown)
    raise AssertionError("unreachable: loop returns or raises")


@asynccontextmanager
async def running_agent(
    adapter: SimpleAdapter[Any],
    *,
    agent_id: str,
    api_key: str,
    config: E2ESettings,
) -> AsyncGenerator[Agent, None]:
    """Run a started agent for the duration of the ``async with`` block."""
    agent = await _connect_agent(
        adapter, agent_id=agent_id, api_key=api_key, config=config
    )
    try:
        yield agent
    finally:
        await agent.stop()
