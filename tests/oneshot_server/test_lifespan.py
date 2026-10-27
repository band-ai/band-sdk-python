"""Receiver startup loads the real invoker before it serves requests."""

from __future__ import annotations

from types import ModuleType

import httpx
import pytest
from band_rest.core.api_error import ApiError
from pytest_httpx import HTTPXMock

from tests.runtime.conftest import make_link_mock

AGENT_ME_PATH = "/api/v1/agent/me"


class TestLifespan:
    async def test_real_invoker_serves_after_startup_and_refuses_after_shutdown(
        self,
        receiver: ModuleType,
        platform_url: str,
        httpx_mock: HTTPXMock,
    ) -> None:
        response = await make_link_mock().rest.agent_api_identity.get_agent_me()
        httpx_mock.add_response(
            url=platform_url + AGENT_ME_PATH,
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

    async def test_failed_startup_surfaces_the_platform_error_and_never_serves(
        self,
        receiver: ModuleType,
        platform_url: str,
        httpx_mock: HTTPXMock,
    ) -> None:
        httpx_mock.add_response(url=platform_url + AGENT_ME_PATH, status_code=401)
        with pytest.raises(ApiError) as failure:
            async with receiver.app.router.lifespan_context(receiver.app):
                pass
        assert failure.value.status_code == 401
        assert not hasattr(receiver.app.state, "invoker")
