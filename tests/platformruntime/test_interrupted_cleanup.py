"""PlatformRuntime interrupted cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.core.exceptions import AgentAlreadyRunningError, CleanupIncompleteError
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import ContactEventConfig
from tests.platformruntime.kit import ROOMS, serving_link
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import RoomCleanup


class TestInterruptedCleanupKeepsTheRuntime:
    """A restart is refused until the previous runtime's whole stop succeeds."""

    @pytest.fixture
    def cleanups(self) -> RoomCleanup:
        return RoomCleanup()

    @pytest.fixture
    def link(self) -> MagicMock:
        return serving_link(*ROOMS)

    @staticmethod
    def platform(**kwargs: object) -> PlatformRuntime:
        return PlatformRuntime(agent_id="agent-123", api_key="k", **kwargs)

    @staticmethod
    async def start(
        platform: PlatformRuntime, link: MagicMock, cleanups: RoomCleanup
    ) -> None:
        with patch("band.runtime.platform_runtime.BandLink", return_value=link):
            await platform.start(on_execute=AsyncMock(), on_cleanup=cleanups)

    async def serving(
        self, link: MagicMock, cleanups: RoomCleanup, **kwargs: object
    ) -> PlatformRuntime:
        platform = self.platform(**kwargs)
        await self.start(platform, link, cleanups)
        await wait_for_condition(lambda: set(platform.runtime.executions) == set(ROOMS))
        return platform

    async def assert_held_until_retried(
        self, platform: PlatformRuntime, link: MagicMock, cleanups: RoomCleanup
    ) -> None:
        assert platform.cleanup_pending
        with pytest.raises(CleanupIncompleteError):
            await self.start(platform, link, cleanups)
        with pytest.raises(AgentAlreadyRunningError):
            await self.start(self.platform(), link, cleanups)

        cleanups.failing = False
        cleanups.release.set()
        await platform.stop()
        assert sorted(cleanups.rooms) == list(ROOMS)
        assert not platform.cleanup_pending
        await self.start(self.platform(), serving_link(), cleanups)

    async def test_cancelled_stop_leaves_a_serving_room_owned(self, link, cleanups):
        platform = await self.serving(link, cleanups)
        cleanups.hold()
        stopping = asyncio.create_task(platform.stop())
        await cleanups.entered.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        cleanups.release.set()
        await wait_for_condition(lambda: len(cleanups.rooms) == 1)
        assert len(platform.runtime.executions) == 1
        await self.assert_held_until_retried(platform, link, cleanups)

    async def test_stop_cancelled_before_any_room_is_fenced(self, link, cleanups):
        platform = await self.serving(
            link, cleanups, contact_config=ContactEventConfig(broadcast_changes=True)
        )
        unsubscribing, release = asyncio.Event(), asyncio.Event()

        async def held_unsubscribe() -> None:
            unsubscribing.set()
            await release.wait()

        link.unsubscribe_agent_contacts.side_effect = held_unsubscribe
        stopping = asyncio.create_task(platform.stop())
        await unsubscribing.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        assert set(platform.runtime.executions) == set(ROOMS)
        await self.assert_held_until_retried(platform, link, cleanups)

    async def test_failed_presence_teardown_keeps_the_runtime(self, link, cleanups):
        platform = await self.serving(link, cleanups)
        presence = platform.runtime.presence
        presence_stop = presence.stop
        presence.stop = AsyncMock(side_effect=OSError("presence stop failed"))

        with pytest.raises(OSError, match="presence stop failed"):
            await platform.stop()
        presence.stop = presence_stop
        await self.assert_held_until_retried(platform, link, cleanups)

    @pytest.mark.parametrize("unwind", ["fails", "is_cancelled"])
    async def test_interrupted_failed_start_unwind_keeps_the_runtime(
        self, link, cleanups, unwind: str
    ):
        link.subscribe_agent_contacts.side_effect = OSError("contacts down")
        platform = self.platform(
            contact_config=ContactEventConfig(broadcast_changes=True)
        )
        if unwind == "fails":
            cleanups.failing = True
            with pytest.raises(OSError, match="contacts down"):
                await self.start(platform, link, cleanups)
        else:
            cleanups.hold()
            starting = asyncio.create_task(self.start(platform, link, cleanups))
            await cleanups.entered.wait()
            starting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starting
            cleanups.release.set()
            await wait_for_condition(lambda: len(cleanups.rooms) == 1)

        link.subscribe_agent_contacts.side_effect = None
        await self.assert_held_until_retried(platform, link, cleanups)
