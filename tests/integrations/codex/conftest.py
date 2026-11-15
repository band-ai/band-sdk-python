"""Controlled Windows spawn boundaries without native job-object calls."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from band.integrations.codex import stdio_client, winspawn
from tests.integrations.codex.job import JobKernel
from tests.integrations.codex.lifecycle import WindowsLifecycle
from tests.integrations.codex.lookup import WindowsLookup


@pytest.fixture
def job_kernel(monkeypatch: pytest.MonkeyPatch) -> JobKernel:
    kernel = JobKernel()
    monkeypatch.setattr(winspawn, "_kernel32", lambda: kernel)
    monkeypatch.setattr(winspawn, "_last_error", lambda: kernel.last_error)
    monkeypatch.setattr(
        winspawn, "_win_os_error", lambda code: OSError(code, "native Job failure")
    )
    return kernel


@pytest.fixture
def windows_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WindowsLookup:
    peer = WindowsLookup(tmp_path)
    monkeypatch.chdir(peer.current)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        winspawn, "os", SimpleNamespace(name="nt", path=os.path, pathsep=os.pathsep)
    )
    monkeypatch.setattr(winspawn, "_kernel32", lambda: peer.kernel)
    return peer


@pytest_asyncio.fixture(loop_scope="function")
async def windows_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[WindowsLifecycle]:
    peer = WindowsLifecycle()
    monkeypatch.setattr(stdio_client, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(winspawn, "create_job", peer.create_job)
    monkeypatch.setattr(winspawn, "_os_handle", lambda fd: fd)
    monkeypatch.setattr(
        winspawn.subprocess, "STARTUPINFO", SimpleNamespace, raising=False
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", peer.launch)
    monkeypatch.setattr(winspawn, "_read_status", peer.read_status)
    try:
        yield peer
    finally:
        peer.creation_allowed.set()
        peer.helper.exits_on_kill = True
        peer.job.drains = True
        await peer.client.close()
