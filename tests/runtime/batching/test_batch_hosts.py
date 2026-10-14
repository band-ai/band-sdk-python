"""Enabling batching with a host that cannot run a batch fails loudly at start."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.core.exceptions import BandConfigError
from tests.core.agentlifecycle.kit import make_runtime
from tests.runtime.batching.kit import (
    BatchAdapter,
    LegacyAdapter,
    LegacyPreprocessor,
    batching_agent,
)


class TestBatchHosts:
    async def test_an_adapter_that_never_declared_batch_support_is_refused(
        self,
    ) -> None:
        with pytest.raises(BandConfigError, match="LegacyAdapter"):
            await batching_agent(LegacyAdapter()).start()

    async def test_a_preprocessor_without_process_batch_is_refused(self) -> None:
        agent = batching_agent(BatchAdapter(), preprocessor=LegacyPreprocessor())

        with pytest.raises(BandConfigError, match="LegacyPreprocessor"):
            await agent.start()

    async def test_a_legacy_adapter_still_starts_when_batching_is_off(self) -> None:
        agent = Agent(runtime=make_runtime(), adapter=LegacyAdapter())

        await agent.start()

        assert agent.is_running
