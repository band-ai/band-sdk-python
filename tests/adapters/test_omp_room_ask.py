"""Native ask dialogs complete a room turn without waiting for a later message."""

from __future__ import annotations

import asyncio
import sys
from functools import partial
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.core.exceptions import RoomExecutionStoppedError
from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools import AgentTools
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.integrations.acp.conftest import make_platform_message


@pytest.fixture
def ask_adapter(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.integrations.ompaskpeer",
                "ask",
            ),
        ),
        emit=(),
    )
    return adapter


async def ask_turn(
    adapter: OmpAdapter,
    tools: FakeAgentTools,
    *,
    bootstrap: bool = True,
    content: str = "Ask me which database to use",
) -> None:
    await adapter.run_judged_turn(
        msg=make_platform_message(content),
        tools=tools,
        history=OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id="room-123",
    )


async def ask_frames(adapter: OmpAdapter) -> list[dict[str, JsonValue]]:
    checkpoint = await adapter._rooms["room-123"].runtime.request("checkpoint")
    return cast(list[dict[str, JsonValue]], checkpoint["frames"])


@pytest.mark.asyncio
class TestOmpRoomAsk:
    async def test_questions_are_the_reply_and_later_answer_resumes_same_session(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        try:
            async with asyncio.timeout(5):
                await ask_turn(ask_adapter, tools)
            [posted] = tools.messages_sent
            assert posted["mentions"] == [{"id": "peer-456", "name": "Test Peer"}]
            for text in (
                "Storage",
                "Database?",
                "SQLite",
                "Embedded",
                "recommended",
                "Postgres",
                "Deploy where?",
                "Dev",
                "Prod",
            ):
                assert text in posted["content"]
            assert tools.turn.replied
            assert not failure_reports(tools)
            first_frames = await ask_frames(ask_adapter)
            [response] = [
                frame
                for frame in first_frames
                if frame["type"] == "extension_ui_response"
            ]
            assert response["id"] == "ask-1"
            assert "cancelled" not in response
            answers = cast(list[dict[str, JsonValue]], response["answers"])
            assert [answer["id"] for answer in answers] == ["storage", "targets"]
            assert all(answer["selectedOptions"] == [] for answer in answers)
            assert all(answer["customInput"] for answer in answers)
            assert "later" in str(answers[0]["customInput"])

            runtime = ask_adapter._rooms["room-123"].runtime
            resumed = FakeAgentTools()
            resumed.fetch_room_context = AsyncMock(return_value={"data": []})
            resumed.execute_tool_call_structured = partial(
                AgentTools.execute_tool_call_structured, resumed
            )
            async with asyncio.timeout(5):
                await ask_turn(
                    ask_adapter, resumed, bootstrap=False, content="Use SQLite for Dev."
                )
            assert ask_adapter._rooms["room-123"].runtime is runtime
            assert (
                resumed.messages_sent[0]["content"]
                == "The answer resumed this session."
            )
            assert resumed.turn.replied
            frames = await ask_frames(ask_adapter)
            assert len([frame for frame in frames if frame["type"] == "prompt"]) == 2
            prompts = [frame for frame in frames if frame["type"] == "prompt"]
            assert "Use SQLite for Dev." in str(prompts[-1]["message"])
            [opt_in] = [frame for frame in frames if frame["type"] == "set_ask_dialog"]
            assert opt_in["enabled"] is True
        finally:
            await ask_adapter.cleanup_all()

    @pytest.mark.parametrize(
        "error",
        [RuntimeError("platform unavailable"), RoomExecutionStoppedError("other-room")],
    )
    async def test_failed_question_post_reports_error_without_cancelling_native_ask(
        self, ask_adapter: OmpAdapter, error: Exception
    ) -> None:
        tools = FakeAgentTools()
        tools.send_message_error = error
        try:
            async with asyncio.timeout(5):
                with pytest.raises(TurnResultAlreadyReported):
                    await ask_turn(ask_adapter, tools)
            assert not tools.turn.replied
            assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
            frames = await ask_frames(ask_adapter)
            [response] = [
                frame for frame in frames if frame["type"] == "extension_ui_response"
            ]
            assert "cancelled" not in response
            answers = cast(list[dict[str, JsonValue]], response["answers"])
            assert [answer["id"] for answer in answers] == ["storage", "targets"]
            assert all(str(error) in str(answer["customInput"]) for answer in answers)
        finally:
            await ask_adapter.cleanup_all()

    async def test_stopped_current_room_ends_turn_instead_of_answering_ask(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        tools.send_message_error = RoomExecutionStoppedError("room-123")
        try:
            async with asyncio.timeout(5):
                with pytest.raises(RoomExecutionStoppedError):
                    await ask_turn(ask_adapter, tools)
            assert not tools.turn.replied
            assert not failure_reports(tools)
            assert "room-123" not in ask_adapter._rooms
        finally:
            await ask_adapter.cleanup_all()

    async def test_cancellation_during_question_post_retires_native_session(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        held = tools.hold_message("Database?")
        running = asyncio.create_task(ask_turn(ask_adapter, tools))
        try:
            async with asyncio.timeout(5):
                await held.sending.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert not tools.turn.replied
            assert not tools.messages_sent
            assert not failure_reports(tools)
            assert "room-123" not in ask_adapter._rooms
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await ask_adapter.cleanup_all()
