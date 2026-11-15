"""A typed completion is not overturned by a trailing transport error."""

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


class TestTrailingStreamError:
    @pytest.mark.parametrize("mode", MODES)
    async def test_trailing_transport_error_after_completion_keeps_the_room_open(
        self, mode: StreamingMode
    ) -> None:
        adapter, client = adapter_over_recording_server(mode)
        client.script_turns(
            LettaStream(
                [
                    make_assistant_message("done"),
                    make_stop_reason_message("end_turn"),
                ],
                fails_with=ConnectionResetError("peer closed after completion"),
            )
        )
        first, second = new_tools(), new_tools()

        await adapter.on_event(turn_input(first))
        await adapter.on_event(turn_input(second))

        assert [m["content"] for m in first.messages_sent] == ["done"]
        assert [m["content"] for m in second.messages_sent] == ["Hello!"]
        assert not adapter.backend_turns.unavailable(ROOM_ID)
        assert client.cancels == []
