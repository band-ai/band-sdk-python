"""WebSocket upgrade refusals parse into WebSocketUpgradeError."""

from __future__ import annotations

import pytest

from band.client.streaming import WebSocketUpgradeError
from band.client.streaming.errors import CONNECTION_CONFLICT_CODE
from tests.websocket.client.kit import (
    connection_conflict_error_body,
    upgrade_error_body,
    upgrade_exception,
)


class TestUpgradeErrors:
    @pytest.mark.parametrize(
        ("status", "body", "code"),
        [
            (409, connection_conflict_error_body(), CONNECTION_CONFLICT_CODE),
            (
                400,
                upgrade_error_body("invalid_on_conflict", status=400),
                "invalid_on_conflict",
            ),
            (401, upgrade_error_body("unauthorized", status=401), "unauthorized"),
            (403, upgrade_error_body("forbidden", status=403), "forbidden"),
            (503, upgrade_error_body("tracking_failed", status=503), "tracking_failed"),
        ],
    )
    def test_parses_the_platform_error_of_any_status(
        self, status: int, body: bytes, code: str
    ) -> None:
        err = WebSocketUpgradeError.from_exception(upgrade_exception(status, body))

        assert err is not None
        assert (err.status_code, err.code, err.request_id, err.retry_after) == (
            status,
            code,
            f"req-{status}",
            None,
        )

    def test_parses_a_status_without_a_json_body(self) -> None:
        err = WebSocketUpgradeError.from_exception(upgrade_exception(403, b""))

        assert err is not None
        assert (err.status_code, err.code, err.message) == (
            403,
            None,
            "WebSocket upgrade failed with HTTP 403",
        )

    @pytest.mark.parametrize(
        ("body_retry_after", "header", "expected"),
        [
            (12, "30", 12),
            (None, "30", 30),
            (None, "0", 0),
            (None, "-5", None),
            (-5, None, None),
            (-5, "30", 30),
            (True, "30", 30),
            (None, "Wed, 21 Oct 2026 07:28:00 GMT", None),
            (None, "soon", None),
            (True, None, None),
        ],
    )
    @pytest.mark.parametrize("status", [429, 503])
    def test_reads_the_retry_hint_and_drops_an_invalid_one(
        self,
        status: int,
        body_retry_after: object,
        header: str | None,
        expected: int | None,
    ) -> None:
        headers = {"Retry-After": header} if header is not None else None
        body = upgrade_error_body("busy", status=status, retry_after=body_retry_after)

        err = WebSocketUpgradeError.from_exception(
            upgrade_exception(status, body, headers)
        )

        assert err is not None
        assert err.retry_after == expected

    def test_ignores_an_error_without_an_http_response(self) -> None:
        assert WebSocketUpgradeError.from_exception(OSError("refused")) is None
