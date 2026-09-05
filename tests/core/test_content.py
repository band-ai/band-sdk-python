"""Tests for the platform's visible-content predicate."""

from __future__ import annotations

import pytest

from band.core.content import has_visible_content

ZERO_WIDTH_SPACE = "\u200b"
LEFT_TO_RIGHT_MARK = "\u200e"


# Table mirrors the platform's own validate_visible_content/1 cases: every
# letter/number/punctuation/symbol is visible, everything else (whitespace,
# zero-width, bidi marks) is not.
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", False),
        ("   ", False),
        ("\n\t ", False),
        (ZERO_WIDTH_SPACE, False),
        (LEFT_TO_RIGHT_MARK, False),
        ("0", True),
        ("hello", True),
        ("\U0001f600", True),
        ("  hi  ", True),
    ],
)
def test_has_visible_content(content: str, expected: bool) -> None:
    assert has_visible_content(content) is expected
