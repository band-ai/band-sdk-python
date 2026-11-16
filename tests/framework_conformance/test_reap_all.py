"""Teardown attempts every owned resource and keeps the failed ones for a retry."""

from __future__ import annotations

from pytest_httpx import HTTPXMock

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.restclient import baseline_rest_client

REST_URL = "https://band.test"
AGENTS = ("blocked-agent", "removed-agent")
ROOMS = ("blocked-room", "removed-room")
REFUSED = 403


def agent_url(agent_id: str) -> str:
    return f"{REST_URL}/api/v1/me/agents/{agent_id}?force=true"


def room_url(room_id: str) -> str:
    return f"{REST_URL}/api/v1/me/chats/{room_id}"


def registered(agent_id: str) -> dict[str, object]:
    return {
        "data": {
            "agent": {
                "id": agent_id,
                "inserted_at": "2026-01-01T00:00:00Z",
                "name": agent_id,
                "owner_id": "owner-1",
            },
            "credentials": {"api_key": "nonsecret-fixture"},
        }
    }


def deleted(agent_id: str) -> dict[str, object]:
    return {"data": {"id": agent_id, "name": agent_id, "executions_deleted": 0}}


def deletes_sent(httpx_mock: HTTPXMock) -> list[str]:
    return [
        str(request.url)
        for request in httpx_mock.get_requests()
        if request.method == "DELETE"
    ]


class TestReapAll:
    async def test_reap_all_attempts_every_resource_and_retains_failed_ids(
        self, httpx_mock: HTTPXMock
    ) -> None:
        for agent_id in AGENTS:
            httpx_mock.add_response(
                method="POST",
                url=f"{REST_URL}/api/v1/me/agents/register",
                json=registered(agent_id),
            )
        resources = ResourceManager(
            user_client=baseline_rest_client(api_key="nonsecret", base_url=REST_URL),
            settings=BaselineSettings(),
            run_id="cleanup-test",
        )
        for agent_id in AGENTS:
            await resources.provision_agent(agent_id)
        for room_id in ROOMS:
            resources.adopt_room(room_id)
        blocked_agent, removed_agent = AGENTS
        blocked_room, removed_room = ROOMS
        httpx_mock.add_response(
            method="DELETE", url=room_url(blocked_room), status_code=REFUSED
        )
        httpx_mock.add_response(method="DELETE", url=room_url(removed_room))
        httpx_mock.add_response(
            method="DELETE", url=agent_url(blocked_agent), status_code=REFUSED, json={}
        )
        httpx_mock.add_response(
            method="DELETE", url=agent_url(removed_agent), json=deleted(removed_agent)
        )

        await resources.reap_all()

        assert deletes_sent(httpx_mock) == [
            room_url(blocked_room),
            room_url(removed_room),
            agent_url(blocked_agent),
            agent_url(removed_agent),
        ]

        httpx_mock.reset()
        httpx_mock.add_response(method="DELETE", url=room_url(blocked_room))
        httpx_mock.add_response(
            method="DELETE", url=agent_url(blocked_agent), json=deleted(blocked_agent)
        )

        await resources.reap_all()

        assert deletes_sent(httpx_mock) == [
            room_url(blocked_room),
            agent_url(blocked_agent),
        ]
