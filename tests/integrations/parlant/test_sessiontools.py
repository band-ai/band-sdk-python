"""Tests for the Parlant session -> room tools registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.integrations.parlant.sessiontools import (
    _session_tools,
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

    def test_set_session_tools_stores_tools(self):
        """Should store tools for a session."""
        mock_tools = MagicMock()

        set_session_tools("session-123", mock_tools)

        assert "session-123" in _session_tools
        assert _session_tools["session-123"] is mock_tools

    def test_set_session_tools_clears_on_none(self):
        """Should clear tools when setting None."""
        mock_tools = MagicMock()
        set_session_tools("session-123", mock_tools)
        assert "session-123" in _session_tools

        set_session_tools("session-123", None)

        assert "session-123" not in _session_tools

    def test_get_session_tools_returns_stored_tools(self):
        """Should return stored tools for session."""
        mock_tools = MagicMock()
        _session_tools["session-123"] = mock_tools

        result = get_session_tools("session-123")

        assert result is mock_tools

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
