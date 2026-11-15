"""A run that ended with a typed, non-cancellation stop reason leaves the room open."""

from __future__ import annotations

import pytest

from tests.adapters.letta.unconfirmed.kit import (
    MODES,
    StreamingMode,
    adapter_over_recording_server,
    new_tools,
)
from tests.adapters.lettakit import (
    LettaStream,
    make_assistant_message,
    make_stop_reason_message,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

COMPLETING_STOP_REASONS = ["end_turn", "max_steps", "error", "tool_rule"]


class TestOrdinarySuccess:
    @pytest.mark.parametrize("mode", MODES)
    async def test_consecutive_turns_stream_and_reply_in_each_mode(
        self, mode: StreamingMode
    ) -> None:
        adapter, client = adapter_over_recording_server(mode)
        first, second = new_tools(), new_tools()

        await adapter.on_event(turn_input(first))
        await adapter.on_event(turn_input(second))

        assert client.journal == [mode.sent, mode.sent]
        assert [m["content"] for m in first.messages_sent] == ["Hello!"]
        assert [m["content"] for m in second.messages_sent] == ["Hello!"]
        assert not adapter.backend_turns.unavailable(ROOM_ID)
        assert adapter.backend_turns.unconfirmed_handle(ROOM_ID) is None

    @pytest.mark.parametrize("stop_reason", COMPLETING_STOP_REASONS)
    @pytest.mark.parametrize("mode", MODES)
    async def test_any_typed_non_cancellation_stop_permits_the_next_turn(
        self, mode: StreamingMode, stop_reason: str
    ) -> None:
        adapter, client = adapter_over_recording_server(mode)
        client.script_turns(
            LettaStream(
                [make_assistant_message("done"), make_stop_reason_message(stop_reason)]
            )
        )
        first, second = new_tools(), new_tools()

        await adapter.on_event(turn_input(first))
        await adapter.on_event(turn_input(second))

        assert client.cancels == []
        assert [m["content"] for m in first.messages_sent] == ["done"]
        assert [m["content"] for m in second.messages_sent] == ["Hello!"]
