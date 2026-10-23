from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bandpap.service import application

from tests.pap.servicekit import Bridge, Gateway, Participant


class TestPAPService:
    @pytest.mark.asyncio
    async def test_shutdown_cancels_pumps_before_participants_and_host_resources(
        self,
    ) -> None:
        events = []
        gateway, agent, bridge = Gateway(events), Participant(events), Bridge(events)

        async def cleanup() -> None:
            events.append("host-close")

        app = application(
            gateway, agents=[agent], bridges={"proxy": bridge}, cleanup=cleanup
        )
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(agent.started.wait(), 1)
            await asyncio.wait_for(bridge.started.wait(), 1)
        assert events.index("bridge-cancelled") < events.index("agent-stop")
        assert events[-3:] == ["agent-stop", "gateway-stop", "host-close"]

    @pytest.mark.asyncio
    async def test_participant_failure_cancels_sibling_and_closes_host_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = []
        gateway, agent, bridge = Gateway(events), Participant(events), Bridge(events)
        fail = asyncio.Event()

        async def stopped(*, install_signal_handlers: Any) -> Any:
            await bridge.started.wait()
            await fail.wait()

        async def cleanup() -> None:
            events.append("host-close")

        monkeypatch.setattr(agent, "run_forever", stopped)
        app = application(
            gateway, agents=[agent], bridges={"proxy": bridge}, cleanup=cleanup
        )
        with pytest.raises(ExceptionGroup, match="TaskGroup"):
            async with app.router.lifespan_context(app):
                await asyncio.wait_for(bridge.started.wait(), 1)
                fail.set()
                await asyncio.Event().wait()
        assert "bridge-cancelled" in events
        assert events[-3:] == ["agent-stop", "gateway-stop", "host-close"]
