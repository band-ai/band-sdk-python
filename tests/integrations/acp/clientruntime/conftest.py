"""Fixtures for the ACP runtime tests: real stdio peers and their log observer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest

from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.peer import (
    ExitStage,
    stdio_peer_config,
)
from tests.integrations.acp.clientruntime.concurrency import ConcurrencyProbe
from tests.integrations.acp.clientruntime.kit import (
    RUNTIME_LOGGER,
    InitializationObserver,
    StdioRuntimeFactory,
)
from tests.paths import REPO_ROOT


@pytest.fixture
def probe() -> ConcurrencyProbe:
    return ConcurrencyProbe()


@pytest.fixture
def stdio_runtime() -> StdioRuntimeFactory:
    @asynccontextmanager
    async def build(stage: ExitStage, lines: list[str]) -> AsyncIterator[ACPRuntime]:
        config = stdio_peer_config(stage, lines)
        runtime = ACPRuntime(
            command=list(config.command),
            cwd=str(REPO_ROOT),
            env=config.env,
        )
        try:
            yield runtime
        finally:
            await runtime.stop()

    return build


@pytest.fixture
def initialization_observer() -> Iterator[InitializationObserver]:
    observer = InitializationObserver()
    logger = logging.getLogger(RUNTIME_LOGGER)
    logger.addHandler(observer)
    try:
        yield observer
    finally:
        logger.removeHandler(observer)
