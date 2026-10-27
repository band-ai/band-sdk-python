"""Receiver HTTP routes delegate invocations and sanitize errors."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import NonCallableMagicMock

import httpx

from band.runtime.oneshot import OneShotEnvelopeError


class TestRoutes:
    async def test_ping_reports_healthy(
        self, receiver: ModuleType, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/ping")
        assert (response.status_code, response.json()) == (
            200,
            {"status": receiver.HEALTHY},
        )

    async def test_invocation_body_reaches_the_invoker_unchanged(
        self, client: httpx.AsyncClient, invoker: NonCallableMagicMock
    ) -> None:
        body = {"event_type": "message_created", "room_id": "r1", "payload": {}}
        response = await client.post("/invocations", json=body)
        assert (response.status_code, response.json()) == (200, {"status": "done"})
        invoker.handle_event.assert_awaited_once_with(body)

    async def test_malformed_json_is_rejected_before_invocation(
        self,
        receiver: ModuleType,
        client: httpx.AsyncClient,
        invoker: NonCallableMagicMock,
    ) -> None:
        response = await client.post(
            "/invocations",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        assert (response.status_code, response.json()) == (
            400,
            {"detail": receiver.INVALID_JSON},
        )
        invoker.handle_event.assert_not_awaited()

    async def test_envelope_error_maps_to_400_with_its_reason(
        self, client: httpx.AsyncClient, invoker: NonCallableMagicMock
    ) -> None:
        invoker.handle_event.side_effect = OneShotEnvelopeError("missing room_id")
        response = await client.post("/invocations", json={})
        assert (response.status_code, response.json()) == (
            400,
            {"detail": "missing room_id"},
        )

    async def test_unexpected_error_maps_to_a_sanitized_500(
        self,
        receiver: ModuleType,
        client: httpx.AsyncClient,
        invoker: NonCallableMagicMock,
    ) -> None:
        invoker.handle_event.side_effect = RuntimeError("private failure")
        response = await client.post("/invocations", json={})
        assert (response.status_code, response.json()) == (
            500,
            {"detail": receiver.INVOCATION_FAILED},
        )
