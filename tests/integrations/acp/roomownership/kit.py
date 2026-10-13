"""Shared agents, sessions and helpers for the ACP room-ownership tests."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import pytest
from acp.schema import SessionConfigOptionSelect

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.client.streaming import ControlMode
from band.core.retention import ReleaseCause, RoomRetentionConfig
from band.integrations.acp import client_runtime
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.acp.client_types import ACPRoom
from band.integrations.acp.model_selection import MODEL_CATEGORY
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    Reply,
    acp_adapter,
    decision_token,
    fake_agent_config,
    released,
    select_option,
    started_acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID, Gate
from tests.integrations.acp.acp_toolkit.harness import RELEASE_TURNS

CANCELLED = {"outcome": {"outcome": "cancelled"}}
# Start and transition bounds short enough that waiting them out costs nothing.
HUNG_START_TIMEOUT_S = 0.05
SHORT_TRANSITION_S = 0.05
# A guard on a wait that should finish at once, so a hang fails clearly.
TEST_GUARD_S = 5


def models(current: str, *offered: str) -> FakeACPAgent:
    """An agent with session load whose model select offers ``offered``."""
    return FakeACPAgent(
        supports_session_load=True,
        config_options=[model_select(current, *offered)],
    )


def model_select(current: str, *offered: str) -> SessionConfigOptionSelect:
    return select_option(MODEL_OPTION_ID, current, offered, category=MODEL_CATEGORY)


async def recover(session: AcpSession, room: str = DEFAULT_ROOM) -> ACPRoom:
    """Retire the room's process through its owner; the session is kept."""
    holder = session.room(room)
    assert holder.retained.recover(generation=holder.generation, reason="test")
    await released(holder)
    return holder


@asynccontextmanager
async def woken(adapter: ACPClientAdapter, room: ACPRoom) -> AsyncIterator[None]:
    """Acquire ``room`` as a deliberate wake does, then close that process.

    Idle release is disabled for every ACP profile, so the wake path is driven
    through the adapter's acquisition callback directly.
    """
    try:
        await adapter._acquire_room(room, room.generation + 1, True)
        yield
    finally:
        await adapter._release_room(room, ReleaseCause.RECOVERY)


def questions(reply: Reply) -> list[str]:
    """The Cursor question decisions ``reply`` has posted to the room."""
    return [text for text in reply.texts if "Cursor needs input" in text]


async def question_asked(reply: Reply) -> None:
    """Let a running turn post its question decision to ``reply``'s room."""
    for _ in range(RELEASE_TURNS):
        if questions(reply):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"no question was asked: {reply.texts}")


def question_token(reply: Reply) -> str:
    """The token of the one question decision ``reply`` asked the room."""
    [prompt] = questions(reply)
    return decision_token(prompt)


def stop_budget() -> ACPClientAdapterConfig:
    """Band tools on, with a transition bound short enough to outlast."""
    return fake_agent_config(
        inject_band_tools=True,
        retention=RoomRetentionConfig(transition_timeout_s=SHORT_TRANSITION_S),
    )


def holds_prompt(agent: FakeACPAgent, number: int) -> Gate:
    """Hold the agent's ``number``-th prompt until release; others answer."""
    prompting = Gate()

    @agent.on_prompt
    async def hold(peer: FakeACPAgent, session_id: str) -> None:
        if len(peer.prompts) == number:
            await prompting.hold()

    return prompting


class QuestioningAgent(FakeACPAgent):
    """Asks one Cursor question per prompt and records each session's answer."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: dict[str, dict[str, object]] = {}
        self.on_prompt(self.ask_mode)

    async def ask_mode(self, peer: FakeACPAgent, session_id: str) -> None:
        self.answers[session_id] = await self.ask_cursor_question(
            session_id, "mode", "agent", "plan"
        )


@asynccontextmanager
async def cursor(agent: FakeACPAgent, **settings: Any) -> AsyncIterator[AcpSession]:
    adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent", **settings))
    async with started_acp_adapter(adapter, agent) as session:
        yield session


@asynccontextmanager
async def inline(agent: FakeACPAgent, **settings: Any) -> AsyncIterator[AcpSession]:
    """An adapter whose turns run inside their message handler (OMP, Copilot)."""
    async with acp_adapter(agent, fake_agent_config(**settings)) as session:
        yield session


async def stop(session: AcpSession, handler: asyncio.Task[Reply]) -> None:
    """A room ``/stop``: the runtime cancels the handler and interrupts the room."""
    handler.cancel()
    await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.STOP)
    with suppress(asyncio.CancelledError):
        await handler


def answered(option: str) -> dict[str, object]:
    return {
        "outcome": {
            "outcome": "answered",
            "answers": [{"questionId": "mode", "selectedOptionIds": [option]}],
        }
    }


def runtime_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == client_runtime.__name__ and record.levelno >= logging.WARNING
    ]


def terminate_pid(pid: int) -> None:
    with suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM)
