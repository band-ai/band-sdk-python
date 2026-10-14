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

    @pytest.mark.parametrize(
        "ceiling", [1, DEFAULT_MAX_QUEUED_MESSAGES, ACTIONABLE_PAGE_SIZE]
    )
    def test_a_ceiling_inside_the_page_size_is_accepted(self, ceiling: int) -> None:
        config = SessionConfig(consume_all_queued=True, max_queued_messages=ceiling)

        assert config.max_queued_messages == ceiling

    @pytest.mark.parametrize(
        "ceiling", [0, -1, ACTIONABLE_PAGE_SIZE + 1, 10.0, "10", True, None]
    )
    def test_a_ceiling_outside_the_page_size_is_rejected(self, ceiling: object) -> None:
        with pytest.raises(ValueError, match="max_queued_messages"):
            SessionConfig(max_queued_messages=ceiling)  # type: ignore[arg-type]

    def test_batching_with_model_retries_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_message_retries"):
            SessionConfig(consume_all_queued=True, max_message_retries=2)
