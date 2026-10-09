"""Tests for the Parlant session -> room tools registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.integrations.parlant.sessiontools import (
    bound_session_tools,
    get_session_tools,
)
from band.integrations.parlant.tools import (
    get_current_tools,
    set_current_tools,
    set_session_tools,
)


class TestSessionToolsRegistry:
    """Tests for session-keyed tools registry."""

    def test_set_then_clear_round_trip(self):
        tools = MagicMock()

        set_session_tools("session-123", tools)
        assert get_session_tools("session-123") is tools

        set_session_tools("session-123", None)
        assert get_session_tools("session-123") is None

    def test_get_session_tools_returns_none_for_unknown_session(self):
        """Should return None for unknown session."""
        result = get_session_tools("unknown-session")

        assert result is None


class TestDeprecatedFunctions:
    """Tests for deprecated compatibility functions."""

    def test_set_current_tools_emits_deprecation_warning(self):
        """Should emit deprecation warning."""
        with pytest.warns(DeprecationWarning, match="set_current_tools is deprecated"):
            set_current_tools(MagicMock())

    def test_get_current_tools_emits_deprecation_warning(self):
        """Should emit deprecation warning."""
        with pytest.warns(DeprecationWarning, match="get_current_tools is deprecated"):
            get_current_tools()

    def test_get_current_tools_returns_none(self):
        """Should return None (tools now accessed via session_id)."""
        with pytest.warns(DeprecationWarning):
            result = get_current_tools()

        assert result is None


class TestBoundSessionTools:
    """The adapter binds a room's tools to its session for exactly one turn."""

    def test_binds_for_the_turn_then_unbinds(self):
        tools = MagicMock()

        with bound_session_tools(session_id="session-123", tools=tools):
            assert get_session_tools("session-123") is tools

        assert get_session_tools("session-123") is None

    def test_unbinds_when_the_turn_fails(self):
        with (
            pytest.raises(RuntimeError),
            bound_session_tools(session_id="session-123", tools=MagicMock()),
        ):
            raise RuntimeError("turn failed")

        assert get_session_tools("session-123") is None
