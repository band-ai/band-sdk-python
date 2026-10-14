"""Residency hosts that release every barrier and stop on teardown."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from tests.core.retention.kit import (
    IMPATIENT_TRANSITION_S,
    SETTLE_GRACE_S,
    ResidenceHost,
)


@pytest_asyncio.fixture(loop_scope="function")
async def host() -> AsyncIterator[ResidenceHost]:
    async with ResidenceHost() as host:
        yield host


@pytest_asyncio.fixture(loop_scope="function")
async def impatient_host() -> AsyncIterator[ResidenceHost]:
    async with ResidenceHost(transition_timeout=IMPATIENT_TRANSITION_S) as host:
        yield host


@pytest_asyncio.fixture(loop_scope="function")
async def graceful_host() -> AsyncIterator[ResidenceHost]:
    async with ResidenceHost(timeout=None, settle_grace=SETTLE_GRACE_S) as host:
        yield host


@pytest_asyncio.fixture(loop_scope="function")
async def graceful_impatient_host() -> AsyncIterator[ResidenceHost]:
    async with ResidenceHost(
        timeout=None,
        transition_timeout=IMPATIENT_TRANSITION_S,
        settle_grace=SETTLE_GRACE_S,
    ) as host:
        yield host
