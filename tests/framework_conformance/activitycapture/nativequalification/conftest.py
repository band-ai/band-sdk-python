"""Owned loopback exchange and child commands for native qualification helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.e2e.baseline.toolkit.resultexchange import ResultExchange
from tests.framework_conformance.activitycapture.nativequalification.kit import (
    NativeCommands,
)
from tests.hostshells import SHELL_PROCESS_TIMEOUT_SECONDS


@pytest.fixture
async def exchange() -> AsyncIterator[ResultExchange]:
    async with ResultExchange(
        deadline_s=SHELL_PROCESS_TIMEOUT_SECONDS
    ).running() as running:
        yield running


@pytest.fixture
async def native_commands(exchange: ResultExchange) -> AsyncIterator[NativeCommands]:
    commands = NativeCommands(exchange)
    try:
        yield commands
    finally:
        await commands.close()
