"""Tests for PlatformRuntime."""

from band_rest.core.api_error import ApiError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import AgentConfig, SessionConfig


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
        """Should create BandLink on start."""
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

                mock_link_class.assert_called_once_with(
                    agent_id="agent-123",
                    api_key="test-key",
                    ws_url="wss://app.band.ai/api/v1/socket/websocket",
                    rest_url="https://app.band.ai",
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
    async def test_delegates_to_link(self, mock_link, mock_runtime):
        """Should delegate to link.run_forever()."""
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
                await runtime.run_forever()

                mock_link.run_forever.assert_awaited_once()

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
        with pytest.raises(BandConfigError, match="already running"):
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


class TestNoopCleanup:
    """Tests for the noop cleanup function."""

    @pytest.mark.asyncio
    async def test_noop_cleanup_does_nothing(self):
        """The noop cleanup should not raise."""
        await PlatformRuntime._noop_cleanup("room-123")
