"""Receiver startup uses the real invoker before accepting requests."""

from __future__ import annotations

from types import ModuleType

import httpx
import pytest
from pytest_httpx import HTTPXMock

from tests.runtime.conftest import make_link_mock


class TestLifespan:
    async def test_real_invoker_serves_only_after_metadata_loaded(
        self,
        receiver: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: HTTPXMock,
    ) -> None:
        monkeypatch.setenv("BAND_AGENT_ID", "agent-1")
        monkeypatch.setenv("BAND_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-provider")
        monkeypatch.setenv("BAND_REST_URL", "https://receiver-platform.test")
        response = await make_link_mock().rest.agent_api_identity.get_agent_me()
        httpx_mock.add_response(
            url="https://receiver-platform.test/api/v1/agent/me",
            json=response.model_dump(mode="json"),
        )
        async with receiver.app.router.lifespan_context(receiver.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=receiver.app),
                base_url="http://receiver",
            ) as client:
                result = await client.post(
                    "/invocations",
                    json={
                        "event_type": "room_added",
                        "agent_id": "agent-1",
                        "room_id": "room-1",
                        "payload": {},
                    },
                )
            assert (result.status_code, result.json()) == (
                200,
                {"status": "ignored", "event_type": "room_added"},
            )
            assert receiver.app.state.invoker.agent_name == response.data.name
        with pytest.raises(RuntimeError, match="startup"):
            await receiver.app.state.invoker.handle_event({"event_type": "room_added"})
