"""Locked dependency synchronization into the sandbox-owned environment.

Runs the image's pinned `uv` with `sync --locked` against the customer
project — resolution never happens at startup, so a missing or stale
`uv.lock` fails with clear guidance instead of silently drifting. The sync
is serialized with a file lock in sandbox state so a concurrent restart can
never corrupt a half-built environment.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock, Timeout

from band.docker.launcher.config import AGENT_HOME
from band.docker.launcher.errors import LaunchError

if TYPE_CHECKING:
    from band.docker.launcher.run import ResolvedLaunch

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_S = 600.0


def require_locked_project(launch: ResolvedLaunch) -> None:
    """The project must ship a committed lock; the image must ship its uv."""
    if not (launch.project / "pyproject.toml").is_file():
        raise LaunchError("sync", f"pyproject.toml not found in {launch.project}")
    if not (launch.project / "uv.lock").is_file():
        raise LaunchError(
            "sync",
            f"uv.lock not found in {launch.project} — commit a lock "
            "(`uv lock`); unlocked resolution is not supported",
        )
    if not launch.uv_binary.is_file():
        raise LaunchError("sync", f"pinned runtime uv not found at {launch.uv_binary}")


def sync_environment(launch: ResolvedLaunch) -> dict[str, str]:
    """The environment the pinned uv runs under: fully pinned, no downloads."""
    sync_env = dict(os.environ)
    sync_env["UV_PROJECT_ENVIRONMENT"] = str(launch.environment_path)
    sync_env["UV_CACHE_DIR"] = str(launch.cache_path)
    sync_env["HOME"] = AGENT_HOME
    # Build the customer venv on the image's base interpreter (the SDK
    # venv's own base, not the venv python) and fail clearly if the
    # project's requires-python can't accept it — never let uv try to
    # download a managed interpreter inside the egress-fenced sandbox.
    sync_env["UV_PYTHON"] = str(Path(sys.base_prefix) / "bin" / "python3")
    sync_env["UV_PYTHON_DOWNLOADS"] = "never"
    return sync_env


def run_locked_sync(launch: ResolvedLaunch) -> None:
    """Invoke the pinned uv and shape its failure into launch guidance."""
    logger.info("Synchronizing locked dependencies into %s", launch.environment_path)
    result = subprocess.run(
        [str(launch.uv_binary), "sync", "--locked"],
        cwd=launch.project,
        env=sync_environment(launch),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-15:])
        raise LaunchError(
            "sync",
            "locked dependency synchronization failed (is uv.lock up to "
            f"date with pyproject.toml?):\n{tail}",
        )


def sync_customer_environment(launch: ResolvedLaunch) -> None:
    """Run the pinned ``uv sync --locked``, serialized against restarts."""
    require_locked_project(launch)

    launch.state_path.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(launch.state_path / "dependency_sync.lock"))
    try:
        lock.acquire(timeout=LOCK_TIMEOUT_S)
    except Timeout:
        raise LaunchError(
            "sync",
            f"timed out waiting for the dependency-sync lock after "
            f"{LOCK_TIMEOUT_S:.0f}s",
        ) from None
    try:
        run_locked_sync(launch)
    finally:
        lock.release()
