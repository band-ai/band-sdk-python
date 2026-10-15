"""Controlled Windows spawn boundaries without native job-object calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio

from band.integrations.codex import stdio_client, winspawn
from tests.integrations.codex.lifecycle import WindowsLifecycle


@pytest_asyncio.fixture(loop_scope="function")
async def windows_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[WindowsLifecycle]:
    peer = WindowsLifecycle()
    monkeypatch.setattr(stdio_client, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(winspawn, "create_job", peer.create_job)
    monkeypatch.setattr(winspawn, "_os_handle", lambda fd: fd)
    monkeypatch.setattr(winspawn, "_launch_helper", peer.launch)
    monkeypatch.setattr(winspawn, "_read_status", peer.read_status)
    monkeypatch.setattr(
        peer.client, "_windows_command", lambda _job, _handle: ["unused"]
    )
    try:
        yield peer
    finally:
        peer.creation_allowed.set()
        peer.helper.exits_on_kill = True
        peer.job.drains = True
        await peer.client.close()
