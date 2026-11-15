"""A batch is one Codex turn on the room's thread, judged once as a whole."""

from __future__ import annotations

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.codexbatch.kit import OpenRoom, reply_to_room
from tests.adapters.codexturns import final_text, tool_call_request, turn_completed
from tests.framework_conformance.batchprobes import current_records


class TestBatchTurn:
    async def test_every_original_reaches_the_app_server_in_one_turn(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(reply_to_room(), turn_completed())
        originals = room.alice_helper_alice()

        await room.send_batch(*originals)

        (turn_input,) = room.turn_inputs
        assert [record["message_id"] for record in current_records(turn_input)] == [
            m.id for m in originals
        ]

    async def test_a_command_inside_a_batch_is_text_for_the_model_not_a_command(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(reply_to_room(), turn_completed())
        originals = (room.message("/help"), room.message("list the files"))

        await room.send_batch(*originals)

        (turn_input,) = room.turn_inputs
        assert [record["message_id"] for record in current_records(turn_input)] == [
            m.id for m in originals
        ]

    async def test_one_reply_completes_the_whole_batch(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(reply_to_room(), turn_completed())

        tools = await room.send_batch(*room.alice_helper_alice())

        assert (tools.turn.complete, failure_reports(tools)) == (True, [])

    async def test_one_decline_completes_the_whole_batch(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            tool_call_request(1, "band_no_reply", {"reason": "all FYIs"}),
            turn_completed(),
        )

        tools = await room.send_batch(*room.alice_helper_alice())

        assert (tools.turn.complete, room.chat) == (True, [])

    async def test_a_batch_nobody_answered_is_reported_once(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(final_text("plain text, no tool"), turn_completed())
        inputs = room.alice_helper_alice()

        with pytest.raises(TurnResultAlreadyReported):
            await room.send_batch(*inputs)

        assert failure_reports(room.deliveries[-1]) == [MISSING_REPLY_FAILURE]

    async def test_a_later_batch_continues_the_room_thread(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            reply_to_room(), turn_completed(), reply_to_room(), turn_completed("turn-2")
        )

        await room.send_batch(*room.alice_helper_alice())
        await room.send_batch(*room.alice_helper_alice())

        first, second = room.turn_inputs
        assert room.client.request_methods.count(CodexRequestMethod.THREAD_START) == 1
        assert "[System Instructions]" in first
        assert "[System Instructions]" not in second
