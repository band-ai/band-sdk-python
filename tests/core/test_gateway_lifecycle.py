"""Tests for shared gateway lifecycle (GatewayBase + claim map)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.core.exceptions import LifecycleError
from band.core.gateways import (
    GatewayBase,
    claim_agent,
    release_agent,
    stop_all,
)
from band.core.protocols import Gateway


class FakeGateway(GatewayBase[object]):
    """Records lifecycle hooks for GatewayBase tests."""

    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        self.start_calls = 0
        self.stop_calls = 0
        self.serve_calls = 0
        self.fail_stop = False
        self.serve_blocks: asyncio.Event | None = None

    async def _start_resources(self) -> None:
        self.start_calls += 1
        await self._agent.start()

    async def _stop_resources(self) -> None:
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("stop failed")
        if self._agent.is_running:
            await self._agent.stop()

    async def _serve_transport(self) -> None:
        self.serve_calls += 1
        if self.serve_blocks is not None:
            await self.serve_blocks.wait()


def make_agent(*, started: bool = False) -> Agent:
    runtime = AsyncMock()
    runtime.agent_name = "test-agent"
    runtime.agent_description = "desc"
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    adapter = AsyncMock()
    agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]
    if started:
        agent._started = True
    return agent


@pytest.fixture(autouse=True)
def clear_claims() -> None:
    from band.core import gateways as gw

    gw._agent_claims.clear()


class TestClaimMap:
    def test_claim_and_release(self) -> None:
        agent = make_agent()
        owner = object()
        claim_agent(agent, owner)
        release_agent(agent, owner)
        claim_agent(agent, owner)
        release_agent(agent, owner)

    def test_double_claim_raises(self) -> None:
        agent = make_agent()
        claim_agent(agent, object())
        with pytest.raises(LifecycleError, match="already owned"):
            claim_agent(agent, object())

    def test_claim_running_agent_raises(self) -> None:
        agent = make_agent(started=True)
        with pytest.raises(LifecycleError, match="already started"):
            claim_agent(agent, object())


class TestGatewayBaseLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self) -> None:
        gateway = FakeGateway(make_agent())
        await gateway.start()
        await gateway.start()
        assert gateway.start_calls == 1

        await gateway.stop()
        await gateway.stop()
        assert gateway.stop_calls == 1

    @pytest.mark.asyncio
    async def test_concurrent_start_and_stop_wait_for_one_startup(self) -> None:
        entered_start = asyncio.Event()
        release_start = asyncio.Event()

        class BlockingGateway(FakeGateway):
            async def _start_resources(self) -> None:
                self.start_calls += 1
                entered_start.set()
                await release_start.wait()
                await self._agent.start()

        gateway = BlockingGateway(make_agent())
        first_start = asyncio.create_task(gateway.start())
        await entered_start.wait()
        second_start = asyncio.create_task(gateway.start())
        stop = asyncio.create_task(gateway.stop())
        await asyncio.sleep(0)

        assert not stop.done()
        release_start.set()
        await asyncio.gather(first_start, second_start, stop)

        assert gateway.start_calls == 1
        assert gateway.stop_calls == 1
        assert gateway.state == "stopped"

    @pytest.mark.asyncio
    async def test_restart_after_stop(self) -> None:
        agent = make_agent()
        gateway = FakeGateway(agent)
        await gateway.start()
        await gateway.stop()
        await gateway.start()
        assert gateway.start_calls == 2
        assert agent.is_running

    @pytest.mark.asyncio
    async def test_serve_before_start_raises(self) -> None:
        gateway = FakeGateway(make_agent())
        with pytest.raises(LifecycleError, match="serve\\(\\) requires start"):
            await gateway.serve()

    @pytest.mark.asyncio
    async def test_already_started_agent_raises(self) -> None:
        gateway = FakeGateway(make_agent(started=True))
        with pytest.raises(LifecycleError, match="already started"):
            await gateway.start()

    @pytest.mark.asyncio
    async def test_same_agent_two_gateways_raises(self) -> None:
        agent = make_agent()
        first = FakeGateway(agent)
        second = FakeGateway(agent)
        await first.start()
        with pytest.raises(LifecycleError, match="already started"):
            await second.start()
        await first.stop()

    @pytest.mark.asyncio
    async def test_aexit_preserves_primary_when_stop_fails(self) -> None:
        gateway = FakeGateway(make_agent())
        gateway.fail_stop = True

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async with gateway:
                raise ValueError("primary failure")

        group = exc_info.value
        assert str(group.exceptions[0]) == "primary failure"
        assert isinstance(group.exceptions[1], RuntimeError)
        assert group.__cause__ is group.exceptions[0]

    @pytest.mark.asyncio
    async def test_aexit_cleanup_failure_without_primary(self) -> None:
        gateway = FakeGateway(make_agent())
        await gateway.start()
        gateway.fail_stop = True

        with pytest.raises(RuntimeError, match="stop failed"):
            await gateway.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_serve_failure_triggers_stop(self) -> None:
        agent = make_agent()
        gateway = FakeGateway(agent)

        async def boom() -> None:
            raise RuntimeError("serve crashed")

        gateway._serve_transport = boom  # type: ignore[method-assign]
        await gateway.start()

        with pytest.raises(RuntimeError, match="serve crashed"):
            await gateway.serve()

        assert gateway.state == "stopped"
        assert not agent.is_running

    @pytest.mark.asyncio
    async def test_cancellation_during_cleanup(self) -> None:
        agent = make_agent()
        gateway = FakeGateway(agent)
        await gateway.start()

        async def cancel_on_stop() -> None:
            raise asyncio.CancelledError()

        gateway._stop_resources = cancel_on_stop  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            await gateway.stop()

    def test_static_gateway_protocol(self) -> None:
        gateway: Gateway = FakeGateway(make_agent())
        assert isinstance(gateway, GatewayBase)


class TestGatewayBaseFailureModes:
    @pytest.mark.asyncio
    async def test_failed_start_rolls_back_running_agent(self) -> None:
        agent = make_agent()

        class BoomGateway(GatewayBase[object]):
            def __init__(self, agent: Agent) -> None:
                super().__init__(agent)
                self.stop_calls = 0

            async def _start_resources(self) -> None:
                await self._agent.start()
                raise RuntimeError("prepare failed")

            async def _stop_resources(self) -> None:
                self.stop_calls += 1
                if self._agent.is_running:
                    await self._agent.stop()

            async def _serve_transport(self) -> None:
                return None

        gateway = BoomGateway(agent)
        with pytest.raises(RuntimeError, match="prepare failed"):
            await gateway.start()

        assert gateway.state == "stopped"
        assert gateway.stop_calls == 1
        assert agent.is_running is False
        # Claim released for another owner.
        other = FakeGateway(agent)
        await other.start()
        assert other.state == "started"
        await other.stop()

    @pytest.mark.asyncio
    async def test_concurrent_serve_raises(self) -> None:
        agent = make_agent()
        gateway = FakeGateway(agent)
        gateway.serve_blocks = asyncio.Event()
        await gateway.start()

        task = asyncio.create_task(gateway.serve())
        await asyncio.sleep(0)
        with pytest.raises(LifecycleError, match="already in progress"):
            await gateway.serve()

        gateway.serve_blocks.set()
        await task
        await gateway.stop()


class TestLifecycleHelpers:
    def test_require_adapter_ok(self) -> None:
        from band.core.gateways import require_adapter

        class Wanted:
            pass

        wanted = Wanted()
        agent = make_agent()
        agent._adapter = wanted  # type: ignore[attr-defined]
        assert require_adapter(agent, Wanted, gateway="G") is wanted

    def test_require_adapter_rejects(self) -> None:
        from band.core.gateways import require_adapter

        class Wanted:
            pass

        with pytest.raises(TypeError, match="G requires Wanted"):
            require_adapter(make_agent(), Wanted, gateway="G")

    def test_override_attribute_restores(self) -> None:
        from band.core.gateways import override_attribute

        class Obj:
            flag = True

        obj = Obj()
        with override_attribute(obj, "flag", False):
            assert obj.flag is False
        assert obj.flag is True

    @pytest.mark.asyncio
    async def test_override_via_gateway_stack_restores_on_stop(self) -> None:
        class Obj:
            flag = True

        obj = Obj()
        agent = make_agent()

        class G(FakeGateway):
            async def _start_resources(self) -> None:
                self._override(obj, "flag", False)
                await super()._start_resources()

        gateway = G(agent)
        await gateway.start()
        assert obj.flag is False
        await gateway.stop()
        assert obj.flag is True


class TestGatewayBaseAdapterGeneric:
    def test_missing_hook_fails_at_construction(self) -> None:
        class Incomplete(GatewayBase[object]):
            async def _start_resources(self) -> None:
                return None

            async def _stop_resources(self) -> None:
                return None

            # missing _serve_transport

        with pytest.raises(TypeError, match="abstract"):
            Incomplete(make_agent())

    def test_unparameterized_subclass_rejected(self) -> None:
        with pytest.raises(TypeError, match=r"GatewayBase\[SomeAdapter\]"):

            class Bare(GatewayBase):  # type: ignore[type-arg]
                async def _start_resources(self) -> None:
                    return None

                async def _stop_resources(self) -> None:
                    return None

                async def _serve_transport(self) -> None:
                    return None

    def test_wrong_adapter_names_gateway(self) -> None:
        class Wanted:
            pass

        class NamedGateway(GatewayBase[Wanted]):
            async def _start_resources(self) -> None:
                return None

            async def _stop_resources(self) -> None:
                return None

            async def _serve_transport(self) -> None:
                return None

        with pytest.raises(TypeError, match="NamedGateway requires Wanted"):
            NamedGateway(make_agent()).adapter


class TestStopAll:
    @pytest.mark.asyncio
    async def test_every_step_runs_even_after_one_fails(self) -> None:
        """A transport that will not close must not take the agent with it."""
        ran: list[str] = []

        async def close_transport() -> None:
            ran.append("transport")
            raise RuntimeError("socket stuck")

        async def stop_agent() -> None:
            ran.append("agent")

        with pytest.raises(RuntimeError, match="socket stuck"):
            await stop_all((close_transport, stop_agent), "stop failed")

        assert ran == ["transport", "agent"]

    @pytest.mark.asyncio
    async def test_a_cancelled_step_stays_a_cancellation(self) -> None:
        """Teardown under ``asyncio.timeout`` must still time out.

        Folding the cancellation into an exception group consumes it: the
        timeout no longer recognises its own cancel, and the caller gets an
        opaque group instead of TimeoutError.
        """
        ran: list[str] = []

        async def stop_agent() -> None:
            ran.append("agent")
            await asyncio.sleep(30)  # the timeout cancels here

        async def close_adapter() -> None:
            ran.append("adapter")
            raise RuntimeError("also broken")

        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.01):
                await stop_all((stop_agent, close_adapter), "stop failed")

        assert ran == ["agent", "adapter"], "the cancel must not skip the rest"
