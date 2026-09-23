"""Tests for the shared at-least-one-of cross-field validation rule."""

from __future__ import annotations

import pytest

from band.core.validation import at_least_one_of


def test_raises_when_every_field_is_none() -> None:
    with pytest.raises(ValueError, match="At least one of a, b, or c must be set"):
        at_least_one_of(a=None, b=None, c=None)


def test_passes_when_one_field_is_set() -> None:
    at_least_one_of(a=None, b="value", c=None)


def test_two_field_message_has_no_oxford_comma() -> None:
    with pytest.raises(ValueError, match="At least one of goal_title or goal_summary"):
        at_least_one_of(goal_title=None, goal_summary=None)


def test_explicit_empty_string_counts_as_set() -> None:
    """An explicit "" or 0 is a real value, not an omission."""
    at_least_one_of(a=None, b="")
    at_least_one_of(a=None, b=0)


def test_raises_type_error_when_called_with_no_fields() -> None:
    """A caller passing zero fields is a programming error, not user input --
    there is nothing to name in an "at least one of ..." message."""
    with pytest.raises(TypeError, match="at least one keyword argument"):
        at_least_one_of()
