"""Drive Codex seed transitions against an independent delivery ledger."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import strategies as st

from band.adapters.codex import SYSTEM_CONTEXT_PREFIX, CodexAdapter, CodexAdapterConfig
from band.converters.codex import CODEX_RESUME_RECORD
from band.converters.helpers import HISTORY_REPLAY_HEADER
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.formatters import build_participants_message
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    agent_input,
    replayed_history,
    resume_records,
    stored,
)
from tests.adapters.codex.seedpeer import Step, native_turn_id
from tests.adapters.waiting import UNTIL_TIMEOUT_S, until
from tests.lifecycle import backgrounded

sequences = st.lists(st.sampled_from(Step), max_size=8)


class SeedRoom:
    """One real adapter and the room facts its delivered requests establish."""

    def __init__(self, root: Path) -> None:
        self.trace = root / "requests.jsonl"
        self.action = root / "outcome.txt"
        self.adapter = CodexAdapter(
            CodexAdapterConfig(
                model="seed-model",
                codex_command=(
                    sys.executable,
                    str(Path(__file__).with_name("seedpeer.py")),
                    str(self.trace),
                    str(self.action),
                ),
                workspace_for_room=lambda room: str(root),
            ),
            emit=(),
        )
        self.history: list[dict[str, Any]] = []
        self.outstanding: list[str] = []
        self.unsent: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.accepted_thread: str | None = None
        self.recorded_thread: str | None = None
        self.previous_thread: str | None = None
        self.post_fails = False
        self.update_number = 0
        self.turn_number = 0

    def requests(self, method: CodexRequestMethod) -> list[dict[str, Any]]:
        """Read complete requests; a final file write may still be in flight."""
        if not self.trace.exists():
            return []
        requests = [
            json.loads(line)
            for line in self.trace.read_text().splitlines(keepends=True)
            if line.endswith("\n")
        ]
        return [
            request["params"] for request in requests if request.get("method") == method
        ]

    def prompts(self) -> list[dict[str, Any]]:
        """Return every native prompt the peer accepted."""
        return self.requests(CodexRequestMethod.TURN_START)

    async def take(self, step: Step) -> None:
        match step:
            case Step.UPDATE:
                self.update_number += 1
                update = f"opaque update {self.update_number}"
                self.outstanding.append(update)
                self.unsent.append(update)
            case Step.POST_FAILS:
                self.post_fails = True
            case Step.POST_RECOVERS:
                self.post_fails = False
            case Step.COMPLETE | Step.FAIL | Step.INTERRUPT | Step.CANCEL:
                await self.turn(step)

    async def turn(self, outcome: Step) -> None:
        self.turn_number += 1
        self.action.write_text(outcome)
        tools = ToolSchemaFakeTools()
        tools.send_event_error = (
            ConnectionError("record unavailable") if self.post_fails else None
        )
        inp = replace(
            agent_input(
                f"request {self.turn_number}",
                tools,
                raw_history=list(self.history),
                is_session_bootstrap=self.turn_number == 1,
            ),
            contacts_msg="\n".join(self.unsent) or None,
        )
        tools.set_room_context([*self.history, stored(inp.msg)])
        match outcome:
            case Step.FAIL:
                with pytest.raises(TurnResultAlreadyReported):
                    await self.adapter.on_event(inp)
            case Step.CANCEL:
                async with backgrounded(self.adapter.on_event(inp)) as sending:
                    await until(
                        lambda: len(self.prompts()) == self.turn_number,
                        failure="native turn never started",
                    )
                    sending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await sending
                await self.adapter.backend_turns.settled(ROOM_ID)
                assert self.requests(CodexRequestMethod.TURN_INTERRUPT)[-1] == {
                    "threadId": self.prompts()[-1]["threadId"],
                    "turnId": native_turn_id(self.turn_number),
                }
            case Step.COMPLETE | Step.INTERRUPT:
                await self.adapter.on_event(inp)
        self.unsent.clear()
        prompt = self.prompts()[-1]
        thread_id = prompt["threadId"]
        expected_context = []
        if self.accepted_thread is None:
            assert thread_id != self.previous_thread
            expected_context.append(build_participants_message(tools.participants))
            replay = []
            if self.history:
                marker = prompt["input"][-1]["text"].split("\n", 1)[0]
                transcript = "\n".join(
                    f"[{message['sender_name']}]: {message['content']}"
                    for message in self.history
                )
                replay = [
                    f"{HISTORY_REPLAY_HEADER.format(marker=marker)}\n{transcript}"
                ]
            assert replayed_history(prompt) == replay
        else:
            assert thread_id == self.accepted_thread
            assert replayed_history(prompt) == []
        if self.outstanding:
            expected_context.append("\n".join(self.outstanding))
        actual_context = [
            item["text"].removeprefix(f"{SYSTEM_CONTEXT_PREFIX} ")
            for item in prompt["input"]
            if item["text"].startswith(SYSTEM_CONTEXT_PREFIX)
        ]
        assert actual_context == expected_context
        if outcome == Step.COMPLETE:
            self.accepted_thread = thread_id
            self.outstanding.clear()
        if (
            self.recorded_thread is None
            and self.accepted_thread
            and not self.post_fails
            and outcome in {Step.COMPLETE, Step.INTERRUPT}
        ):
            self.recorded_thread = self.accepted_thread
        self.records.extend(resume_records(tools))
        expected_records = (
            {ROOM_ID: self.recorded_thread} if self.recorded_thread else {}
        )
        assert CODEX_RESUME_RECORD.candidates(self.records) == expected_records
        assert len(self.records) == len(expected_records)
        self.previous_thread = thread_id
        self.history.append(stored(inp.msg))


@asynccontextmanager
async def seed_room(root: Path) -> AsyncIterator[SeedRoom]:
    room = SeedRoom(root)
    try:
        await room.adapter.on_started("Codex Agent", "A coding agent")
        yield room
    finally:
        await room.adapter.cleanup_all()


async def play(steps: list[Step], root: Path) -> None:
    """Exercise every generated sequence and require eventual clean delivery."""
    async with seed_room(root) as room:
        async with asyncio.timeout(UNTIL_TIMEOUT_S):
            for step in steps:
                await room.take(step)
            await room.take(Step.POST_RECOVERS)
            await room.take(Step.COMPLETE)
            await room.take(Step.COMPLETE)
