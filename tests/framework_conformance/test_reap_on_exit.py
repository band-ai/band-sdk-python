"""Test-end cleanup reaps tracked resources without hiding the test error."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager

REGISTERED_AGENT_ID = "agent-1"


def manager() -> tuple[ResourceManager, AsyncMock]:
    def respond(**_kwargs: object) -> MagicMock:
        response = MagicMock()
        response.data.agent.id = REGISTERED_AGENT_ID
        response.data.credentials.api_key = "test-key"
        return response

    client = MagicMock()
    client.human_api_agents.register_my_agent = AsyncMock(side_effect=respond)
    delete = AsyncMock()
    client.human_api_agents.delete_my_agent = delete
    return (
        ResourceManager(
            user_client=client,
            settings=cast(BaselineSettings, MagicMock()),
            run_id="session",
        ),
        delete,
    )


class TestReapOnExit:
    async def test_reaps_on_normal_exit(self) -> None:
        resources, delete = manager()

        async with resources.reap_on_exit():
            await resources.provision_agent("copilot_sdk")

        delete.assert_awaited_once_with(REGISTERED_AGENT_ID, force=True)

    async def test_body_error_survives_a_failed_reap(self) -> None:
        resources, delete = manager()
        delete.side_effect = RuntimeError("delete failed")

        with pytest.raises(RuntimeError, match="boom"):
            async with resources.reap_on_exit():
                await resources.provision_agent("copilot_sdk")
                raise RuntimeError("boom")

        delete.assert_awaited_once_with(REGISTERED_AGENT_ID, force=True)

    async def test_disabled_does_not_reap(self) -> None:
        resources, delete = manager()

        async with resources.reap_on_exit(enabled=False):
            await resources.provision_agent("copilot_sdk")

        delete.assert_not_awaited()
