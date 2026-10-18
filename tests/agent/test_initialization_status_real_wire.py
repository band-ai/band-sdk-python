"""Initialization and identity-lock custody remain visible to host snapshots."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import GetAgentMeResponse

from band.agent import Agent
from band.core.exceptions import AgentAlreadyRunningError
from band.runtime.agentconfig import AgentConfig
from band.runtime.platform_runtime import PlatformRuntime
from band.testing import fake_phoenix_server
from tests.agent.kit import MinimalAdapter
from tests.runtime.conftest import make_link_mock


class TestInitializationStatus:
    @pytest.fixture
    def metadata_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[asyncio.Event, asyncio.Event]:
        entered, release = asyncio.Event(), asyncio.Event()
        rest = make_link_mock().rest
        response: GetAgentMeResponse = rest.agent_api_identity.get_agent_me.return_value

        async def get_me(**kwargs: object) -> GetAgentMeResponse:
            entered.set()
            await release.wait()
            return response

        rest.agent_api_identity.get_agent_me = AsyncMock(side_effect=get_me)
        rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=MagicMock(data=[], metadata=MagicMock(total_pages=1))
        )
        monkeypatch.setattr("band.platform.link.AsyncRestClient", lambda **kwargs: rest)
        return entered, release

    @pytest.mark.parametrize("single_instance", [True, False])
    async def test_agent_initialization_custody_survives_deferred_public_stop(
        self,
        metadata_gate: tuple[asyncio.Event, asyncio.Event],
        single_instance: bool,
    ) -> None:
        entered, release = metadata_gate
        async with fake_phoenix_server() as server:
            agent = Agent.create(
                adapter=MinimalAdapter(),
                agent_id="agent-initializing",
                api_key="test-key",
                ws_url=server.url,
                rest_url="https://test.invalid",
                config=AgentConfig(single_instance=single_instance),
            )
            assert not agent.status().cleanup_pending
            starting = asyncio.create_task(agent.start())
            try:
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                for status in (agent.status(), agent.runtime.status()):
                    assert status.cleanup_pending
                    assert not status.running and not status.connected
                    assert status.started_at is None and status.rooms == ()
                assert server.connection_count == 0
                if single_instance:
                    duplicate = Agent.create(
                        adapter=MinimalAdapter(),
                        agent_id="agent-initializing",
                        api_key="test-key",
                    )
                    with pytest.raises(AgentAlreadyRunningError):
                        await duplicate.start()
                assert await agent.stop() is True
                assert not starting.done()
                assert agent.status().cleanup_pending
                assert agent.runtime.status().cleanup_pending
                release.set()
                await starting
                assert not agent.status().cleanup_pending
                assert not agent.runtime.status().cleanup_pending
                assert not agent.status().running and not agent.status().connected
            finally:
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                await agent.stop()

    @pytest.mark.parametrize("single_instance", [True, False])
    async def test_standalone_runtime_observes_metadata_initialization_custody(
        self,
        metadata_gate: tuple[asyncio.Event, asyncio.Event],
        single_instance: bool,
    ) -> None:
        entered, release = metadata_gate
        async with fake_phoenix_server() as server:
            runtime = PlatformRuntime(
                agent_id="runtime-initializing",
                api_key="test-key",
                ws_url=server.url,
                rest_url="https://test.invalid",
                config=AgentConfig(single_instance=single_instance),
            )
            assert not runtime.status().cleanup_pending
            starting = asyncio.create_task(runtime.start(on_execute=AsyncMock()))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                status = runtime.status()
                assert status.cleanup_pending
                assert not status.running and not status.connected
                assert status.started_at is None and status.rooms == ()
                assert server.connection_count == 0
                release.set()
                await starting
                assert runtime.status().running and runtime.status().connected
                assert not runtime.status().cleanup_pending
            finally:
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                await runtime.stop()
            assert not runtime.status().cleanup_pending

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_cancelled_metadata_initialization_releases_observed_custody(
        self,
        metadata_gate: tuple[asyncio.Event, asyncio.Event],
        hosting: str,
    ) -> None:
        entered, release = metadata_gate
        async with fake_phoenix_server() as server:
            runtime = PlatformRuntime(
                agent_id="cancelled-metadata",
                api_key="test-key",
                ws_url=server.url,
                rest_url="https://test.invalid",
            )
            owner = (
                Agent(runtime=runtime, adapter=MinimalAdapter())
                if hosting == "agent"
                else runtime
            )
            starting = asyncio.create_task(
                owner.start()
                if hosting == "agent"
                else runtime.start(on_execute=AsyncMock())
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                assert owner.status().cleanup_pending
                starting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await starting
                assert not owner.status().cleanup_pending
                assert not runtime.status().cleanup_pending
                assert not owner.status().running and not owner.status().connected
                assert owner.status().started_at is None
                assert server.connection_count == 0
                replacement = PlatformRuntime(
                    agent_id="cancelled-metadata", api_key="test-key"
                )
                replacement.claim_single_instance()
                replacement.release_single_instance()
            finally:
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                await owner.stop()

    def test_explicit_identity_guard_custody_is_visible_without_starting(self) -> None:
        agent = Agent.create(
            adapter=MinimalAdapter(),
            agent_id="guard-held-before-start",
            api_key="test-key",
        )
        agent.runtime.claim_single_instance()
        try:
            assert agent.status().cleanup_pending
            assert agent.runtime.status().cleanup_pending
            assert not agent.status().running and not agent.status().connected
            assert agent.status().started_at is None
        finally:
            agent.runtime.release_single_instance()
        assert not agent.status().cleanup_pending
        assert not agent.runtime.status().cleanup_pending
