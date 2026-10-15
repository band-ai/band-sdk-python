from __future__ import annotations

import pytest
from acp import RequestError
from acp.helpers import start_tool_call, update_tool_call

from band.core.exceptions import InputNotAccepted
from band.integrations.acp.client_types import (
    SESSION_BUSY_CODE,
    SESSION_BUSY_REASON,
    ACPClientSessionState,
    session_busy_error,
)
from band.runtime.prompts import (
    COMMUNICATION_INSTRUCTIONS,
)
from band.testing import FakeAgentTools
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
)
from tests.integrations.acp.clientbehavior.kit import (
    REPLAY_HEADER_LINE,
    rehydration_history,
)
from tests.integrations.acp.conftest import make_platform_message


class TestPermissionAdmission:
    @pytest.mark.asyncio
    async def test_ordinary_tool_permission_granted_without_a_bubble(
        self, fake_agent
    ) -> None:
        """An ordinary tool's permission is auto-granted silently — no permission pair.

        The tool's own tool_call/tool_result already show the call, so posting a
        permission pair too would duplicate it in the room.
        """
        fake_agent.will_ask_permission(
            tool_call_id="tc-1", title="band_lookup_peers"
        ).will_say("done")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("do the thing")

        assert (
            fake_agent.approved is True
        )  # the round-trip still granted the allow option
        assert (
            reply.permissions == []
        )  # no duplicate permission pair for an ordinary tool
        assert "done" in reply.thoughts  # the turn proceeded after approval

    @pytest.mark.asyncio
    @pytest.mark.parametrize("restored", [False, True])
    async def test_busy_first_prompt_preserves_session_context(
        self, restored: bool
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        if restored:
            agent.knows_session("session-1")
        history = rehydration_history(
            "[Marco]: My favorite color is blue.",
            session="session-1" if restored else None,
        )

        @agent.on_prompt
        async def reject_once(peer: FakeACPAgent, sid: str) -> None:
            if len(peer.prompts) == 1:
                raise session_busy_error()
            await peer.say(sid, "Blue.")

        async with acp_adapter(agent) as session:
            with pytest.raises(InputNotAccepted):
                await session.send(
                    "What is my favorite color?", bootstrap=True, history=history
                )
            assert session.last_reply.outline == []
            reply = await session.send(
                "What is my favorite color?", bootstrap=True, history=history
            )
            await session.send("Next question")

        assert [request["session_id"] for request in agent.prompts] == [
            agent.prompts[0]["session_id"]
        ] * 3
        rejected, accepted, subsequent = agent.prompt_texts()
        assert COMMUNICATION_INSTRUCTIONS in rejected
        assert COMMUNICATION_INSTRUCTIONS in accepted
        assert COMMUNICATION_INSTRUCTIONS not in subsequent
        assert (REPLAY_HEADER_LINE in accepted) is not restored
        assert ("[Marco]:" in accepted) is not restored
        assert REPLAY_HEADER_LINE not in subsequent
        assert reply.thoughts == ["Blue."]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "data"),
        [
            (SESSION_BUSY_CODE, None),
            (SESSION_BUSY_CODE, {"reason": "other"}),
            (-32603, {"reason": SESSION_BUSY_REASON}),
        ],
    )
    async def test_only_exact_typed_busy_response_preserves_session(
        self, code: int, data: dict[str, str] | None
    ) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def fail_once(peer: FakeACPAgent, sid: str) -> None:
            if len(peer.prompts) == 1:
                raise RequestError(code, "Session is busy", data)
            await peer.say(sid, "Recovered.")

        async with acp_adapter(agent) as session:
            with pytest.raises(RequestError):
                await session.send("first")
            assert "error" in session.last_reply.outline
            await session.send("second")

        assert len(agent.sessions) == 2

    @pytest.mark.asyncio
    async def test_rejected_acp_activity_cannot_complete_incoming_turn(self) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def autonomous_activity(peer: FakeACPAgent, sid: str) -> None:
            await peer.emit(sid, start_tool_call("other-work", "band_no_reply"))
            await peer.emit(
                sid,
                update_tool_call("other-work", raw_output="Done", status="completed"),
            )
            await peer.say(sid, "Autonomous final text")
            raise session_busy_error()

        tools = FakeAgentTools()
        async with acp_adapter(agent) as session:
            with pytest.raises(InputNotAccepted):
                await session.adapter.on_message(
                    make_platform_message("Pending work", room_id="room-1"),
                    tools,
                    ACPClientSessionState(),
                    None,
                    None,
                    is_session_bootstrap=True,
                    room_id="room-1",
                )

        assert not tools.turn.complete
        assert not tools.turn.replied
        assert tools.messages_sent == []
        assert all(event["message_type"] != "error" for event in tools.events_sent)
