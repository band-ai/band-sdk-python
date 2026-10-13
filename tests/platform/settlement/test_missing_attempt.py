"""Only the platform's structured no-processing-attempt 422 is REJECTED."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from band.platform.message_lifecycle import (
    PROCESSING_ATTEMPT_POINTER,
    VALIDATION_ERROR_CODE,
)
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    Reply,
    SettlementPeer,
    Terminal,
    error_body,
)


def unprocessable(code: str, details: dict[str, object]) -> Reply:
    return Reply.of_json(422, error_body(code, details))


class TestMissingAttempt:
    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            pytest.param(MISSING_ATTEMPT, SettlementResult.REJECTED, id="known"),
            pytest.param(Reply(422), SettlementResult.UNCONFIRMED, id="empty-body"),
            pytest.param(
                Reply(422, "{not json"), SettlementResult.UNCONFIRMED, id="bad-json"
            ),
            pytest.param(
                Reply.of_json(422, {"unexpected": 1}),
                SettlementResult.UNCONFIRMED,
                id="malformed-envelope",
            ),
            pytest.param(
                unprocessable(VALIDATION_ERROR_CODE, {}),
                SettlementResult.UNCONFIRMED,
                id="no-details",
            ),
            pytest.param(
                unprocessable(VALIDATION_ERROR_CODE, {"/error": ["is invalid"]}),
                SettlementResult.UNCONFIRMED,
                id="unrelated-detail",
            ),
            pytest.param(
                unprocessable("database_error", {PROCESSING_ATTEMPT_POINTER: ["x"]}),
                SettlementResult.UNCONFIRMED,
                id="other-code",
            ),
            pytest.param(
                unprocessable(VALIDATION_ERROR_CODE, {PROCESSING_ATTEMPT_POINTER: []}),
                SettlementResult.UNCONFIRMED,
                id="empty-detail",
            ),
            pytest.param(
                unprocessable(VALIDATION_ERROR_CODE, {PROCESSING_ATTEMPT_POINTER: "x"}),
                SettlementResult.UNCONFIRMED,
                id="malformed-detail",
            ),
        ],
    )
    async def test_a_422_is_rejected_only_when_it_names_the_missing_attempt(
        self,
        terminal: Terminal,
        peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
        expected: SettlementResult,
    ) -> None:
        peer.reply = reply

        assert await terminal.detailed(link) is expected
