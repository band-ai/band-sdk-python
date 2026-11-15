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
                {"api_key": f"{SECRET}\r\nx-injected: 1"},
                CREDENTIAL_REQUIRED,
                id="api-key-with-line-break",
            ),
            pytest.param(
                {"access_token": f"{SECRET}\n"},
                CREDENTIAL_REQUIRED,
                id="access-token-with-line-break",
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

    @pytest.mark.parametrize(
        ("credentials", "api_key"),
        [
            pytest.param({"api_key": SECRET}, SECRET, id="api-key"),
            pytest.param({"access_token": SECRET}, None, id="access-token"),
        ],
    )
    def test_the_api_key_is_reported_but_cannot_be_replaced(
        self, credentials: dict[str, Any], api_key: str | None
    ) -> None:
        client = WebSocketClient(UNREACHABLE_URL, **credentials)

        with pytest.raises(AttributeError):
            client.api_key = "synthetic-replacement-credential"  # pyrefly: ignore[read-only]

        assert client.api_key == api_key
