"""Early failure and ownership refusal release only the accepted lifetime."""

from __future__ import annotations

import asyncio

import pytest

from band.agent import Agent
from band.core.exceptions import AgentAlreadyRunningError, BandConfigError
from band.runtime.oneshot import OneShotInvoker
from tests.adaptercleanup import PassiveAdapter, TeardownAdapter, resource_peer
from tests.core.agentlifecycle.kit import make_runtime
from tests.runtime.conftest import make_link_mock


class TestStartupOwnership:
    async def test_concurrent_agent_start_cannot_take_or_release_the_active_claim(
        self,
    ) -> None:
        adapter = TeardownAdapter()
        runtime = make_runtime()
        entered, release = asyncio.Event(), asyncio.Event()

        async def initialize() -> None:
            entered.set()
            await release.wait()

        runtime.initialize.side_effect = initialize
        async with resource_peer() as client:
            adapter.add_cleanup(client.aclose)
            host = Agent(runtime=runtime, adapter=adapter)
            starting = asyncio.create_task(host.start())
            await entered.wait()
            try:
                with pytest.raises(BandConfigError, match="startup is already running"):
                    await host.start()
                assert (await client.get("/resource")).status_code == 200
            finally:
                release.set()
                await starting
                await host.stop()
            assert adapter.cleanup_calls == 1

    async def test_cancelled_oneshot_startup_waiter_keeps_owned_start_and_resources(
        self,
    ) -> None:
        adapter = TeardownAdapter()
        link = make_link_mock()
        identity = link.rest.agent_api_identity.get_agent_me
        response = identity.return_value
        entered, release = asyncio.Event(), asyncio.Event()

        async def metadata(**kwargs: object) -> object:
            entered.set()
            await release.wait()
            return response

        identity.side_effect = metadata
        async with resource_peer() as client:
            adapter.add_cleanup(client.aclose)
            host = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")
            starting = asyncio.create_task(host.startup())
            await entered.wait()
            starting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert (await client.get("/resource")).status_code == 200
            stopping = asyncio.create_task(host.shutdown())
            release.set()
            await stopping
            assert client.is_closed

    @pytest.mark.parametrize("failure_stage", ["identity", "metadata", "framework"])
    async def test_agent_accepted_failure_releases_resources_with_original_error(
        self, failure_stage: str
    ) -> None:
        adapter = TeardownAdapter()
        runtime = make_runtime()
        error = (
            AgentAlreadyRunningError("Identity is busy")
            if failure_stage == "identity"
            else LookupError("Startup failed")
        )
        match failure_stage:
            case "identity":
                runtime.claim_single_instance.side_effect = error
            case "metadata":
                runtime.initialize.side_effect = error
            case "framework":

                async def fail(*args: object, **kwargs: object) -> None:
                    raise error

                adapter.on_started = fail
        async with resource_peer() as client:
            adapter.add_cleanup(client.aclose)
            host = Agent(runtime=runtime, adapter=adapter)
            with pytest.raises(type(error)) as raised:
                await host.start()
            assert raised.value is error
            assert client.is_closed
            assert adapter.cleanup_calls == (1 if failure_stage == "framework" else 0)

    async def test_oneshot_metadata_failure_owns_resources_without_framework_teardown(
        self,
    ) -> None:
        adapter = TeardownAdapter()
        link = make_link_mock()
        link.rest.agent_api_identity.get_agent_me.side_effect = LookupError(
            "Metadata failed"
        )
        async with resource_peer() as client:
            adapter.add_cleanup(client.aclose)
            host = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")
            with pytest.raises(LookupError, match="Metadata failed"):
                await host.startup()
            assert client.is_closed
            assert adapter.cleanup_calls == 0

    async def test_foreign_host_refused_before_platform_or_identity_mutation(
        self,
    ) -> None:
        adapter = PassiveAdapter()
        first_runtime, second_runtime = make_runtime(), make_runtime()
        async with resource_peer() as client:
            adapter.add_cleanup(client.aclose)
            first = Agent(runtime=first_runtime, adapter=adapter)
            second = Agent(runtime=second_runtime, adapter=adapter)
            await first.start()
            platform = adapter.platform
            with pytest.raises(BandConfigError, match="another host"):
                await second.start()
            second_runtime.claim_single_instance.assert_not_called()
            second_runtime.initialize.assert_not_awaited()
            second_runtime.release_single_instance.assert_not_called()
            assert adapter.platform is platform
            assert (await client.get("/resource")).status_code == 200
            await first.stop()

    async def test_failed_start_keeps_original_exception_and_same_host_retry(
        self,
    ) -> None:
        adapter = PassiveAdapter()
        runtime = make_runtime()
        original = LookupError("Metadata failed")
        runtime.initialize.side_effect = original
        failing = True
        attempts = 0

        async def close() -> None:
            nonlocal attempts
            attempts += 1
            if failing:
                raise OSError("Close failed")

        adapter.add_cleanup(close)
        host = Agent(runtime=runtime, adapter=adapter)
        with pytest.raises(LookupError) as raised:
            await host.start()
        assert raised.value is original
        assert any("retain this host" in note for note in original.__notes__)
        failing = False
        await host.stop()
        assert attempts == 2
