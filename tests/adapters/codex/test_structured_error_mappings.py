"""Codex adapter: build_agent_failure passes codexErrorInfo through verbatim -- no remediation/suggested-action policy; that belongs to a consumer, not this shared shape."""

from __future__ import annotations

import pytest

from band.integrations.codex.types import build_agent_failure


class TestStructuredErrorMappings:
    """build_agent_failure passes codexErrorInfo through verbatim -- no
    remediation/suggested-action policy; that belongs to a consumer, not
    this shared shape."""

    @pytest.mark.parametrize(
        "error_type",
        [
            "HttpConnectionFailed",
            "SandboxError",
            "Unauthorized",
            "BadRequest",
            "ResponseTooManyFailedAttempts",
        ],
    )
    def test_error_type_becomes_the_failure_code(self, error_type: str) -> None:
        failure = build_agent_failure(
            {"codexErrorInfo": {"type": error_type, "retryable": True}}
        )
        assert failure.code == error_type
        assert failure.detail["codex_is_retryable"] is True

    def test_non_dict_codex_error_info_is_tolerated(self) -> None:

        failure = build_agent_failure(
            {"message": "boom", "codexErrorInfo": "not-a-dict"}
        )
        assert failure.code is None
        assert failure.message == "boom"

    def test_missing_codex_error_info_falls_back_to_message(self) -> None:

        failure = build_agent_failure({"message": "network down"})
        assert failure.code is None
        assert failure.message == "network down"

    def test_additional_details_preserved_in_detail(self) -> None:

        failure = build_agent_failure(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": {"hint": "refresh token"},
            }
        )
        assert failure.detail["codex_additional_details"] == {"hint": "refresh token"}
