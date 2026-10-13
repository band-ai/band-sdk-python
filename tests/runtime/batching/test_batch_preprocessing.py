"""One input over several deliveries shares one context commit and history load."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.preprocessing.default import DefaultPreprocessor
from tests.conftest import make_message_event

AGENT_ID = "agent-1"


class TestBatchPreprocessing:
    @pytest.mark.asyncio
    async def test_originals_stay_separate_and_in_order(self, ctx: MagicMock) -> None:
        events = [
            make_message_event(msg_id="m1", sender_id="alice", content="first"),
            make_message_event(msg_id="m2", sender_id="bob", content="second"),
        ]

        inp = await DefaultPreprocessor().process_batch(ctx, events, AGENT_ID)

        assert inp is not None
        assert [(m.id, m.sender_id, m.content) for m in inp.messages] == [
            ("m1", "alice", "first"),
            ("m2", "bob", "second"),
        ]
        assert inp.msg is inp.messages[0]

    @pytest.mark.asyncio
    async def test_the_agents_own_messages_are_not_part_of_the_turn(
        self, ctx: MagicMock
    ) -> None:
        events = [
            make_message_event(msg_id="m1", sender_id=AGENT_ID, sender_type="Agent"),
            make_message_event(msg_id="m2", sender_id="bob"),
        ]

        inp = await DefaultPreprocessor().process_batch(ctx, events, AGENT_ID)

        assert inp is not None
        assert [m.id for m in inp.messages] == ["m2"]

    @pytest.mark.asyncio
    async def test_a_batch_of_nothing_addressable_builds_no_input(
        self, ctx: MagicMock
    ) -> None:
        own = make_message_event(sender_id=AGENT_ID, sender_type="Agent")

        assert await DefaultPreprocessor().process_batch(ctx, [own], AGENT_ID) is None

    @pytest.mark.asyncio
    async def test_context_is_committed_once_for_the_whole_turn(
        self, ctx: MagicMock
    ) -> None:
        events = [
            make_message_event(msg_id="m1", sender_id="alice"),
            make_message_event(msg_id="m2", sender_id="bob"),
        ]

        inp = await DefaultPreprocessor().process_batch(ctx, events, AGENT_ID)

        assert inp is not None and inp.commit_context is not None
        inp.commit_context()
        ctx.mark_llm_initialized.assert_called_once_with()
