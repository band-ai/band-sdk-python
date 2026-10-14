"""Bootstrap history ends before the turn's earliest original and repeats none."""

from __future__ import annotations

from tests.runtime.batching.kit import history_contents


class TestBatchHistory:
    async def test_history_stops_before_the_earliest_original(self) -> None:
        stored_ids = ["h1", "h2", "m1", "m2", "m3"]

        assert await history_contents(stored_ids=stored_ids) == ["h1", "h2"]

    async def test_a_later_original_is_not_repeated_when_the_first_is_not_yet_stored(
        self,
    ) -> None:
        assert await history_contents(stored_ids=["h1", "m2"]) == ["h1"]
