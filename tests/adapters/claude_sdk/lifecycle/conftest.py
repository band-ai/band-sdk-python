"""Claude-only lifecycle tests on the common suite's host, started on the
Claude profile alone."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from tests.adapters.claude_sdk.lifecycleprofile import PROFILES
from tests.adapters.lifecycle.kit import RetentionHost, hosted


@pytest_asyncio.fixture(loop_scope="function")
async def peer_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    (claude,) = PROFILES
    async with hosted(claude, tmp_path, monkeypatch) as host:
        yield host
