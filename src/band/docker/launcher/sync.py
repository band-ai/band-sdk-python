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
from typing import TYPE_CHECKING

from band.docker.launcher.config import AGENT_HOME
from band.docker.launcher.errors import LaunchError

if TYPE_CHECKING:
    from band.docker.launcher.run import ResolvedLaunch

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_S = 600.0


def sync_customer_environment(launch: ResolvedLaunch) -> None:
    """Run the pinned ``uv sync --locked`` into the customer environment."""
    pyproject = launch.project / "pyproject.toml"
    lockfile = launch.project / "uv.lock"
    if not pyproject.is_file():
        raise LaunchError("sync", f"pyproject.toml not found in {launch.project}")
    if not lockfile.is_file():
        raise LaunchError(
            "sync",
            f"uv.lock not found in {launch.project} — commit a lock "
            "(`uv lock`); unlocked resolution is not supported",
        )
    if not launch.uv_binary.is_file():
        raise LaunchError("sync", f"pinned runtime uv not found at {launch.uv_binary}")

    from filelock import FileLock, Timeout

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
        sync_env = dict(os.environ)
        sync_env["UV_PROJECT_ENVIRONMENT"] = str(launch.environment_path)
        sync_env["UV_CACHE_DIR"] = str(launch.cache_path)
        sync_env["HOME"] = AGENT_HOME
        # Build the customer venv on the image's interpreter and fail clearly
        # if the project's requires-python can't accept it — never let uv try
        # to download a managed interpreter inside the egress-fenced sandbox.
        sync_env["UV_PYTHON"] = sys.executable
        sync_env["UV_PYTHON_DOWNLOADS"] = "never"
        logger.info(
            "Synchronizing locked dependencies into %s", launch.environment_path
        )
        result = subprocess.run(
            [str(launch.uv_binary), "sync", "--locked"],
            cwd=launch.project,
            env=sync_env,
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
    finally:
        lock.release()
