"""Real processes whose exit and pipe closure can be driven independently."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import partial

import pytest
import pytest_asyncio

from tests.core.retention.kit import ManualClock
from tests.integrations.acp.acp_toolkit.pipes import (
    PipeProcessFactory,
    controlled_pipes,
)


@pytest.fixture
def pipe_process(monkeypatch: pytest.MonkeyPatch) -> PipeProcessFactory:
    return partial(controlled_pipes, monkeypatch)


@pytest_asyncio.fixture(loop_scope="function")
async def loop_clock(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ManualClock]:
    """Advance deadlines explicitly while socket readiness remains real I/O."""
    loop = asyncio.get_running_loop()
    clock = ManualClock()
    clock.time = loop.time()
    with monkeypatch.context() as scoped:
        scoped.setattr(loop, "time", clock.now)
        yield clock
