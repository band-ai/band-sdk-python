from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.core.types import (
    USAGE_METADATA_KEY,
    Emit,
    is_usage_event,
)
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.strands.kit import (
    _INPUT_TOKENS_PER_CALL,
    _OUTPUT_TOKENS_PER_CALL,
    ROOM,
    SEND_TURN,
    _alternates,
    _run_message,
)


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_send_message_turn_dispatches_and_persists_history(
        self, tools, scripted
    ):
        adapter = await scripted(SEND_TURN)

        await _run_message(adapter, tools)

        tools.assert_message_sent(content="hi", mentions=["@tester"], count=1)
        # user prompt + toolUse + toolResult + final text
        assert len(adapter._message_history[ROOM]) == 4

    @pytest.mark.asyncio
    async def test_bootstrap_rehydrates_history(self, tools, scripted):
        adapter = await scripted(SEND_TURN)
        prior = [
            {"role": "user", "content": [{"text": "[Tester]: earlier question"}]},
            {"role": "assistant", "content": [{"text": "earlier answer"}]},
        ]

        await _run_message(adapter, tools, history=list(prior))

        persisted = adapter._message_history[ROOM]
        assert persisted[:2] == prior
        assert len(persisted) > 2  # this turn appended on top

    @pytest.mark.asyncio
    async def test_later_turns_keep_the_transcript_the_adapter_owns(
        self, tools, scripted
    ):
        """Only a session bootstrap reseeds from platform history.

        A later turn that reseeded would replay the room's own transcript on top
        of the one the adapter is already holding.
        """
        adapter = await scripted(SEND_TURN)
        await _run_message(adapter, tools, history=[])
        after_first = list(adapter._message_history[ROOM])

        await _run_message(adapter, tools, history=[], is_session_bootstrap=False)

        assert adapter._message_history[ROOM][: len(after_first)] == after_first

    @pytest.mark.asyncio
    async def test_platform_context_rides_the_turn_it_belongs_to(self, tools, scripted):
        """Strands appends the prompt itself, so context posted as its own
        message would leave two user turns in a row — rejected by Converse."""
        adapter = await scripted(SEND_TURN)

        await _run_message(
            adapter,
            tools,
            participants_msg="Alice joined",
            contacts_msg="Bob is now a contact",
        )

        history = adapter._message_history[ROOM]
        assert history[0]["content"][0]["text"].startswith(
            "[System]: Alice joined\n\n[System]: Bob is now a contact\n\n"
        )
        assert _alternates(history), [message["role"] for message in history]

    @pytest.mark.asyncio
    async def test_narration_failure_does_not_cost_the_room_its_reply(self, scripted):
        """Execution events are best-effort; a flaky event backend must not end the turn."""

        class NoEventTools(FakeAgentTools):
            async def send_event(self, *args, **kwargs):
                raise RuntimeError("events down")

        tools = NoEventTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN, emit=Emit.TOOL_CALLS)

        await _run_message(adapter, tools)

        tools.assert_message_sent(content="hi", count=1)

    @pytest.mark.asyncio
    async def test_usage_emitted_once_per_turn(self, tools, scripted):
        adapter = await scripted(
            SEND_TURN,
            input_tokens=_INPUT_TOKENS_PER_CALL,
            output_tokens=_OUTPUT_TOKENS_PER_CALL,
            emit=Emit.USAGE,
        )

        await _run_message(adapter, tools)

        usage_events = [e for e in tools.events_sent if is_usage_event(e["metadata"])]
        assert len(usage_events) == 1
        # The tool turn and the closing text turn are two model calls, so the
        # event carries the turn total, not the last call's usage.
        assert usage_events[0]["metadata"][USAGE_METADATA_KEY] == {
            "input_tokens": 2 * _INPUT_TOKENS_PER_CALL,
            "output_tokens": 2 * _OUTPUT_TOKENS_PER_CALL,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
