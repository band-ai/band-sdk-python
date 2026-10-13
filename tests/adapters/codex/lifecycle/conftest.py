"""Codex-only lifecycle tests on the common suite's host, started on the Codex
profile alone."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path

import pytest
import pytest_asyncio

from tests.adapters.codex.lifecycleprofile import PROFILES, install
from tests.adapters.lifecycle.kit import RetentionHost, hosted


@pytest_asyncio.fixture(loop_scope="function")
async def host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    (codex,) = PROFILES
    async with hosted(codex, tmp_path, monkeypatch) as host:
        yield host


# A settle grace short enough that a leave cancels a held start promptly.
BRIEF_SETTLE_GRACE_S = 0.01


@pytest_asyncio.fixture(loop_scope="function")
async def brief_grace_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    (codex,) = PROFILES
    brief = dataclasses.replace(
        codex, install=partial(install, turn_settle_timeout_s=BRIEF_SETTLE_GRACE_S)
    )
    async with hosted(brief, tmp_path, monkeypatch) as host:
        yield host
