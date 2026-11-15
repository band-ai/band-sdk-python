"""Bootstrap replay excludes pending requests after an input REST omits."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from band.core.types import AgentInput
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.roomhistory import stored

START = datetime(2024, 1, 1, tzinfo=UTC)
CURRENT = START + timedelta(seconds=1)
LATER = START + timedelta(seconds=2)
FIRST_ID = "00000000-0000-4000-8000-000000000001"
CURRENT_ID = "00000000-0000-4000-8000-000000000002"
LATER_ID = "00000000-0000-4000-8000-000000000003"
THREAD_ID = "00000000-0000-4000-8000-000000000004"


def contents(inp: AgentInput | None) -> list[str]:
    """Read the bootstrap history the preprocessor hands to the adapter."""
    assert inp is not None
    return [message["content"] for message in inp.history.raw]


class TestBootstrapHistoryCutoff:
    @pytest.mark.parametrize(
        ("before_time", "after_time"),
        [(START, LATER), (CURRENT, CURRENT)],
        ids=["later-time", "id-tiebreak"],
    )
    async def test_a_delivered_thread_reply_keeps_only_earlier_room_context(
        self,
        history_context: Callable[..., ExecutionContext],
        before_time: datetime,
        after_time: datetime,
    ) -> None:
        ctx = history_context(
            [
                stored("earlier", message_id=FIRST_ID, inserted_at=before_time),
                stored(
                    "LATER PENDING REQUEST", message_id=LATER_ID, inserted_at=after_time
                ),
            ]
        )
        event = make_message_event(
            room_id="room-1",
            msg_id=CURRENT_ID,
            inserted_at=CURRENT.isoformat(),
            thread_id=THREAD_ID,
        )

        inp = await DefaultPreprocessor().process(ctx, event, "agent-1")

        assert contents(inp) == ["earlier"]

    async def test_a_missing_earliest_batch_input_precedes_a_later_stored_input(
        self, history_context: Callable[..., ExecutionContext]
    ) -> None:
        ctx = history_context(
            [
                stored("earlier", message_id=FIRST_ID, inserted_at=START),
                stored("pending between inputs", inserted_at=LATER),
                stored(
                    "last input",
                    message_id=LATER_ID,
                    inserted_at=LATER + timedelta(seconds=1),
                ),
            ]
        )
        first = make_message_event(
            room_id="room-1",
            msg_id=CURRENT_ID,
            inserted_at=CURRENT.isoformat(),
            thread_id=THREAD_ID,
        )
        last = make_message_event(
            room_id="room-1",
            msg_id=LATER_ID,
            inserted_at=(LATER + timedelta(seconds=1)).isoformat(),
        )

        inp = await DefaultPreprocessor().process_batch(ctx, (first, last), "agent-1")

        assert contents(inp) == ["earlier"]

    async def test_unavailable_ordering_uses_existing_empty_history_degradation(
        self,
        history_context: Callable[..., ExecutionContext],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctx = history_context([stored("unknown insertion time", inserted_at=None)])
        event = make_message_event(
            room_id="room-1", msg_id=CURRENT_ID, inserted_at=CURRENT.isoformat()
        )

        inp = await DefaultPreprocessor().process(ctx, event, "agent-1")

        assert contents(inp) == []
        assert "Cannot order room history" in caplog.text

    async def test_history_opt_out_still_keeps_replay_empty(
        self, history_context: Callable[..., ExecutionContext]
    ) -> None:
        ctx = history_context([stored("earlier", inserted_at=START)], hydrate=False)
        event = make_message_event(
            room_id="room-1", msg_id=CURRENT_ID, inserted_at=CURRENT.isoformat()
        )

        inp = await DefaultPreprocessor().process(ctx, event, "agent-1")

        assert contents(inp) == []
