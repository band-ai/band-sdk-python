"""Enabling batching with a host that cannot run a batch fails loudly at start."""

from __future__ import annotations

from typing import Any

import pytest

from band.agent import Agent
from band.core.exceptions import BandConfigError
from band.core.simple_adapter import SimpleAdapter
from band.runtime.types import SessionConfig
from tests.core.agentlifecycle.kit import make_runtime


class LegacyAdapter(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None: ...


class BatchAdapter(LegacyAdapter):
    supports_batch_input = True


class LegacyPreprocessor:
    async def process(self, *args: Any, **kwargs: Any) -> None:
        return None


def batching_agent(adapter: SimpleAdapter[Any], **kwargs: Any) -> Agent:
    runtime = make_runtime()
    runtime.session_config = SessionConfig(consume_all_queued=True)
    return Agent(runtime=runtime, adapter=adapter, **kwargs)


class TestBatchHosts:
    @pytest.mark.asyncio
    async def test_an_adapter_that_never_declared_batch_support_is_refused(
        self,
    ) -> None:
        with pytest.raises(BandConfigError, match="LegacyAdapter"):
            await batching_agent(LegacyAdapter()).start()

    @pytest.mark.asyncio
    async def test_a_preprocessor_without_process_batch_is_refused(self) -> None:
        agent = batching_agent(BatchAdapter(), preprocessor=LegacyPreprocessor())

        with pytest.raises(BandConfigError, match="LegacyPreprocessor"):
            await agent.start()

    @pytest.mark.asyncio
    async def test_a_legacy_adapter_still_starts_when_batching_is_off(self) -> None:
        agent = Agent(runtime=make_runtime(), adapter=LegacyAdapter())

        await agent.start()

        assert agent.is_running
