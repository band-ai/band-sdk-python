"""A timed-out turn's CLI is replaced before the room runs another turn."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig, TurnResultAlreadyReported
from band.core.exceptions import ProviderUnavailableError
from band.runtime.activity import tool_call_activity
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.activity.kit import native_tool_uses
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import TURN_TIMEOUT_S, OpenRoom

STUCK_CALL = "stuck-call"


@pytest.mark.looptime
class TestTimeoutRecovery:
    async def test_a_cli_that_cannot_be_released_makes_the_room_unavailable(
        self, claude_room: OpenRoom
    ) -> None:
        """Interrupting the turn proves nothing about the process: when it
        cannot be closed, the next turn fails terminally without a second
        process, and leaving the room releases it so a new turn runs again."""
        room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
        room.claude.script(
            [room.model_reply("Hello.")], [Hold()], [room.model_reply("Fresh room.")]
        )
        await room.send("hi")
        room.claude.refuse_close = True

        with pytest.raises(TurnResultAlreadyReported):
            await room.send("take forever")
        with pytest.raises(ProviderUnavailableError):
            await room.send("and now?")

        # Sticky outage: no replacement CLI while the stuck process is held.
        assert len(room.claude.sessions) == 1
        assert room.claude.sessions[0].alive
        assert len(room.claude.prompts) == 2

        room.claude.refuse_close = False
        await room.leave()
        await room.send("anyone there?")

        assert room.chat == ["Hello.", "Fresh room."]
        assert [session.alive for session in room.claude.sessions] == [False, True]
        assert len(room.claude.prompts) == 3

    async def test_a_call_of_the_replaced_cli_never_resolves_to_a_later_turn(
        self, claude_room: OpenRoom
    ) -> None:
        """The timed-out CLI's tool call lost its origin with the process, so a
        late request for it reaches neither that session's activity nor the
        successor's."""
        room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
        stuck = Hold()
        room.claude.script(
            [room.model_reply("Hello.")],
            [native_tool_uses((STUCK_CALL, "Bash")), stuck],
            [room.model_reply("Back again.")],
        )
        await room.send("hi")
        session = room.session_manager.session(room.room_id)
        assert session is not None
        recorder = ActivityRecorder()
        sending = asyncio.create_task(
            room.send("take forever", tools=recorder.observing(room.fresh_tools()))
        )
        await stuck.arrived()
        await recorder.wait_for_detail(tool_call_activity("Bash"))
        replaced = session.activity
        assert replaced is not None
        assert replaced.resolve(STUCK_CALL).tools is not None

        with pytest.raises(TurnResultAlreadyReported):
            await sending
        await room.send("and now?")

        successor = session.activity
        assert successor is not None and successor is not replaced
        for activity in (replaced, successor):
            with pytest.raises(ValueError):
                activity.resolve(STUCK_CALL)
