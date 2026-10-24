"""Native ask dialogs complete a room turn without waiting for a later message."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from textwrap import indent
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from band.adapters.omp import OmpAdapter
from band.core.exceptions import RoomExecutionStoppedError
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.ompwire import OmpCommandName, OmpFrameType
from band.runtime.tools import AgentTools
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.omp.askkit import (
    AskStartupObserver,
    ask_frames,
    ask_session_changes,
    ask_turn,
    room_ask_adapter,
)
from tests.adapters.omp.kit import (
    claimed_workspace_rooms,
    occupied_room_ids,
    room_runtime,
)
from tests.integrations.ompaskpeer import (
    ASK_QUESTION_IDS,
    ASK_REQUEST_ID,
    DATABASE_DESCRIPTION,
    DATABASE_HEADER,
    DATABASE_OPTIONS,
    DATABASE_QUESTION,
    DEPLOY_OPTIONS,
    DEPLOY_QUESTION,
    PREVIEW_CHOICES,
    PREVIEW_ONLY,
    PREVIEW_QUESTION,
    REFUSED_DIALOG,
    RESUMED_REPLY,
)


@pytest.mark.asyncio
class TestOmpRoomAsk:
    async def test_questions_are_the_reply_and_later_answer_resumes_same_session(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        try:
            await ask_turn(ask_adapter, tools)
            [posted] = tools.messages_sent
            assert posted["mentions"] == [{"id": "peer-456", "name": "Test Peer"}]
            for text in (
                DATABASE_HEADER,
                DATABASE_QUESTION,
                *DATABASE_OPTIONS,
                DATABASE_DESCRIPTION,
                "recommended",
                DEPLOY_QUESTION,
                *DEPLOY_OPTIONS,
            ):
                assert text in posted["content"]
            assert tools.turn.replied
            assert not failure_reports(tools)
            [response] = await ask_frames(ask_adapter, OmpFrameType.UI_RESPONSE)
            assert response["id"] == ASK_REQUEST_ID
            assert "cancelled" not in response
            answers = cast(list[dict[str, JsonValue]], response["answers"])
            assert [answer["id"] for answer in answers] == list(ASK_QUESTION_IDS)
            assert all(answer["selectedOptions"] == [] for answer in answers)
            assert all(answer["customInput"] for answer in answers)
            assert "later" in str(answers[0]["customInput"])

            session_changes = await ask_session_changes(ask_adapter)
            runtime = room_runtime(ask_adapter, "room-123")
            resumed = FakeAgentTools()
            resumed.fetch_room_context = AsyncMock(return_value={"data": []})
            resumed.execute_tool_call_structured = partial(
                AgentTools.execute_tool_call_structured, resumed
            )
            await ask_turn(
                ask_adapter, resumed, bootstrap=False, content="Use SQLite for Dev."
            )
            assert room_runtime(ask_adapter, "room-123") is runtime
            assert await ask_session_changes(ask_adapter) == session_changes
            assert resumed.messages_sent[0]["content"] == RESUMED_REPLY
            assert resumed.turn.replied
            prompts = await ask_frames(ask_adapter, OmpCommandName.PROMPT)
            assert len(prompts) == 2
            assert "Use SQLite for Dev." in str(prompts[-1]["message"])
            [opt_in] = await ask_frames(ask_adapter, OmpCommandName.SET_ASK_DIALOG)
            assert opt_in["enabled"] is True
        finally:
            await ask_adapter.cleanup_all()

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("private delivery details: bearer-canary"),
            RoomExecutionStoppedError("other-room"),
        ],
    )
    async def test_failed_question_post_reports_error_without_cancelling_native_ask(
        self,
        ask_adapter: OmpAdapter,
        error: Exception,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tools = FakeAgentTools()
        tools.send_message_error = error
        try:
            with (
                caplog.at_level(logging.WARNING, logger=OmpAdapter.__module__),
                pytest.raises(TurnResultAlreadyReported),
            ):
                await ask_turn(ask_adapter, tools)
            [warning] = [
                record
                for record in caplog.records
                if record.name == OmpAdapter.__module__
            ]
            assert warning.levelno == logging.WARNING
            diagnostic = warning.getMessage()
            assert str(error) not in diagnostic
            assert "room-123" not in diagnostic
            assert type(error).__name__ in diagnostic
            assert not tools.turn.replied
            assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
            [response] = await ask_frames(ask_adapter, OmpFrameType.UI_RESPONSE)
            assert "cancelled" not in response
            answers = cast(list[dict[str, JsonValue]], response["answers"])
            assert [answer["id"] for answer in answers] == list(ASK_QUESTION_IDS)
            assert all(str(error) in str(answer["customInput"]) for answer in answers)
        finally:
            await ask_adapter.cleanup_all()

    async def test_stopped_current_room_ends_turn_instead_of_answering_ask(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        tools.send_message_error = RoomExecutionStoppedError("room-123")
        try:
            with pytest.raises(RoomExecutionStoppedError):
                await ask_turn(ask_adapter, tools)
            assert not tools.turn.replied
            assert not failure_reports(tools)
            assert "room-123" not in occupied_room_ids(ask_adapter)
        finally:
            await ask_adapter.cleanup_all()

    async def test_cancellation_during_question_post_retires_native_session(
        self, ask_adapter: OmpAdapter
    ) -> None:
        tools = FakeAgentTools()
        held = tools.hold_message(DATABASE_QUESTION)
        running = asyncio.create_task(ask_turn(ask_adapter, tools))
        try:
            await held.sending.wait()
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert not tools.turn.replied
            assert not tools.messages_sent
            assert not failure_reports(tools)
            assert "room-123" not in occupied_room_ids(ask_adapter)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await ask_adapter.cleanup_all()

    async def test_refused_dialog_releases_startup_without_prompting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        observer = AskStartupObserver()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", observer)
        adapter = room_ask_adapter(tmp_path, REFUSED_DIALOG)
        tools = FakeAgentTools()
        try:
            with pytest.raises(RuntimeError, match="refused the structured ask dialog"):
                await ask_turn(adapter, tools)
            assert not observer.prompted
            assert not tools.messages_sent
            assert not tools.turn.replied
            assert occupied_room_ids(adapter) == set()
            assert claimed_workspace_rooms(adapter) == ()
            await observer.assert_released()
        finally:
            await adapter.cleanup_all()

    async def test_preview_only_choices_reach_the_room(self, tmp_path: Path) -> None:
        adapter = room_ask_adapter(tmp_path, PREVIEW_ONLY)
        tools = FakeAgentTools()
        try:
            await ask_turn(adapter, tools)
            [posted] = tools.messages_sent
            assert PREVIEW_QUESTION in posted["content"]
            for label, preview in PREVIEW_CHOICES:
                assert f"- {label}" in posted["content"]
                assert indent(preview, "  ") in posted["content"]
            assert tools.turn.replied
            assert not failure_reports(tools)
        finally:
            await adapter.cleanup_all()
