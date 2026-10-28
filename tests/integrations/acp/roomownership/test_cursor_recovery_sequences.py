"""Cursor turn ownership survives repeated recovery, settings queues and room leave."""

from __future__ import annotations

import asyncio

import pytest
from acp import RequestError

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.core.model_catalog import ModelSelection
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.waiting import spin
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    accepted_work_finished,
)
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID, Gate
from tests.integrations.acp.acp_toolkit.harness import SlowFailureReports
from tests.integrations.acp.roomownership.kit import (
    CANCELLED,
    TEST_GUARD_S,
    ask_mode,
    cursor,
    models,
)


class TestCursorRecoverySequences:
    @pytest.mark.asyncio
    async def test_repeated_failures_recover_the_conversation_then_serve_a_new_turn(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        failure = RequestError.internal_error()

        @agent.on_prompt
        async def fail_twice(peer: FakeACPAgent, session_id: str) -> None:
            peer.knows_session(session_id)
            if len(peer.prompts) in (2, 3):
                raise failure
            await peer.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={"content": "recovered reply", "mentions": ["@peer-1"]},
            )

        async with cursor(agent, inject_band_tools=True) as session:
            await session.send_judged("seeding turn")
            failures = []
            for message in ("first failure", "second failure"):
                with pytest.raises(RequestError):
                    await session.send_judged(message)
                failures.append(session.last_reply)
            retained = session.session_id(DEFAULT_ROOM)
            served = await session.send_judged("healthy turn")
            await accepted_work_finished(session.room())

            assert served.texts == ["recovered reply"]
            assert served.errors == []
            assert [reply.errors for reply in failures] == [[str(failure)]] * 2
            assert agent.session_load_requests == [retained, retained]
            assert agent.session_ids() == [retained]

    @pytest.mark.asyncio
    async def test_a_switch_waiting_behind_a_parked_turn_survives_stop(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        served_models: list[str | None] = []
        native_finishing = Gate()
        native_finished = asyncio.Event()
        cancelled_outcomes: list[dict[str, object]] = []

        @agent.on_prompt
        async def ask_then_reply(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                cancelled_outcomes.append(await ask_mode(peer, session_id))
                await native_finishing.hold()
                native_finished.set()
                return
            served_models.append(peer.current_value(MODEL_OPTION_ID))
            await peer.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={"content": "selected model served", "mentions": ["@peer-1"]},
            )

        async with cursor(agent, inject_band_tools=True) as session:
            parked = await session.send_judged("ask")
            switching = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )
            )
            try:
                async with asyncio.timeout(TEST_GUARD_S):
                    await spin()
                    assert not switching.done()
                    assert agent.config_selections() == []
                    await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.STOP)
                    await switching
                    await native_finishing.received.wait()
                    native_finishing.release.set()
                    await native_finished.wait()
                    await accepted_work_finished(session.room())
                    served = await session.send_judged("next turn")
                    await accepted_work_finished(session.room())
            finally:
                native_finishing.release.set()
                await asyncio.gather(switching, return_exceptions=True)

            assert parked.errors == []
            assert cancelled_outcomes == [CANCELLED]
            assert served_models == ["b"]
            assert served.texts == ["selected model served"]
            assert served.errors == []
            assert agent.config_selections() == [(MODEL_OPTION_ID, "b")]
            assert agent.cancelled_sessions == [session.session_id(DEFAULT_ROOM)]

    @pytest.mark.asyncio
    async def test_leave_joins_a_failure_report_before_rejoin_while_another_room_serves(
        self,
    ) -> None:
        agent = FakeACPAgent()
        failure = RequestError.internal_error()

        @agent.on_prompt
        async def fail_first(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                raise failure
            await peer.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={"content": session_id, "mentions": ["@peer-1"]},
            )

        async with cursor(agent, inject_band_tools=True) as session:
            old_tools = SlowFailureReports(lost=0)
            failing = asyncio.create_task(session.send_judged("fail", tools=old_tools))
            leaving = None
            try:
                async with asyncio.timeout(TEST_GUARD_S):
                    await old_tools.reporting.received.wait()
                    (old_session,) = agent.session_ids()
                    leaving = asyncio.create_task(
                        session.adapter.on_cleanup(DEFAULT_ROOM)
                    )
                    other = await session.send_judged("other room", room="other-room")
                    with pytest.raises(InputNotAccepted):
                        await session.send("too early")
                    assert not leaving.done()
                    old_tools.reporting.release.set()
                    with pytest.raises(RequestError):
                        await failing
                    await leaving
                    rejoined = await session.send_judged("rejoin")
                    await accepted_work_finished(session.room())
            finally:
                old_tools.reporting.release.set()
                pending = [failing] if leaving is None else [failing, leaving]
                await asyncio.gather(*pending, return_exceptions=True)

            assert old_tools.reply.errors == [str(failure)]
            assert old_tools.reply.texts == []
            assert other.errors == rejoined.errors == []
            assert other.texts == [session.session_id("other-room")]
            assert rejoined.texts == [session.session_id(DEFAULT_ROOM)]
            assert old_session != session.session_id(DEFAULT_ROOM)
