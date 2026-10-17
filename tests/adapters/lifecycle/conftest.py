"""Run the common Room lifecycle suite against every registered provider."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from tests.adapters.lifecycle.kit import (
    RetentionHost,
    hosted,
)
from tests.adapters.lifecycle.profiles import registered_profiles

PROFILES = registered_profiles()


@pytest_asyncio.fixture(params=list(PROFILES), loop_scope="function")
async def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    async with hosted(PROFILES[request.param], tmp_path, monkeypatch) as host:
        yield host
