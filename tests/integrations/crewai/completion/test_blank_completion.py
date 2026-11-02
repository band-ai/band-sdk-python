"""A blank CrewAI completion is retried once, and only before any tool ran."""

from __future__ import annotations

import pytest

pytest.importorskip("crewai", reason="crewai not installed (band-sdk[crewai])")

from band.adapters.crewai import CrewAIAdapter
from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.crewaiscript import ScriptedLLM, step
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY
from tests.framework_conformance.turnprobes import ToolCall, turn_input, turn_tools
from tests.integrations.crewai.completion.kit import work

BLANKS = ["", None, "\n", " \t\r\n"]


class TestBlankCompletion:
    @pytest.mark.parametrize("blank", BLANKS)
    async def test_blank_completion_recovers_with_one_reply(
        self, adapter: CrewAIAdapter, model: ScriptedLLM, blank: str | None
    ) -> None:
        model.responses.extend([blank, step(REPLY), "\n"])
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert tools.chat == [ANSWER]
        assert failure_reports(tools) == []
        assert not model.responses

    @pytest.mark.parametrize("blank", BLANKS)
    async def test_blank_completions_exhaust_with_one_missing_reply(
        self, adapter: CrewAIAdapter, model: ScriptedLLM, blank: str | None
    ) -> None:
        model.responses.extend([blank, blank])
        tools = turn_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert tools.chat == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert not model.responses

    @pytest.mark.parametrize(
        ("call", "chat"),
        [(REPLY, [ANSWER]), (ToolCall(BandTool.CREATE_CHATROOM), [])],
        ids=["reply", "action"],
    )
    async def test_a_completing_tool_needs_no_retry(
        self,
        adapter: CrewAIAdapter,
        model: ScriptedLLM,
        call: ToolCall,
        chat: list[str],
    ) -> None:
        model.responses.extend([step(call), "\n"])
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert tools.chat == chat
        assert failure_reports(tools) == []
        assert not model.responses

    @pytest.mark.parametrize(
        "call",
        [
            ToolCall(BandTool.GET_PARTICIPANTS),
            ToolCall(BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": []}),
        ],
        ids=["read-only", "failed-send"],
    )
    async def test_a_tool_without_work_is_not_retried_and_reports_a_missing_reply(
        self, adapter: CrewAIAdapter, model: ScriptedLLM, call: ToolCall
    ) -> None:
        model.responses.extend([step(call), "\n"])
        tools = turn_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert tools.chat == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert not model.responses

    async def test_a_custom_action_is_not_repeated_after_blank_output(
        self, adapter: CrewAIAdapter, model: ScriptedLLM, executions: list[bool]
    ) -> None:
        model.responses.extend([step(work(fail=False)), "\n"])
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert executions == [False]
        assert failure_reports(tools) == []
        assert not model.responses

    async def test_a_failed_custom_action_is_not_repeated_and_reports_once(
        self, adapter: CrewAIAdapter, model: ScriptedLLM, executions: list[bool]
    ) -> None:
        model.responses.extend([step(work(fail=True)), "\n"])
        tools = turn_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert executions == [True]
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert not model.responses
