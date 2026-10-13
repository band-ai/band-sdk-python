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
    profile = PROFILES[request.param]
    host = RetentionHost(
        profile=profile,
        root=tmp_path,
        fail_startup=tmp_path / "fail-startup",
        hold_startup=tmp_path / "hold-startup",
    )
    host.bridge = profile.install(host, monkeypatch)
    await host.adapter.on_started("Peer", "A subprocess under test")
    try:
        yield host
    finally:
        host.fail_startup.unlink(missing_ok=True)
        host.hold_startup.unlink(missing_ok=True)
        for child in host.children:
            child.refuse_close = False
            for barrier in (child.starting, child.closing):
                if barrier is not None:
                    barrier.released.set()
        try:
            await host.adapter.cleanup_all()
        finally:
            for child in host.children:
                await child.close()
