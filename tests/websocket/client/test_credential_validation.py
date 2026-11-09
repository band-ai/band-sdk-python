"""A WebSocketClient takes exactly one usable credential and never echoes it."""

from __future__ import annotations

from typing import Any

import pytest

from band.client.streaming import WebSocketClient
from band.client.streaming.client import (
    CREDENTIAL_REQUIRED,
    TOKEN_WITH_AGENT_SETTINGS,
)
from band.core.types import ConflictPolicy
from tests.identifiers import AGENT_ID

SECRET = "synthetic-validation-credential"
UNREACHABLE_URL = "ws://peer.invalid"


class TestCredentialValidation:
    @pytest.mark.parametrize(
        ("credentials", "message"),
        [
            pytest.param({}, CREDENTIAL_REQUIRED, id="missing"),
            pytest.param(
                {"api_key": SECRET, "access_token": SECRET},
                CREDENTIAL_REQUIRED,
                id="ambiguous",
            ),
            pytest.param({"api_key": ""}, CREDENTIAL_REQUIRED, id="empty-api-key"),
            pytest.param(
                {"access_token": " \t"}, CREDENTIAL_REQUIRED, id="blank-access-token"
            ),
            pytest.param(
                {"access_token": SECRET, "agent_id": AGENT_ID},
                TOKEN_WITH_AGENT_SETTINGS,
                id="access-token-with-agent-id",
            ),
            pytest.param(
                {"access_token": SECRET, "conflict_policy": ConflictPolicy.REJECT},
                TOKEN_WITH_AGENT_SETTINGS,
                id="access-token-with-conflict-policy",
            ),
        ],
    )
    def test_construction_rejects_an_unusable_credential(
        self, credentials: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError) as rejected:
            WebSocketClient(UNREACHABLE_URL, **credentials)

        assert str(rejected.value) == message

    def test_the_credential_cannot_be_replaced_in_place(self) -> None:
        client = WebSocketClient(UNREACHABLE_URL, SECRET)

        with pytest.raises(AttributeError):
            client.api_key = "synthetic-replacement-credential"  # pyrefly: ignore[read-only]

        assert client.api_key == SECRET
