"""Residency hosts that release every barrier and stop on teardown."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from tests.core.retention.kit import SETTLE_GRACE_S, ResidenceHost


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


@pytest_asyncio.fixture(loop_scope="function")
async def graceful_host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost(timeout=None, settle_grace=SETTLE_GRACE_S)
    try:
        yield host
    finally:
        await host.close()


@pytest_asyncio.fixture(loop_scope="function")
async def graceful_impatient_host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost(
        timeout=None, transition_timeout=0.05, settle_grace=SETTLE_GRACE_S
    )
    try:
        yield host
    finally:
        await host.close()
