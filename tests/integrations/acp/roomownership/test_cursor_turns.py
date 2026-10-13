"""Every accepted Cursor turn is reachable by its room's controls."""

from __future__ import annotations

import asyncio

import pytest
from acp import RequestError

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from tests.conftest import eager_tasks, requires_eager_tasks
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    accepted_work_finished,
)
from tests.integrations.acp.acp_toolkit.agent import Gate
from tests.integrations.acp.acp_toolkit.harness import (
    SlowFailureReports,
    TranscriptTools,
)
from tests.integrations.acp.roomownership.kit import (
    answered,
    cursor,
    question_asked,
    question_token,
)


class TestCursorTurns:
    """Every accepted Cursor turn is reachable by its room's controls."""

    @pytest.mark.asyncio
    async def test_an_interrupt_during_startup_ends_the_turn_and_frees_the_room(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("hello"))
            await starting.received.wait()

            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.STOP)
            starting.release.set()

            with pytest.raises(asyncio.CancelledError):
                await turn
            assert agent.prompts == []

            served = await session.send("again")
            await accepted_work_finished(session.room())

            assert served.errors == []
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_an_interrupt_before_any_decision_cancels_the_prompt(
        self,
    ) -> None:
        agent = FakeACPAgent()
        prompting = Gate()

        @agent.on_prompt
        async def hold(peer: FakeACPAgent, session_id: str) -> None:
            prompting.received.set()
            await prompting.release.wait()

        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("hello"))
            await prompting.received.wait()

            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.INTERRUPT)

            with pytest.raises(asyncio.CancelledError):
                await turn
            assert agent.cancelled_sessions == [session.session_id(DEFAULT_ROOM)]
            prompting.release.set()

    @pytest.mark.asyncio
    async def test_a_message_during_a_parked_turn_is_left_for_a_retry(
        self,
    ) -> None:
        """The host keeps it, so the parked turn's answer still gets through
        and the message runs once the room is free."""
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def ask_first(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                answers.append(
                    await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                )

        async with cursor(agent) as session:
            asked = await session.send("ask")
            refused = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=refused)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(session.room())
            retried = await session.send("next")

        assert refused.reply.texts == [] and refused.reply.errors == []
        assert answers == [answered("plan")]
        assert len(agent.prompts) == 2 and retried.errors == []

    @pytest.mark.asyncio
    async def test_a_message_during_an_interrupted_turns_unwind_is_not_failed(
        self,
    ) -> None:
        """Cursor may still be finishing a cancelled prompt when the next one
        arrives; its ``session_busy`` refusal leaves the message for a retry."""
        agent = FakeACPAgent()
        cancelled = asyncio.Event()

        @agent.on_prompt
        async def busy_after_cancel(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                await cancelled.wait()
                return
            raise RequestError(-32003, "busy", {"reason": "session_busy"})

        async with cursor(agent) as session:
            await session.send("long task")
            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.INTERRUPT)
            unwinding = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=unwinding)
            cancelled.set()
            await accepted_work_finished(session.room())
            busy = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=busy)

        assert unwinding.reply.errors == [] and busy.reply.errors == []
        assert len(agent.prompts) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "lost"),
        [
            # The agent's error is reported, then judged once that post is lost.
            (RequestError.internal_error(), 1),
            (RequestError(-32003, "busy", {"reason": "session_busy"}), 0),
        ],
        ids=["agent-error", "session-busy"],
    )
    async def test_a_slow_failure_report_keeps_the_room_until_it_lands(
        self, failure: RequestError, lost: int
    ) -> None:
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def fail_then_ask(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                raise failure
            answers.append(
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
            )

        async with cursor(agent) as session:
            failing = SlowFailureReports(lost=lost)
            asked = await session.send("ask", tools=failing)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await failing.reporting.received.wait()

            with pytest.raises(InputNotAccepted):
                await session.send("next")
            failing.reporting.release.set()
            await accepted_work_finished(session.room())
            retried = await session.send("next")
            await question_asked(retried)
            await session.send(f"/cursor answer {question_token(retried)} mode=agent")
            await accepted_work_finished(session.room())

        assert answers == [answered("agent")]

    @pytest.mark.asyncio
    async def test_a_report_lost_in_the_lease_is_not_retried_after_it(
        self,
    ) -> None:
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def busy_then_ask(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                raise RequestError(-32003, "busy", {"reason": "session_busy"})
            answers.append(
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
            )

        async with cursor(agent) as session:
            failing = SlowFailureReports(lost=1)
            asked = await session.send("ask", tools=failing)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(session.room())
            retried = await session.send("next")
            await question_asked(retried)
            await session.send(f"/cursor answer {question_token(retried)} mode=agent")
            await accepted_work_finished(session.room())

            assert not failing.reporting.received.is_set()
        assert answers == [answered("agent")]

    @requires_eager_tasks
    @pytest.mark.asyncio
    async def test_a_closing_room_refuses_a_message_under_an_eager_task_factory(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with cursor(agent) as session:
            await session.send("hello")
            leaving = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await asyncio.sleep(0)
            assert session.room().retained.closing
            tools = TranscriptTools()

            with eager_tasks(), pytest.raises(InputNotAccepted):
                await session.send("late", tools=tools)
            await leaving

            assert tools.transcript == []
        assert len(agent.prompts) == 1
