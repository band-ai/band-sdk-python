"""Receiver HTTP routes delegate invocations and sanitize errors."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import MagicMock

import httpx
import pytest

from band.runtime.oneshot import OneShotEnvelopeError


class TestRoutes:
    async def test_ping(self, receiver: ModuleType) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver.app), base_url="http://receiver"
        ) as client:
            response = await client.get("/ping")
        assert (response.status_code, response.json()) == (200, {"status": "Healthy"})

    async def test_invocation(self, receiver: ModuleType, invoker: MagicMock) -> None:
        body = {"event_type": "message_created", "room_id": "r1", "payload": {}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver.app), base_url="http://receiver"
        ) as client:
            response = await client.post("/invocations", json=body)
        assert (response.status_code, response.json()) == (200, {"status": "done"})
        invoker.handle_event.assert_awaited_once_with(body)

    @pytest.mark.parametrize(
        "error,status,detail",
        [
            (OneShotEnvelopeError("missing room_id"), 400, "missing room_id"),
            (RuntimeError("private failure"), 500, "invocation failed"),
        ],
    )
    async def test_error_mapping(
        self,
        receiver: ModuleType,
        invoker: MagicMock,
        error: Exception,
        status: int,
        detail: str,
    ) -> None:
        invoker.handle_event.side_effect = error
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver.app), base_url="http://receiver"
        ) as client:
            response = await client.post("/invocations", json={})
        assert (response.status_code, response.json()) == (status, {"detail": detail})
