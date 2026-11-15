"""Bootstrap history ends before the turn's earliest original and repeats none."""

from __future__ import annotations

from tests.runtime.batching.kit import (
    FIRST_INPUT_AT,
    LATER_INPUT_AT,
    SECOND_INPUT_AT,
    history_contents,
    stored_message,
)


class TestBatchHistory:
    async def test_history_stops_before_the_earliest_original(self) -> None:
        history = [
            stored_message("h1"),
            stored_message("h2"),
            stored_message("m1", inserted_at=FIRST_INPUT_AT),
            stored_message("m2", inserted_at=SECOND_INPUT_AT),
            stored_message("m3", inserted_at=LATER_INPUT_AT),
        ]

        assert await history_contents(stored_messages=history) == ["h1", "h2"]

    async def test_a_later_original_is_not_repeated_when_the_first_is_not_yet_stored(
        self,
    ) -> None:
        history = [
            stored_message("h1"),
            stored_message("m2", inserted_at=SECOND_INPUT_AT),
        ]
        assert await history_contents(stored_messages=history) == ["h1"]

    async def test_entries_after_a_stored_later_original_are_pending_turns_too(
        self,
    ) -> None:
        history = [
            stored_message("h1"),
            stored_message("m2", inserted_at=SECOND_INPUT_AT),
            stored_message("after", inserted_at=LATER_INPUT_AT),
        ]

        assert await history_contents(stored_messages=history) == ["h1"]
