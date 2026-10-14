"""Shared agents, sessions and helpers for the ACP room-ownership tests."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

import pytest
from acp.schema import SessionConfigOptionSelect, ToolCallUpdate

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.client.streaming import ControlMode
from band.core.model_catalog import ModelSelection
from band.core.retention import ReleaseCause, RoomRetentionConfig
from band.integrations.acp import client_runtime
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.acp.client_types import ACPRoom
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from band.integrations.acp.model_selection import MODEL_CATEGORY
from tests.adapters.waiting import spin, until
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    Reply,
    acp_adapter,
    decision_command,
    decision_token,
    fake_agent_config,
    released,
    select_option,
    started_acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import (
    MODEL_OPTION_ID,
    Gate,
    allow_or_reject,
)

CANCELLED = {"outcome": {"outcome": "cancelled"}}
# A transition bound short enough that waiting it out costs nothing.
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


def decisions(reply: Reply) -> list[str]:
    """Every decision prompt ``reply`` has posted: each tells the room how to reply."""
    return [text for text in reply.texts if decision_command(text) is not None]


def questions(reply: Reply) -> list[str]:
    """The Cursor question decisions ``reply`` has posted to the room."""
    return [
        text
        for text in reply.texts
        if (command := decision_command(text)) is not None
        and command.word == CursorCommandWord.ANSWER
    ]


async def question_asked(reply: Reply) -> None:
    """Let a running turn post its question decision to ``reply``'s room."""
    await until(lambda: bool(questions(reply)), failure="no question was asked")


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


def fails_prompt(agent: FakeACPAgent, number: int, error: Exception) -> None:
    """Fail the agent's ``number``-th prompt with ``error``; others answer."""

    @agent.on_prompt
    async def fail(peer: FakeACPAgent, session_id: str) -> None:
        if len(peer.prompts) == number:
            raise error


@dataclass(frozen=True)
class CursorQuestion:
    id: str
    options: tuple[str, ...]


# The one Cursor question these tests ask, and the options it offers.
MODE_QUESTION = CursorQuestion(id="mode", options=("agent", "plan"))


async def ask_mode(agent: FakeACPAgent, session_id: str) -> dict[str, object]:
    """Ask ``MODE_QUESTION`` from ``session_id`` and return the agent's answer."""
    return await agent.ask_cursor_question(
        session_id, MODE_QUESTION.id, *MODE_QUESTION.options
    )


async def ask_shell_permission(
    agent: FakeACPAgent, session_id: str
) -> dict[str, object]:
    """Ask permission to run a shell tool from ``session_id``; return the answer."""
    answer = await agent.ask_permission(
        session_id,
        ToolCallUpdate(tool_call_id="tc-shell", title="shell"),
        allow_or_reject(),
    )
    return answer.model_dump(by_alias=True, exclude_none=True)


def answer(token: str, option: str) -> str:
    """The room command answering ``MODE_QUESTION`` decision ``token`` with ``option``."""
    return (
        f"{ROOM_COMMAND} {CursorCommandWord.ANSWER} {token} {MODE_QUESTION.id}={option}"
    )


class QuestioningAgent(FakeACPAgent):
    """Asks ``MODE_QUESTION`` per prompt and records each session's answer."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: dict[str, dict[str, object]] = {}
        self.on_prompt(self.ask_mode)

    async def ask_mode(self, peer: FakeACPAgent, session_id: str) -> None:
        self.answers[session_id] = await ask_mode(peer, session_id)


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


async def restart(session: AcpSession) -> None:
    """Start the adapter again, as ``Agent.start()`` does after a stop."""
    await session.adapter.on_started("Fake Agent", "in-process fake")


def answered(option: str) -> dict[str, object]:
    return {
        "outcome": {
            "outcome": "answered",
            "answers": [
                {"questionId": MODE_QUESTION.id, "selectedOptionIds": [option]}
            ],
        }
    }


@dataclass(frozen=True)
class HeldSwitch:
    """A model switch to ``SWITCHED_MODEL`` queued behind the room's held prompt."""

    agent: FakeACPAgent
    session: AcpSession
    session_id: str
    prompting: Gate
    held: asyncio.Task[Reply]
    switch: asyncio.Task[None]


SWITCHED_MODEL = "b"


@asynccontextmanager
async def switch_behind_held_prompt() -> AsyncIterator[HeldSwitch]:
    """A bootstrapped room whose second prompt is held, with a model switch
    queued behind it."""
    agent = models("a", "a", SWITCHED_MODEL)
    prompting = holds_prompt(agent, 2)
    async with acp_adapter(agent) as session:
        await session.send("bootstrap")
        held = asyncio.create_task(session.send("held"))
        await prompting.received.wait()
        switch = asyncio.create_task(
            session.adapter.apply_model_selection(
                ModelSelection(model=SWITCHED_MODEL), room_id=DEFAULT_ROOM
            )
        )
        # The switch exposes no state while it queues on the room.
        await spin()
        yield HeldSwitch(
            agent=agent,
            session=session,
            session_id=session.session_id(DEFAULT_ROOM),
            prompting=prompting,
            held=held,
            switch=switch,
        )


def runtime_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == client_runtime.__name__ and record.levelno >= logging.WARNING
    ]


def terminate_pid(pid: int) -> None:
    with suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM)
