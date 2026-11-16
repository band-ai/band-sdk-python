"""A run whose end Letta did not type as a completion is never proven stopped."""

from __future__ import annotations

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.core.turn_lifecycle import PROVIDER_UNAVAILABLE_MESSAGE
from band.testing import failure_reports
from tests.adapters.letta.unconfirmed.kit import (
    MODES,
    StreamingMode,
    adapter_over_recording_server,
    new_tools,
    refused_as_unavailable,
)
from tests.adapters.lettakit import (
    LettaStream,
    make_assistant_message,
    make_stop_reason_message,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

LATE_TEXT = "text after the provider's cancellation"

# How a run's stream ends without a typed, non-cancellation completion.
UNCERTAIN_ENDINGS = [
    pytest.param(
        lambda: LettaStream(
            [
                make_assistant_message("partial"),
                make_stop_reason_message("cancelled"),
                # Frames behind the cancellation: late, and never trusted.
                make_assistant_message(LATE_TEXT),
                make_stop_reason_message("end_turn"),
            ]
        ),
        id="cancelled-status",
    ),
    pytest.param(
        lambda: LettaStream([make_assistant_message("partial")]),
        id="eof-without-a-stop-reason",
    ),
    pytest.param(
        lambda: LettaStream(
            [make_assistant_message("partial")],
            fails_with=ConnectionResetError("peer closed the stream"),
        ),
        id="transport-failure",
    ),
]


class TestUncertainRun:
    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("ending", UNCERTAIN_ENDINGS)
    async def test_the_room_stays_unavailable_and_nothing_is_replayed(
        self, mode: StreamingMode, ending
    ) -> None:
        adapter, client = adapter_over_recording_server(mode)
        client.script_turns(ending())
        uncertain = new_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(uncertain))
        await adapter.backend_turns.drain()

        # Only the failure is reported: no partial or late text is relayed.
        assert uncertain.messages_sent == []
        assert failure_reports(uncertain) == [("letta", PROVIDER_UNAVAILABLE_MESSAGE)]
        # The cancel was asked for once, and its answer reopened nothing.
        assert client.journal == [mode.sent, mode.cancel]
        assert adapter.backend_turns.unavailable(ROOM_ID)

        later = await refused_as_unavailable(adapter)
        again = await refused_as_unavailable(adapter)

        assert later.messages_sent == again.messages_sent == []
        assert client.sent == [mode.sent]
