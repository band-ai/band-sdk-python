"""An agent this run still owns is not an orphan, even after the age gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    agent_name,
)

OWNED_AGENT_ID = "owned-1"
SESSION_RUN_ID = "78228ce0"


def listing_manager() -> tuple[ResourceManager, MagicMock, AsyncMock]:
    agent = MagicMock()
    agent.id = OWNED_AGENT_ID
    agent.name = agent_name(token="abcde", label="sandbox")
    agent.inserted_at = datetime.now(UTC) - timedelta(minutes=121)
    listed = MagicMock()
    listed.data = [agent]
    listed.metadata.next_cursor = None
    listed.metadata.has_more = False
    settings = MagicMock()
    settings.run.orphan_max_age_minutes = 120
    client = MagicMock()
    client.human_api_agents.list_my_agents = AsyncMock(return_value=listed)
    delete = AsyncMock()
    client.human_api_agents.delete_my_agent = delete
    manager = ResourceManager(
        user_client=client,
        settings=cast(BaselineSettings, settings),
        run_id=SESSION_RUN_ID,
    )
    return manager, agent, delete


class TestSweepOwned:
    async def test_aged_owned_agent_is_not_swept(self) -> None:
        manager, agent, delete = listing_manager()
        assert f"-{SESSION_RUN_ID}-" not in agent.name

        reaped = await manager.sweep_orphans(keep_ids=[agent.id])

        assert reaped == 0
        delete.assert_not_awaited()

    async def test_aged_agent_without_an_owner_is_swept(self) -> None:
        manager, agent, delete = listing_manager()

        reaped = await manager.sweep_orphans()

        assert reaped == 1
        delete.assert_awaited_once_with(agent.id, force=True)
