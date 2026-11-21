from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.adapters.external_worker import ExternalWorkerAdapterConfig
from band.runtime.tools.agent import AgentTools
from tests.adapters.externalworker.kit import ENV_PREFIX, Worker
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over


@pytest.fixture
def rest_peer() -> RestPeer:
    return RestPeer()


@pytest.fixture
async def tools(rest_peer: RestPeer) -> AsyncIterator[AgentTools]:
    async with rest_client_over(rest_peer.answer) as rest:
        yield AgentTools(ROOM_ID, rest, agent_id=AGENT_ID)


@pytest.fixture
def worker(rest_peer: RestPeer) -> Worker:
    return Worker(rest_peer)


@pytest.fixture
def no_worker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own worker settings out of a test."""
    for field in ExternalWorkerAdapterConfig.model_fields:
        monkeypatch.delenv(f"{ENV_PREFIX}{field.upper()}", raising=False)
