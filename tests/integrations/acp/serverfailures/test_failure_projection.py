from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from tests.integrations.acp.serverfailures.kit import prompt, running_server
from tests.integrations.acp.serverkit import (
    deliver_server_message,
    failure_event,
    wait_for_pending_prompt,
)


class TestFailureProjection:
    @pytest.mark.asyncio
    async def test_error_rejects_with_redacted_core_projection(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        msg = failure_event(
            {
                "failure": {
                    "provider": "peer",
                    "message": "Authorization: ApiKey sk-private",
                    "code": "UPSTREAM",
                    "detail": {"nested": ["Bearer secret-token"]},
                }
            },
            content="token=room-secret",
        )

        await deliver_server_message(adapter, msg)

        expected = AgentFailure(
            "peer",
            "Authorization=[REDACTED]",
            code="UPSTREAM",
            detail={"nested": ["Bearer [REDACTED]"]},
        ).to_extension_data()
        with pytest.raises(RequestError) as raised:
            await task
        assert raised.value.code == -32603
        assert raised.value.data == expected
        update = adapter.get_acp_client().session_update.await_args.kwargs["update"]
        assert update.field_meta == expected
        assert update.content.text == "[Error] token=[REDACTED]"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "metadata",
        [
            {},
            [],
            {"failure": "broken"},
            {"failure": {"provider": " ", "message": "x"}},
            {"failure": {"provider": "peer", "message": "x", "code": 42}},
            {
                "failure": {
                    "provider": "peer",
                    "message": "x",
                    "detail": {"bad": object()},
                }
            },
        ],
    )
    async def test_malformed_failure_uses_safe_fallback(
        self, mock_rest_client: MagicMock, metadata: object
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(adapter, failure_event(metadata, "Bearer secret"))
        with pytest.raises(RequestError) as raised:
            await task
        assert (
            raised.value.data
            == AgentFailure("band", "Bearer [REDACTED]").to_extension_data()
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["", "   "])
    async def test_blank_failure_message_keeps_core_failure(
        self, mock_rest_client: MagicMock, message: str
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(
            adapter,
            failure_event(
                {
                    "failure": {
                        "provider": "codex",
                        "message": message,
                        "code": "timeout",
                        "detail": {"retry": 1},
                    }
                },
                content="codex failed without an error message.",
            ),
        )
        with pytest.raises(RequestError) as raised:
            await task
        assert (
            raised.value.data
            == AgentFailure(
                "codex",
                "codex failed without an error message.",
                code="timeout",
                detail={"retry": 1},
            ).to_extension_data()
        )

    @pytest.mark.asyncio
    async def test_blank_error_uses_generic_fallback(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(adapter, failure_event(content=" "))
        with pytest.raises(RequestError) as raised:
            await task
        assert (
            raised.value.data
            == AgentFailure("band", "Band peer reported a failure.").to_extension_data()
        )
