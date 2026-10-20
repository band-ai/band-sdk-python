from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.framework_conformance.turnprobes import turn_input, turn_tools

if TYPE_CHECKING:
    from band.adapters.crewai import CrewAIAdapter as CrewAIAdapterType

from tests.adapters.crewai.kit import (
    ACT,
    ANSWER,
    DECLINE,
    EMPTY_COMPLETION,
    FAILED_REPLY,
    LOOKUP,
    NARRATION,
    REPLY,
    ToolStep,
    scripted_crew,
    started,
)


class TestTurnVerdict:
    """A CrewAI turn ends on the shared verdict, not on CrewAI's exceptions."""

    @pytest.mark.parametrize(
        ("calls", "ending"),
        [
            ((), "I could not complete the request."),
            ((), None),
            ((LOOKUP,), "\n"),
            ((FAILED_REPLY,), " \t"),
            ((LOOKUP,), EMPTY_COMPLETION),
            ((FAILED_REPLY, NARRATION), EMPTY_COMPLETION),
        ],
        ids=[
            "final-text-only",
            "falsey-result",
            "read-only-blank",
            "failed-send-blank",
            "read-only",
            "failed-send-then-event",
        ],
    )
    async def test_a_turn_that_neither_replied_nor_worked_is_reported_once(
        self,
        CrewAIAdapter: type[CrewAIAdapterType],
        calls: tuple[ToolStep, ...],
        ending: str | Exception | None,
    ) -> None:
        """The final text is discarded: this agent answers only through tools."""
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        adapter._crewai_agent = scripted_crew(adapter, *calls, ending=ending)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert tools.messages_sent == []
        if calls:
            assert adapter._crewai_agent.kickoff_async.call_count == 1

    @pytest.mark.parametrize(
        ("calls", "ending"),
        [
            ((REPLY,), EMPTY_COMPLETION),
            ((REPLY,), "I already sent the user-facing reply."),
            ((DECLINE,), EMPTY_COMPLETION),
            ((ACT,), EMPTY_COMPLETION),
            ((ACT,), "Done."),
            ((REPLY,), "\n"),
            ((DECLINE,), "\n"),
            ((ACT,), "\n"),
        ],
        ids=[
            "reply-then-empty",
            "reply-then-text",
            "decline-then-empty",
            "act-then-empty",
            "act-then-text",
            "reply-then-blank",
            "decline-then-blank",
            "act-then-blank",
        ],
    )
    async def test_a_turn_that_replied_declined_or_worked_completes(
        self,
        CrewAIAdapter: type[CrewAIAdapterType],
        calls: tuple[ToolStep, ...],
        ending: str | Exception | None,
    ) -> None:
        """CrewAI's empty-completion error is how a tool-only turn ends."""
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        adapter._crewai_agent = scripted_crew(adapter, *calls, ending=ending)

        await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == []
        assert adapter._crewai_agent.kickoff_async.call_count == 1

    @pytest.mark.parametrize("ending", [EMPTY_COMPLETION, None, "", "\n", " \t\r\n"])
    async def test_an_empty_first_call_is_retried_once_then_reported(
        self, CrewAIAdapter: type[CrewAIAdapterType], ending: str | Exception | None
    ) -> None:
        """Nothing ran before an empty first call, so one retry duplicates
        nothing; a second empty call ends a turn that did nothing."""
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        adapter._crewai_agent = scripted_crew(adapter, ending=ending)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert adapter._crewai_agent.kickoff_async.call_count == 2
        first, second = adapter._crewai_agent.kickoff_async.call_args_list
        assert first == second

    @pytest.mark.parametrize("ending", [EMPTY_COMPLETION, None, "", "\n", " \t\r\n"])
    async def test_an_empty_first_call_recovers_on_retry(
        self, CrewAIAdapter: type[CrewAIAdapterType], ending: str | Exception | None
    ) -> None:
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        replying = scripted_crew(adapter, REPLY, ending=EMPTY_COMPLETION)

        async def kickoff(prompt: str) -> Any:
            if crew.kickoff_async.call_count == 1:
                if isinstance(ending, Exception):
                    raise ending
                return None if ending is None else MagicMock(raw=ending)
            return await replying.kickoff_async(prompt)

        crew = MagicMock(kickoff_async=AsyncMock(side_effect=kickoff))
        adapter._crewai_agent = crew

        await adapter.on_event(turn_input(tools))

        assert tools.chat == [ANSWER]
        assert failure_reports(tools) == []
        assert crew.kickoff_async.call_count == 2

    @pytest.mark.parametrize("retry", [False, True])
    @pytest.mark.parametrize(
        "error", [RuntimeError("provider unavailable"), asyncio.CancelledError()]
    )
    async def test_unrelated_errors_and_cancellation_propagate(
        self, CrewAIAdapter: type[CrewAIAdapterType], retry: bool, error: BaseException
    ) -> None:
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        responses = ([MagicMock(raw="\n")] if retry else []) + [error]
        crew = MagicMock(kickoff_async=AsyncMock(side_effect=responses))
        adapter._crewai_agent = crew

        with pytest.raises(type(error)):
            await adapter.on_event(turn_input(tools))

        expected = (
            []
            if isinstance(error, asyncio.CancelledError)
            else [("crewai", GENERIC_PROVIDER_FAILURE_MESSAGE)]
        )
        assert failure_reports(tools) == expected
        assert crew.kickoff_async.call_count == (2 if retry else 1)

    @pytest.mark.parametrize("ending", [EMPTY_COMPLETION, "\n", "  exact final text\n"])
    async def test_the_next_turn_remembers_what_the_agent_posted(
        self, CrewAIAdapter: type[CrewAIAdapterType], ending: str | Exception | None
    ) -> None:
        """A turn whose work was a band_send_message must leave that message in
        the room's history. CrewAI's final answer rarely repeats it, and an
        agent that can't see it already asked a peer asks again whenever the
        peer's reply wakes it -- the loop a coordinator falls into."""
        adapter = await started(CrewAIAdapter)
        crew = scripted_crew(adapter, REPLY, ending=ending)
        adapter._crewai_agent = crew

        await adapter.on_event(turn_input(turn_tools()))
        await adapter.on_event(
            replace(turn_input(turn_tools()), is_session_bootstrap=False)
        )

        second_prompt = crew.kickoff_async.call_args_list[1].args[0]
        earlier, _, _ = second_prompt.partition("[New message -- act on this now:]")
        assert f"(to @alice) {ANSWER}" in earlier
        assert earlier.count("assistant:") == (
            2 if isinstance(ending, str) and ending.strip() else 1
        )
        if isinstance(ending, str) and ending.strip():
            assert f"assistant: {ending}" in earlier
