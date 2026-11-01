"""Owned resources for native adapter activity qualification."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from tests.e2e.baseline.toolkit.agnowork import AgnoWorkerWork
from tests.example_agents.anthropic.peer import shared_api


@pytest.fixture
async def agno_worker_work() -> AsyncIterator[AgnoWorkerWork]:
    async with shared_api() as (_, base_url, _):
        with httpx.Client() as client:
            work = AgnoWorkerWork(client, base_url)
            async with work.releasing_on_exit():
                yield work
