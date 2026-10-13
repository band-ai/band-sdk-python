"""Codex adapter: ``additionalDetails`` is attacker-influenceable and must be capped."""

from __future__ import annotations

from typing import Any

from band.integrations.codex.types import (
    _MAX_ERROR_DETAIL_CHARS,
    build_agent_failure,
)


class TestStructuredErrorDetailCap:
    """``additionalDetails`` is attacker-influenceable and must be capped."""

    def test_long_additional_details_string_is_truncated(self) -> None:

        long_detail = "x" * (_MAX_ERROR_DETAIL_CHARS + 500)
        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": long_detail,
            }
        )
        detail = failure.detail["codex_additional_details"]
        assert isinstance(detail, str)
        assert len(detail) < len(long_detail)
        assert "truncated" in detail

    def test_structured_dict_additional_details_are_preserved(self) -> None:
        """Only string details are capped; dict/list payloads pass through."""

        payload = {"hint": "refresh token", "code": 401}
        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": payload,
            }
        )
        assert failure.detail["codex_additional_details"] == payload

    def test_empty_additional_details_is_dropped(self) -> None:
        """Empty strings are not echoed into detail."""

        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": "",
            }
        )
        assert failure.detail is None

    def test_oversized_dict_additional_details_is_replaced_with_marker(
        self,
    ) -> None:
        """Large non-string payloads must not slip past the byte cap.

        A hostile upstream that embeds a megabyte of nested JSON in
        ``additionalDetails`` would otherwise inflate every downstream
        WebSocket frame.  When the serialized form exceeds the cap we
        replace the whole payload with a truncated marker string.
        """

        # Build a dict whose JSON serialization comfortably exceeds the cap.
        oversized_value = "x" * (_MAX_ERROR_DETAIL_CHARS + 500)
        payload = {"nested": {"blob": oversized_value}}

        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": payload,
            }
        )
        detail = failure.detail["codex_additional_details"]
        assert isinstance(detail, str)
        assert "truncated" in detail
        assert len(detail) < len(oversized_value)

    def test_unserializable_additional_details_is_dropped(self) -> None:
        """Payloads that ``json.dumps`` can't handle without ``default=str``
        round-trip through ``default=str``; pathological unserializable
        objects (e.g. a circular reference) must be dropped rather than
        raising into the event-emission path."""

        circular: dict[str, Any] = {}
        circular["self"] = circular

        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": circular,
            }
        )
        assert failure.detail is None
