"""Codex-only lifecycle tests on the common suite's host, started on the Codex
profile alone."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tests.adapters.codex.lifecycleprofile import PROFILES, install
from tests.adapters.lifecycle.kit import RetentionHost, hosted


@asynccontextmanager
async def codex_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **settings: Any
) -> AsyncIterator[RetentionHost]:
    """A host on the Codex profile whose adapter config takes ``settings``."""
    (codex,) = PROFILES
    profile = dataclasses.replace(codex, install=partial(install, **settings))
    async with hosted(profile, tmp_path, monkeypatch) as host:
        yield host


@pytest_asyncio.fixture(loop_scope="function")
async def host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    async with codex_host(tmp_path, monkeypatch) as host:
        yield host


# A settle grace short enough that a leave cancels a held start promptly.
BRIEF_SETTLE_GRACE_S = 0.01


@pytest_asyncio.fixture(loop_scope="function")
async def brief_grace_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    async with codex_host(
        tmp_path, monkeypatch, turn_settle_timeout_s=BRIEF_SETTLE_GRACE_S
    ) as host:
        yield host


# Ample for a real peer to close, while a held close always overruns it.
PEER_CLOSE_BOUND_S = 2.0


@pytest_asyncio.fixture(loop_scope="function")
async def close_bound_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    async with codex_host(
        tmp_path, monkeypatch, client_close_timeout_s=PEER_CLOSE_BOUND_S
    ) as host:
        yield host
