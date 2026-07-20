"""Sandbox kit launcher: boots a locked customer Python project as a Band agent.

Runs as `$BAND_SDK_PYTHON -m band.docker.launcher` from the kit's startup
command, after the base image entrypoint has installed the proxy CA and
dropped to the non-root agent user. The flow, one module per concern:

1. `config`    — read `band.yaml` (strict) and the supported env overrides.
2. `paths`     — resolve and fence every configurable path.
3. `credentials` — optionally fill missing keys from the opt-in workspace
   env file, with its safety checks.
4. `bootstrap` — optionally materialize the project from Git into the
   fenced project path (reuses `band.docker.repo_init`).
5. `sync`      — `uv sync --locked` into a sandbox-owned venv, under a lock.
6. `run`       — assemble the above, then `os.execve` the customer
   entrypoint so signals reach customer code directly.

`launch` holds the `ResolvedLaunch` model the phases hand to each other.

Every failure is a :class:`LaunchError` naming its phase; no error, log
line, or diagnostic ever contains secret values.
"""

from __future__ import annotations

from band.docker.launcher.bootstrap import bootstrap_repository
from band.docker.launcher.config import (
    AGENT_HOME,
    DEFAULT_REST_URL,
    DEFAULT_WS_URL,
    LauncherEnv,
    RepoSection,
    WorkspaceConfig,
    load_workspace_config,
)
from band.docker.launcher.credentials import (
    CredentialName,
    load_file_credentials,
    resolve_credentials,
)
from band.docker.launcher.errors import LaunchError
from band.docker.launcher.launch import ResolvedLaunch
from band.docker.launcher.run import (
    AGENT_UID,
    build_child_environment,
    execute,
    main,
    resolve_launch,
)
from band.docker.launcher.sync import sync_customer_environment

__all__ = [
    "AGENT_HOME",
    "AGENT_UID",
    "CredentialName",
    "DEFAULT_REST_URL",
    "DEFAULT_WS_URL",
    "LaunchError",
    "LauncherEnv",
    "RepoSection",
    "ResolvedLaunch",
    "WorkspaceConfig",
    "bootstrap_repository",
    "build_child_environment",
    "execute",
    "load_file_credentials",
    "load_workspace_config",
    "main",
    "resolve_credentials",
    "resolve_launch",
    "sync_customer_environment",
]
