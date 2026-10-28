"""A batch is one Claude turn in the room's session, judged once as a whole."""

from __future__ import annotations

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.runtime.formatters import CURRENT_INPUTS_HEADING
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.claude_sdk.batch.kit import OpenRoom, alice_helper_alice
from tests.adapters.claude_sdk.helpers import said
from tests.baseline.decisions import ModelDecision
from tests.framework_conformance.batchprobes import current_records


class TestBatchTurn:
    async def test_every_original_reaches_the_cli_in_one_prompt(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        originals = alice_helper_alice(room)
        room.claude.script([room.model_reply("Done.")])

        await room.send_batch(*originals)

        (prompt,) = room.claude.prompts
        assert [record["message_id"] for record in current_records(prompt)] == [
            m.id for m in originals
        ]
        assert room.chat == ["Done."]

    async def test_one_reply_completes_the_whole_batch(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        tools = room.fresh_tools()
        room.claude.script([room.model_reply("Done.")])

        await room.send_batch(*alice_helper_alice(room), tools=tools)

        assert (tools.turn.complete, failure_reports(tools)) == (True, [])

    async def test_one_decline_completes_the_whole_batch(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        tools = room.fresh_tools()
        room.claude.script([room.model_call("band_no_reply", reason="all FYIs")])

        await room.send_batch(*alice_helper_alice(room), tools=tools)

        assert (tools.turn.complete, room.chat) == (True, [])

    async def test_a_batch_nobody_answered_is_reported_once(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        tools = room.fresh_tools()
        room.claude.script([ModelDecision.text_reply("plain text, no tool")])

        with pytest.raises(TurnResultAlreadyReported):
            await room.send_batch(*alice_helper_alice(room), tools=tools)

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_a_later_batch_continues_the_live_session_without_replaying_history(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("First.")], [room.model_reply("Second.")])

        await room.send_batch(
            *alice_helper_alice(room), history=(said("Alice", "remember-the-milk"),)
        )
        await room.send_batch(*alice_helper_alice(room))

        first, second = room.claude.prompts
        assert room.claude.resumed == [None]
        assert "remember-the-milk" in first
        assert "remember-the-milk" not in second
        assert CURRENT_INPUTS_HEADING in second
