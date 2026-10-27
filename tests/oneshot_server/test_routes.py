"""Receiver HTTP routes delegate invocations and sanitize errors."""

from __future__ import annotations

from unittest.mock import NonCallableMagicMock

import httpx
import pytest

from band.runtime.oneshot import OneShotEnvelopeError


class TestRoutes:
    async def test_ping(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/ping")
        assert (response.status_code, response.json()) == (200, {"status": "Healthy"})

    async def test_invocation(
        self, client: httpx.AsyncClient, invoker: NonCallableMagicMock
    ) -> None:
        body = {"event_type": "message_created", "room_id": "r1", "payload": {}}
        response = await client.post("/invocations", json=body)
        assert (response.status_code, response.json()) == (200, {"status": "done"})
        invoker.handle_event.assert_awaited_once_with(body)

    async def test_malformed_json_is_rejected_before_invocation(
        self, client: httpx.AsyncClient, invoker: NonCallableMagicMock
    ) -> None:
        response = await client.post(
            "/invocations",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        assert (response.status_code, response.json()) == (
            400,
            {"detail": "invalid JSON body"},
        )
        invoker.handle_event.assert_not_awaited()

    @pytest.mark.parametrize(
        "error,status,detail",
        [
            (OneShotEnvelopeError("missing room_id"), 400, "missing room_id"),
            (RuntimeError("private failure"), 500, "invocation failed"),
        ],
    )
    async def test_error_mapping(
        self,
        client: httpx.AsyncClient,
        invoker: NonCallableMagicMock,
        error: Exception,
        status: int,
        detail: str,
    ) -> None:
        invoker.handle_event.side_effect = error
        response = await client.post("/invocations", json={})
        assert (response.status_code, response.json()) == (status, {"detail": detail})
