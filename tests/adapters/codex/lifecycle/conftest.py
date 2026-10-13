"""Codex-only lifecycle tests on the common suite's host, started on the Codex
profile alone."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from tests.adapters.codex.lifecycleprofile import PROFILES
from tests.adapters.lifecycle.kit import RetentionHost, hosted


@pytest_asyncio.fixture(loop_scope="function")
async def host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    (codex,) = PROFILES
    async with hosted(codex, tmp_path, monkeypatch) as host:
        yield host
