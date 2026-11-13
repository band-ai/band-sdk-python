"""Strict acceptance teardown attempts every resource and fails visibly."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager


class TestStrictReap:
    @pytest.mark.parametrize("strict", [False, True])
    async def test_reap_all_attempts_every_resource_and_retains_failed_ids(
        self, monkeypatch: pytest.MonkeyPatch, strict: bool
    ) -> None:
        client = AsyncMock()
        client.human_api_agents.register_my_agent.side_effect = [
            SimpleNamespace(
                data=SimpleNamespace(
                    agent=SimpleNamespace(id=agent_id),
                    credentials=SimpleNamespace(api_key="nonsecret-fixture"),
                )
            )
            for agent_id in ("blocked-agent", "removed-agent")
        ]
        resources = ResourceManager(
            user_client=client, settings=BaselineSettings(), run_id="cleanup-test"
        )
        for label in ("blocked", "removed"):
            await resources.provision_agent(label)
        for room_id in ("blocked-room", "removed-room"):
            resources.adopt_room(room_id)
        room_cleanup = AsyncMock(side_effect=[RuntimeError("room blocked"), None])
        monkeypatch.setattr(resources.user_ops, "delete_room", room_cleanup)
        client.human_api_agents.delete_my_agent.side_effect = [
            RuntimeError("agent blocked"),
            None,
        ]

        if strict:
            with pytest.raises(ExceptionGroup) as caught:
                await resources.reap_all(strict=True)
            assert [str(error) for error in caught.value.exceptions] == [
                "room blocked",
                "agent blocked",
            ]
            assert [error.__notes__ for error in caught.value.exceptions] == [
                ["Cleanup incomplete for room blocked-room"],
                ["Cleanup incomplete for agent blocked-agent"],
            ]
        else:
            await resources.reap_all()
        assert [call.args[0] for call in room_cleanup.await_args_list] == [
            "blocked-room",
            "removed-room",
        ]
        assert [
            call.args[0]
            for call in client.human_api_agents.delete_my_agent.await_args_list
        ] == ["blocked-agent", "removed-agent"]

        room_cleanup.reset_mock(side_effect=True)
        client.human_api_agents.delete_my_agent.reset_mock(side_effect=True)
        await resources.reap_all(strict=True)
        room_cleanup.assert_awaited_once_with("blocked-room")
        client.human_api_agents.delete_my_agent.assert_awaited_once_with(
            "blocked-agent", force=True
        )
