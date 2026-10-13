"""Run the common Room lifecycle suite against every registered provider."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from tests.adapters.lifecycle.kit import (
    PROFILE_MODULE,
    LifecycleProfile,
    RetentionHost,
    hosted,
)
from tests.paths import REPO_ROOT


def registered_profiles() -> dict[str, LifecycleProfile]:
    modules = sorted((REPO_ROOT / "tests").rglob(PROFILE_MODULE))
    profiles = (
        profile
        for path in modules
        for profile in importlib.import_module(
            ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        ).PROFILES
    )
    return {profile.name: profile for profile in profiles}


PROFILES = registered_profiles()


@pytest_asyncio.fixture(params=list(PROFILES), loop_scope="function")
async def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    async with hosted(PROFILES[request.param], tmp_path, monkeypatch) as host:
        yield host
