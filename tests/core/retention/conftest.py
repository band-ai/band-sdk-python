"""Residency hosts that release every barrier and stop on teardown."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from tests.core.retention.kit import ResidenceHost


@pytest_asyncio.fixture(loop_scope="function")
async def host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost()
    try:
        yield host
    finally:
        await host.close()


@pytest_asyncio.fixture(loop_scope="function")
async def impatient_host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost(transition_timeout=0.05)
    try:
        yield host
    finally:
        await host.close()
