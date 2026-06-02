"""
Tests for base integration utilities.

Tests cover:
- check_and_format_participants() helper function
"""

from unittest.mock import MagicMock


class TestCheckAndFormatParticipants:
    """Test check_and_format_participants() helper."""

    def test_returns_none_when_no_change(self):
        """Should return None when participants haven't changed."""
        from band.integrations.base import check_and_format_participants

        ctx = MagicMock()
        ctx.participants_changed.return_value = False

        result = check_and_format_participants(ctx)

        assert result is None
        ctx.mark_participants_sent.assert_not_called()

    def test_returns_message_when_changed(self):
        """Should return formatted message when participants changed."""
        from band.integrations.base import check_and_format_participants

        ctx = MagicMock()
        ctx.participants_changed.return_value = True
        ctx.participants = [
            {"id": "user-1", "name": "Alice", "type": "User"},
        ]

        result = check_and_format_participants(ctx)

        # Should contain participant info and usage hint
        assert "## Current Participants" in result
        assert "Alice" in result
        assert "band_send_message" in result
        ctx.mark_participants_sent.assert_called_once()

    def test_marks_participants_sent_automatically(self):
        """Should automatically call mark_participants_sent() when returning message."""
        from band.integrations.base import check_and_format_participants

        ctx = MagicMock()
        ctx.participants_changed.return_value = True
        ctx.participants = [{"id": "user-1", "name": "Alice", "type": "User"}]

        check_and_format_participants(ctx)

        ctx.mark_participants_sent.assert_called_once()


class TestIntegrationsImport:
    """Test that integrations module exports base utilities."""

    def test_can_import_from_integrations(self):
        """Should be able to import check_and_format_participants from integrations."""
        from band.integrations import check_and_format_participants

        assert check_and_format_participants is not None

    def test_check_and_format_participants_in_all(self):
        """Should be listed in __all__."""
        from band import integrations

        assert "check_and_format_participants" in integrations.__all__
