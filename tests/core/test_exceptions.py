"""Tests for Band exception hierarchy."""

from __future__ import annotations

import pytest

from thenvoi.core.exceptions import (
    BandConfigError,
    BandConnectionError,
    BandError,
    BandToolError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_thenvoi_error(self) -> None:
        assert issubclass(BandConfigError, BandError)
        assert issubclass(BandConnectionError, BandError)
        assert issubclass(BandToolError, BandError)

    def test_thenvoi_error_inherits_from_exception(self) -> None:
        assert issubclass(BandError, Exception)

    def test_can_catch_with_base_class(self) -> None:
        with pytest.raises(BandError):
            raise BandConfigError("bad config")

    def test_message_preserved(self) -> None:
        err = BandToolError("send_message failed: 403")
        assert str(err) == "send_message failed: 403"

    def test_config_error_not_tool_error(self) -> None:
        assert not issubclass(BandConfigError, BandToolError)
        assert not issubclass(BandToolError, BandConfigError)


class TestConfigErrorWithSuggestion:
    """Tests for BandConfigError.with_suggestion()."""

    def test_suggests_close_match(self) -> None:
        err = BandConfigError.with_suggestion(
            "Unknown capability 'memry'.",
            "memry",
            ["memory", "contacts"],
        )
        assert "Did you mean 'memory'?" in str(err)

    def test_suggests_case_insensitive(self) -> None:
        err = BandConfigError.with_suggestion(
            "Unknown emit value 'EXEUCTION'.",
            "EXEUCTION",
            ["execution", "thoughts", "task_events"],
        )
        assert "Did you mean 'execution'?" in str(err)

    def test_no_suggestion_when_too_far(self) -> None:
        err = BandConfigError.with_suggestion(
            "Unknown capability 'completely_different'.",
            "completely_different",
            ["memory", "contacts"],
        )
        assert "Did you mean" not in str(err)
        assert "Unknown capability 'completely_different'." in str(err)

    def test_picks_closest_among_candidates(self) -> None:
        err = BandConfigError.with_suggestion(
            "Unknown param 'enabel_memory'.",
            "enabel_memory",
            ["enable_memory", "enable_contacts", "memory"],
        )
        assert "Did you mean 'enable_memory'?" in str(err)

    def test_max_distance_respected(self) -> None:
        # 'memo' -> 'memory' is distance 2
        err_default = BandConfigError.with_suggestion(
            "Bad name 'memo'.",
            "memo",
            ["memory"],
        )
        assert "Did you mean 'memory'?" in str(err_default)

        # With max_distance=1, 'memo' -> 'memory' is too far
        err_strict = BandConfigError.with_suggestion(
            "Bad name 'memo'.",
            "memo",
            ["memory"],
            max_distance=1,
        )
        assert "Did you mean" not in str(err_strict)

    def test_returns_thenvoi_config_error(self) -> None:
        err = BandConfigError.with_suggestion("msg", "x", ["y"], max_distance=5)
        assert isinstance(err, BandConfigError)
        assert isinstance(err, BandError)

    def test_empty_haystack_no_suggestion(self) -> None:
        err = BandConfigError.with_suggestion("Bad name.", "anything", [])
        assert "Did you mean" not in str(err)
