"""Copilot-shaped fake ACP agent and room helpers for the model-selection tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from band.adapters.copilot_acp import (
    CopilotACPAdapter,
    CopilotACPAdapterConfig,
)
from band.core.model_catalog import ModelSelection
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    DEFAULT_ROOM,
    AcpSession,
    started_acp_adapter,
)

# Copilot CLI 1.0.89's efforts per model (probed live).
COPILOT_EFFORTS = {
    "claude-sonnet-5": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "claude-haiku-4.5": (),
}

# Event-loop ticks that cover every step of a model+effort switch, with
# headroom: a cleanup after tick 15 finds the switch already done.
SWITCH_SWEEP_TICKS = 24
ENDED_MID_SWITCH = f"room {DEFAULT_ROOM}'s ACP session ended mid-switch"


def copilot(current: str = "claude-sonnet-5") -> FakeACPAgent:
    """A fake ``copilot --acp`` advertising Copilot's model catalog."""
    return (
        FakeACPAgent(supports_session_load=True)
        .advertises_models(COPILOT_EFFORTS, current=current)
        .knows_session("persisted-session")
        .will_say("Configured")
    )


@asynccontextmanager
async def copilot_room(agent: FakeACPAgent, **config: Any) -> AsyncIterator[AcpSession]:
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(inject_band_tools=False, **config)
    )
    async with started_acp_adapter(adapter, agent) as session:
        yield session


async def switch_room(session: AcpSession, **selection: str) -> None:
    """Switch the live session of the room ``session.send`` talks to."""
    await session.adapter.apply_model_selection(
        ModelSelection(**selection), room_id=DEFAULT_ROOM
    )
