"""band-trigger's error text carries the platform message or falls back to the status."""

from __future__ import annotations

from types import SimpleNamespace

from band_rest.core.api_error import ApiError

from band.cli.trigger import _format_api_error


class TestFormatApiError:
    def test_extracts_message_from_body(self):
        body = SimpleNamespace(
            error=SimpleNamespace(message="Chat room limit reached.")
        )
        err = ApiError(status_code=403, body=body)
        result = _format_api_error(err, "create chatroom")
        assert result == "Failed to create chatroom: Chat room limit reached."

    def test_falls_back_to_status_code(self):
        err = ApiError(status_code=500, body=None)
        result = _format_api_error(err, "send message")
        assert result == "Failed to send message: HTTP 500"

    def test_falls_back_when_no_error_attr(self):
        body = SimpleNamespace()
        err = ApiError(status_code=422, body=body)
        result = _format_api_error(err, "add participant")
        assert result == "Failed to add participant: HTTP 422"
