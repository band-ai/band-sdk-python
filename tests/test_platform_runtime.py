"""Tests for PlatformRuntime."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from band_rest.core.api_error import ApiError

from band.core.exceptions import AgentAlreadyRunningError, CleanupIncompleteError
from band.core.types import ConflictPolicy
from band.runtime.capabilities import FeatureFlag
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import AgentConfig, ContactEventConfig, SessionConfig
from tests.runtime.conftest import chat_row, make_link_mock, wait_for_condition
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


@pytest.fixture
def mock_link():
    """Create mock BandLink."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_identity = MagicMock()
    link.disconnect = AsyncMock()
    link.run_forever = AsyncMock()

    # Mock agent metadata response
    mock_agent = MagicMock()
    mock_agent.name = "TestBot"
    mock_agent.description = "A test bot"

    mock_response = MagicMock()
    mock_response.data = mock_agent
    link.rest.agent_api_identity.get_agent_me = AsyncMock(return_value=mock_response)

    return link


@pytest.fixture
def mock_runtime():
    """Create mock AgentRuntime."""
    runtime = MagicMock()
    runtime.start = AsyncMock()
    runtime.stop = AsyncMock()
    return runtime


class TestInitialization:
    """Tests for PlatformRuntime initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert runtime.agent_id == "agent-123"
        assert runtime._api_key == "test-key"
        assert runtime._ws_url == "wss://app.band.ai/api/v1/socket/websocket"
        assert runtime._rest_url == "https://app.band.ai"
        assert isinstance(runtime._config, AgentConfig)
        assert isinstance(runtime._session_config, SessionConfig)

    def test_custom_urls(self):
        """Should accept custom URLs."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
            ws_url="wss://custom.example.com/ws",
            rest_url="https://custom.example.com",
        )

        assert runtime._ws_url == "wss://custom.example.com/ws"
        assert runtime._rest_url == "https://custom.example.com"

    def test_custom_configs(self):
        """Should accept custom configs."""
        config = AgentConfig(auto_subscribe_existing_rooms=False)
        session_config = SessionConfig(enable_context_cache=False)

        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
            config=config,
            session_config=session_config,
        )

        assert runtime._config is config
        assert runtime._session_config is session_config

    def test_initial_state(self):
        """Should have uninitialized state before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert runtime._link is None
        assert runtime._runtime is None
        assert runtime._agent_name == ""
        assert runtime._agent_description == ""


class TestProperties:
    """Tests for PlatformRuntime properties."""

    def test_agent_id_property(self):
        """Should expose agent_id."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_id == "agent-123"

    def test_agent_name_empty_before_start(self):
        """agent_name should be empty before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_name == ""

    def test_agent_description_empty_before_start(self):
        """agent_description should be empty before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_description == ""

    def test_link_raises_before_start(self):
        """link property should raise if not started."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="Runtime not started"):
            _ = runtime.link

    def test_runtime_raises_before_start(self):
        """runtime property should raise if not started."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="Runtime not started"):
            _ = runtime.runtime


class TestStart:
    """Tests for PlatformRuntime.start() method."""

    @pytest.mark.asyncio
    async def test_creates_link(self, mock_link, mock_runtime):
        """Should create BandLink on start, forwarding the conflict policy."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                    config=AgentConfig(conflict_policy=ConflictPolicy.REJECT),
                )

                on_execute = AsyncMock()
                await runtime.start(on_execute=on_execute)

                mock_link_class.assert_called_once_with(
                    agent_id="agent-123",
                    api_key="test-key",
                    ws_url="wss://app.band.ai/api/v1/socket/websocket",
                    rest_url="https://app.band.ai",
                    conflict_policy=ConflictPolicy.REJECT,
                )

    @pytest.mark.asyncio
    async def test_fetches_agent_metadata(self, mock_link, mock_runtime):
        """Should fetch agent metadata after creating link."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                on_execute = AsyncMock()
                await runtime.start(on_execute=on_execute)

                mock_link.rest.agent_api_identity.get_agent_me.assert_awaited_once()
                assert runtime.agent_name == "TestBot"
                assert runtime.agent_description == "A test bot"

    @pytest.mark.asyncio
    async def test_captures_feature_flags(self, mock_link, mock_runtime):
        """The /me response's feature_flags is retained for capability negotiation."""
        mock_link.rest.agent_api_identity.get_agent_me.return_value.data.feature_flags = {
            FeatureFlag.FILE_TRANSFER: True
        }
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(agent_id="agent-123", api_key="test-key")
                assert runtime.feature_flags is None

                await runtime.start(on_execute=AsyncMock())

                assert runtime.feature_flags == {FeatureFlag.FILE_TRANSFER: True}

    @pytest.mark.asyncio
    async def test_creates_agent_runtime(self, mock_link, mock_runtime):
        """Should create AgentRuntime with correct config."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                session_config = SessionConfig()
                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                    session_config=session_config,
                )

                on_execute = AsyncMock()
                on_cleanup = AsyncMock()
                await runtime.start(on_execute=on_execute, on_cleanup=on_cleanup)

                mock_runtime_class.assert_called_once()
                call_kwargs = mock_runtime_class.call_args.kwargs
                assert call_kwargs["link"] is mock_link
                assert call_kwargs["agent_id"] == "agent-123"
                assert call_kwargs["on_execute"] is on_execute
                assert call_kwargs["session_config"] is session_config
                assert call_kwargs["on_session_cleanup"] is on_cleanup

    @pytest.mark.asyncio
    async def test_passes_participant_callbacks_to_agent_runtime(
        self, mock_link, mock_runtime
    ):
        """Should forward participant callbacks into AgentRuntime."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                on_participant_added = AsyncMock()
                on_participant_removed = AsyncMock()
                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                    on_participant_added=on_participant_added,
                    on_participant_removed=on_participant_removed,
                )

                await runtime.start(on_execute=AsyncMock())

                call_kwargs = mock_runtime_class.call_args.kwargs
                assert call_kwargs["on_participant_added"] is on_participant_added
                assert call_kwargs["on_participant_removed"] is on_participant_removed

    @pytest.mark.asyncio
    async def test_uses_noop_cleanup_when_none_provided(self, mock_link, mock_runtime):
        """Should use noop cleanup when none provided."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                on_execute = AsyncMock()
                await runtime.start(on_execute=on_execute)

                call_kwargs = mock_runtime_class.call_args.kwargs
                # Should have a cleanup function (the noop)
                assert call_kwargs["on_session_cleanup"] is not None

    @pytest.mark.asyncio
    async def test_starts_agent_runtime(self, mock_link, mock_runtime):
        """Should start the AgentRuntime."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                on_execute = AsyncMock()
                await runtime.start(on_execute=on_execute)

                mock_runtime.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wires_control_hook_to_runtime(self, mock_link, mock_runtime):
        """start() should route control signals to AgentRuntime.handle_control."""
        mock_runtime.handle_control = AsyncMock()
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(agent_id="agent-123", api_key="test-key")
                await runtime.start(on_execute=AsyncMock())

                assert mock_link.on_control == mock_runtime.handle_control

    @pytest.mark.asyncio
    async def test_raises_on_missing_metadata(self, mock_link, mock_runtime):
        """Should raise if agent metadata response is empty."""
        mock_link.rest.agent_api_identity.get_agent_me = AsyncMock(
            return_value=MagicMock(data=None)
        )

        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link

            runtime = PlatformRuntime(
                agent_id="agent-123",
                api_key="test-key",
            )

            on_execute = AsyncMock()
            with pytest.raises(RuntimeError, match="Failed to fetch agent metadata"):
                await runtime.start(on_execute=on_execute)

    @pytest.mark.asyncio
    async def test_raises_on_missing_description(self, mock_link, mock_runtime):
        """Should raise if agent has no description."""
        mock_agent = MagicMock()
        mock_agent.name = "TestBot"
        mock_agent.description = None

        mock_link.rest.agent_api_identity.get_agent_me = AsyncMock(
            return_value=MagicMock(data=mock_agent)
        )

        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link

            runtime = PlatformRuntime(
                agent_id="agent-123",
                api_key="test-key",
            )

            on_execute = AsyncMock()
            with pytest.raises(ValueError, match="has no description"):
                await runtime.start(on_execute=on_execute)

    @pytest.mark.asyncio
    async def test_retries_metadata_fetch_on_rate_limit(self, mock_link, mock_runtime):
        """Should retry agent metadata fetch when the platform returns 429."""
        mock_agent = MagicMock()
        mock_agent.name = "TestBot"
        mock_agent.description = "A test bot"
        mock_link.rest.agent_api_identity.get_agent_me = AsyncMock(
            side_effect=[
                ApiError(status_code=429, headers={}, body=""),
                ApiError(status_code=429, headers={}, body=""),
                MagicMock(data=mock_agent),
            ]
        )

        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime
                with patch(
                    "band.runtime.platform_runtime.asyncio.sleep",
                    new=AsyncMock(),
                ) as mock_sleep:
                    runtime = PlatformRuntime(
                        agent_id="agent-123",
                        api_key="test-key",
                    )

                    await runtime.start(on_execute=AsyncMock())

                    assert (
                        mock_link.rest.agent_api_identity.get_agent_me.await_count == 3
                    )
                    assert mock_sleep.await_count == 2


class TestStop:
    """Tests for PlatformRuntime.stop() method."""

    @pytest.mark.asyncio
    async def test_stops_runtime(self, mock_link, mock_runtime):
        """Should stop the AgentRuntime."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                await runtime.stop()

                mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_link(self, mock_link, mock_runtime):
        """Should disconnect the BandLink."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_even_when_runtime_stop_fails(
        self, mock_link, mock_runtime
    ):
        """A failed AgentRuntime.stop still disconnects so run_forever can end."""
        mock_runtime.stop = AsyncMock(side_effect=RuntimeError("teardown failed"))
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                with pytest.raises(RuntimeError, match="teardown failed"):
                    await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_even_when_contacts_unsubscribe_fails(
        self, mock_link, mock_runtime
    ):
        """A failed contacts unsubscribe still disconnects the link."""
        mock_link.unsubscribe_agent_contacts = AsyncMock(
            side_effect=RuntimeError("unsubscribe failed")
        )
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                runtime._contacts_subscribed = True
                await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        """Should handle stop before start gracefully."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        # Should not raise
        await runtime.stop()


class TestRunForever:
    """Tests for PlatformRuntime.run_forever() method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_delegates_to_link(self, mock_link, mock_runtime, kwargs, installs):
        """run_forever hands the host's signal choice to the link."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                await runtime.run_forever(**kwargs)

                mock_link.run_forever.assert_awaited_once_with(
                    install_signal_handlers=installs
                )

    @pytest.mark.asyncio
    async def test_run_forever_before_start_is_safe(self):
        """Should handle run_forever before start gracefully."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        # Should not raise (just does nothing)
        await runtime.run_forever()


@pytest.fixture
def platform_mocks(mock_link, mock_runtime):
    """Run a test with BandLink and AgentRuntime replaced by the mocks."""
    with (
        patch("band.runtime.platform_runtime.BandLink", return_value=mock_link),
        patch("band.runtime.platform_runtime.AgentRuntime", return_value=mock_runtime),
    ):
        yield


class TestSingleInstance:
    """One running process per agent id (see runtime/single_instance.py)."""

    @pytest.mark.asyncio
    async def test_second_start_of_same_agent_is_refused(self, platform_mocks):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        second = PlatformRuntime(agent_id="agent-123", api_key="k")

        await first.start(on_execute=AsyncMock())
        with pytest.raises(AgentAlreadyRunningError, match="already running"):
            await second.start(on_execute=AsyncMock())
        await first.stop()

    @pytest.mark.asyncio
    async def test_stop_releases_the_agent_for_restart(self, platform_mocks):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        await first.start(on_execute=AsyncMock())
        await first.stop()

        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        await second.start(on_execute=AsyncMock())
        await second.stop()

    @pytest.mark.asyncio
    async def test_failed_start_does_not_hold_the_lock(
        self, platform_mocks, mock_runtime
    ):
        """A retry in the same process must not see its own dead lock."""
        mock_runtime.start.side_effect = [RuntimeError("boom"), None]
        runtime = PlatformRuntime(agent_id="agent-123", api_key="k")

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.start(on_execute=AsyncMock())
        await runtime.start(on_execute=AsyncMock())
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_opt_out_allows_duplicates(self, platform_mocks):
        config = AgentConfig(single_instance=False)
        first = PlatformRuntime(agent_id="agent-123", api_key="k", config=config)
        second = PlatformRuntime(agent_id="agent-123", api_key="k", config=config)
        await first.start(on_execute=AsyncMock())
        await second.start(on_execute=AsyncMock())
        await first.stop()
        await second.stop()


class TestIncompleteCleanupOwnership:
    """The runtime and its guard stay held until cleanup actually succeeds."""

    @staticmethod
    def fail_cleanup(mock_runtime: MagicMock) -> None:
        mock_runtime.stop.side_effect = ExceptionGroup(
            "Room cleanup remains incomplete", [RuntimeError("room cleanup failed")]
        )

    @staticmethod
    def finish_cleanup(mock_runtime: MagicMock) -> None:
        mock_runtime.stop.side_effect = None

    @pytest.mark.asyncio
    async def test_incomplete_stop_keeps_guard_and_blocks_restart(
        self, platform_mocks, mock_runtime
    ):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        await first.start(on_execute=AsyncMock())
        self.fail_cleanup(mock_runtime)

        with pytest.raises(ExceptionGroup):
            await first.stop()
        with pytest.raises(AgentAlreadyRunningError):
            await second.start(on_execute=AsyncMock())
        with pytest.raises(CleanupIncompleteError):
            await first.start(on_execute=AsyncMock())

        self.finish_cleanup(mock_runtime)
        await first.stop()
        await second.start(on_execute=AsyncMock())
        await second.stop()

    @pytest.mark.asyncio
    async def test_failed_start_with_failed_unwind_keeps_guard(
        self, platform_mocks, mock_runtime
    ):
        mock_runtime.start.side_effect = RuntimeError("boom")
        self.fail_cleanup(mock_runtime)
        first = PlatformRuntime(agent_id="agent-123", api_key="k")

        with pytest.raises(RuntimeError, match="boom"):
            await first.start(on_execute=AsyncMock())
        with pytest.raises(AgentAlreadyRunningError):
            await PlatformRuntime(agent_id="agent-123", api_key="k").start(
                on_execute=AsyncMock()
            )
        self.finish_cleanup(mock_runtime)
        await first.stop()

    @pytest.mark.asyncio
    async def test_host_claimed_guard_is_released_only_by_the_host(
        self, platform_mocks
    ):
        hosted = PlatformRuntime(agent_id="agent-123", api_key="k")
        hosted.claim_single_instance()
        await hosted.start(on_execute=AsyncMock())
        await hosted.stop()

        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        with pytest.raises(AgentAlreadyRunningError):
            await second.start(on_execute=AsyncMock())
        hosted.release_single_instance()
        await second.start(on_execute=AsyncMock())
        await second.stop()


class TestNoopCleanup:
    """Tests for the noop cleanup function."""

    @pytest.mark.asyncio
    async def test_noop_cleanup_does_nothing(self):
        """The noop cleanup should not raise."""
        await PlatformRuntime._noop_cleanup("room-123")


class RoomCleanups:
    """The adapter's room cleanup, held or failed on demand."""

    def __init__(self) -> None:
        self.cleaned: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failing = False

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def __call__(self, room_id: str) -> None:
        self.entered.set()
        await self.release.wait()
        if self.failing:
            raise RuntimeError("room cleanup failed")
        self.cleaned.append(room_id)


def serving_link(*room_ids: str) -> MagicMock:
    """A link whose presence admits ``room_ids`` and then delivers nothing."""
    link = make_link_mock()
    link.agent_id = "agent-123"
    link.is_connected = True
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()
    link.subscribe_agent_contacts = AsyncMock()
    link.unsubscribe_agent_contacts = AsyncMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(
            data=[chat_row(room_id) for room_id in room_ids],
            metadata=MagicMock(total_pages=1),
        )
    )

    async def no_events():
        await asyncio.Event().wait()
        yield

    link.__aiter__ = lambda _self: no_events()
    return link


ROOMS = ("room-1", "room-2")


class TestInterruptedCleanupKeepsTheRuntime:
    """A restart is refused until the previous runtime's whole stop succeeds."""

    @pytest.fixture
    def cleanups(self) -> RoomCleanups:
        return RoomCleanups()

    @pytest.fixture
    def link(self) -> MagicMock:
        return serving_link(*ROOMS)

    @staticmethod
    def platform(**kwargs: object) -> PlatformRuntime:
        return PlatformRuntime(agent_id="agent-123", api_key="k", **kwargs)

    @staticmethod
    async def start(
        platform: PlatformRuntime, link: MagicMock, cleanups: RoomCleanups
    ) -> None:
        with patch("band.runtime.platform_runtime.BandLink", return_value=link):
            await platform.start(on_execute=AsyncMock(), on_cleanup=cleanups)

    async def serving(
        self, link: MagicMock, cleanups: RoomCleanups, **kwargs: object
    ) -> PlatformRuntime:
        platform = self.platform(**kwargs)
        await self.start(platform, link, cleanups)
        await wait_for_condition(lambda: set(platform.runtime.executions) == set(ROOMS))
        return platform

    async def assert_held_until_retried(
        self, platform: PlatformRuntime, link: MagicMock, cleanups: RoomCleanups
    ) -> None:
        assert platform.cleanup_pending
        with pytest.raises(CleanupIncompleteError):
            await self.start(platform, link, cleanups)
        with pytest.raises(AgentAlreadyRunningError):
            await self.start(self.platform(), link, cleanups)

        cleanups.failing = False
        cleanups.release.set()
        await platform.stop()
        assert sorted(cleanups.cleaned) == list(ROOMS)
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
        await wait_for_condition(lambda: len(cleanups.cleaned) == 1)
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
            await wait_for_condition(lambda: len(cleanups.cleaned) == 1)

        link.subscribe_agent_contacts.side_effect = None
        await self.assert_held_until_retried(platform, link, cleanups)
