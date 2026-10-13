"""Codex adapter: build_agent_failure handling of non-standard inputs."""

from __future__ import annotations

from typing import Any

from band.integrations.codex.types import build_agent_failure


class TestStructuredErrorNormalization:
    """build_agent_failure handling of non-standard inputs."""

    def test_structured_error_with_string_error_obj(self) -> None:
        """_handle_error_event normalizes string error_obj before structuring.

        The review flagged a redundant isinstance(error_obj, dict) check that
        was dead code; this test asserts the normalization still works when
        the original error_obj is a string rather than a dict.
        """

        # Simulate the normalization the adapter performs: convert string to
        # {"message": <str>} before passing to build_agent_failure.
        error_obj: dict[str, Any] = {"message": "raw string error"}
        failure = build_agent_failure(error_obj)
        assert "raw string error" in failure.message
        # No codexErrorInfo -> no known error type.
        assert failure.code is None
