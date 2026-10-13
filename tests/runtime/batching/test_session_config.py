"""Batching is opt-in, bounded by the platform page size, and single-attempt."""

from __future__ import annotations

import pytest

from band.runtime.types import (
    ACTIONABLE_PAGE_SIZE,
    DEFAULT_MAX_QUEUED_MESSAGES,
    SessionConfig,
)


class TestSessionConfig:
    """``consume_all_queued`` and ``max_queued_messages`` validation."""

    def test_batching_is_off_with_a_ceiling_of_ten_by_default(self) -> None:
        config = SessionConfig()

        assert (config.consume_all_queued, config.max_queued_messages) == (False, 10)

    @pytest.mark.parametrize("ceiling", [1, DEFAULT_MAX_QUEUED_MESSAGES, 100])
    def test_a_ceiling_inside_the_page_size_is_accepted(self, ceiling: int) -> None:
        assert ACTIONABLE_PAGE_SIZE == 100
        config = SessionConfig(consume_all_queued=True, max_queued_messages=ceiling)

        assert config.max_queued_messages == ceiling

    @pytest.mark.parametrize("ceiling", [0, -1, 101, 10.0, "10", True, None])
    def test_a_ceiling_outside_one_to_one_hundred_is_rejected(
        self, ceiling: object
    ) -> None:
        with pytest.raises(ValueError, match="max_queued_messages"):
            SessionConfig(max_queued_messages=ceiling)  # type: ignore[arg-type]

    def test_batching_with_model_retries_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_message_retries"):
            SessionConfig(consume_all_queued=True, max_message_retries=2)

    def test_model_retries_remain_configurable_when_batching_is_off(self) -> None:
        assert SessionConfig(max_message_retries=3).max_message_retries == 3
