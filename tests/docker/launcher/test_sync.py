"""Locked dependency synchronization: command, environment, lock, failures."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from band.docker.launcher import (
    AGENT_HOME,
    LaunchError,
    resolve_launch,
    sync_customer_environment,
)
from band.docker.launcher import sync as launcher_sync

from .fakes import Workspace, make_env


class FakeRun:
    """Captures the uv invocation; configurable exit code."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout="", stderr=self.stderr
        )


def test_missing_pyproject_rejected(workspace: Workspace) -> None:
    launch = resolve_launch(make_env(workspace))
    (workspace.root / "pyproject.toml").unlink()
    with pytest.raises(LaunchError, match=r"\[sync\].*pyproject.toml"):
        sync_customer_environment(launch)


def test_missing_lock_rejected_with_guidance(workspace: Workspace) -> None:
    launch = resolve_launch(make_env(workspace))
    (workspace.root / "uv.lock").unlink()
    with pytest.raises(LaunchError, match=r"\[sync\].*uv.lock.*not supported"):
        sync_customer_environment(launch)


def test_missing_uv_binary_rejected(workspace: Workspace) -> None:
    launch = resolve_launch(make_env(workspace))
    workspace.uv_binary.unlink()
    with pytest.raises(LaunchError, match=r"\[sync\].*pinned runtime uv"):
        sync_customer_environment(launch)


def test_sync_invocation_exact(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = resolve_launch(make_env(workspace))
    fake = FakeRun()
    monkeypatch.setattr(launcher_sync.subprocess, "run", fake)

    sync_customer_environment(launch)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["cmd"] == [str(workspace.uv_binary), "sync", "--locked"]
    assert call["cwd"] == launch.project
    env = call["env"]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(launch.environment_path)
    assert env["UV_CACHE_DIR"] == str(launch.cache_path)
    assert env["HOME"] == AGENT_HOME
    # The customer venv must build on the base interpreter (never the SDK
    # venv python), and uv must never try to download one inside the
    # egress-fenced sandbox.
    import sys
    from pathlib import Path

    assert env["UV_PYTHON"] == str(Path(sys.base_prefix) / "bin" / "python3")
    assert env["UV_PYTHON_DOWNLOADS"] == "never"


def test_sync_failure_surfaces_stderr_tail(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = resolve_launch(make_env(workspace))
    fake = FakeRun(returncode=2, stderr="error: lockfile is out of date")
    monkeypatch.setattr(launcher_sync.subprocess, "run", fake)
    with pytest.raises(LaunchError, match=r"(?s)\[sync\].*lockfile is out of date"):
        sync_customer_environment(launch)


def test_sync_lock_contention_times_out_cleanly(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held dependency-sync lock surfaces as a clear phase error, and the
    sync command is never attempted."""
    import filelock

    class HeldLock:
        def __init__(self, path: str) -> None:
            self.path = path

        def acquire(self, timeout: float | None = None) -> None:
            raise filelock.Timeout(self.path)

        def release(self) -> None:  # pragma: no cover - never reached
            raise AssertionError("release without acquire")

    launch = resolve_launch(make_env(workspace))
    fake = FakeRun()
    monkeypatch.setattr(launcher_sync.subprocess, "run", fake)
    monkeypatch.setattr(filelock, "FileLock", HeldLock)

    with pytest.raises(LaunchError, match=r"\[sync\].*timed out.*lock"):
        sync_customer_environment(launch)
    assert fake.calls == []
