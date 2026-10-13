"""Retained transport ownership closes failures before SDK Query construction."""

from __future__ import annotations

import pytest

from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from tests.integrations.claude_sdk.conftest import Launches


async def start(manager: ClaudeSessionManager, room_id: str) -> None:
    async with manager.room(room_id).using():
        pass


class TestPreQueryStartup:
    """A startup failing before the SDK builds its Query still reaps the child."""

    async def test_a_failed_start_reaps_its_child(self, launches: Launches) -> None:
        manager = launches.manager

        with pytest.raises(ValueError, match="invalid literal"):
            await start(manager, "room-a")

        [child] = launches.children
        assert child.exited
        assert not manager.has_session("room-a")

    async def test_a_refused_close_keeps_the_child_and_its_claim_until_a_retry(
        self, launches: Launches
    ) -> None:
        manager = launches.manager
        launches.refuse_close = True

        with pytest.raises(ValueError, match="invalid literal"):
            await start(manager, "room-a")
        [child] = launches.children
        assert not child.exited
        assert manager.has_session("room-a")
        with pytest.raises(ValueError, match="both"):
            await start(manager, "room-b")
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await start(manager, "room-a")
        assert launches.children == [child]

        child.refuse_close = False
        await manager.cleanup_session("room-a")
        assert child.exited
        assert not manager.has_session("room-a")
