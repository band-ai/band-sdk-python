from __future__ import annotations

from band.runtime.execution import (
    _error_label,
)


class TestErrorLabel:
    """Tests for the _error_label helper."""

    def test_returns_str_when_non_empty(self):
        assert (
            _error_label(ValueError("something went wrong")) == "something went wrong"
        )

    def test_falls_back_to_class_name_when_empty(self):
        class EmptyError(Exception):
            def __str__(self):
                return ""

        assert _error_label(EmptyError()) == "EmptyError"

    def test_falls_back_to_class_name_when_whitespace_only(self):
        assert _error_label(Exception("   ")) == "Exception"

    def test_strips_surrounding_whitespace(self):
        assert _error_label(ValueError("  trimmed  ")) == "trimmed"
