"""Owned resources for native adapter activity qualification."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.e2e.baseline.toolkit.agnowork import AgnoWorkerWork
from tests.example_agents.anthropic.peer import shared_api

if TYPE_CHECKING:
    from tests.integrations.omprpc.gate import ProcessGate


@pytest.fixture
async def agno_worker_work() -> AsyncIterator[AgnoWorkerWork]:
    async with shared_api() as (_, base_url, _):
        with httpx.Client() as client:
            work = AgnoWorkerWork(client, base_url)
            async with work.releasing_on_exit():
                yield work


@pytest.fixture
async def native_process_gate() -> AsyncIterator[ProcessGate]:
    from tests.integrations.omprpc.gate import process_gate_session  # noqa: PLC0415 -- psutil belongs to the backend extra

    async with process_gate_session() as gate:
        yield gate
