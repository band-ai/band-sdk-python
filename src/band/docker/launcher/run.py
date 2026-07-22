"""Launch assembly: resolve everything, then exec the customer entrypoint.

`resolve_launch` performs every check that can fail fast (identity, config,
paths, credentials) and returns a fully validated `ResolvedLaunch`.
`execute` then does the side-effect work — the locked dependency sync — and
finally replaces this process with the customer entrypoint via `os.execve`,
so signals (e.g. SIGTERM from `sbx stop`) reach customer code directly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from band.docker.launcher.config import (
    AGENT_HOME,
    DEFAULT_CONFIG_FILENAME,
    LauncherEnv,
    load_workspace_config,
    resolve_agent_id,
    resolve_endpoints,
)
from band.docker.launcher.bootstrap import bootstrap_repository
from band.docker.launcher.credentials import resolve_credentials
from band.docker.launcher.errors import LaunchError
from band.docker.launcher.launch import ResolvedLaunch
from band.docker.launcher.paths import require_project_materialized, resolve_paths
from band.docker.launcher.sync import sync_customer_environment

logger = logging.getLogger(__name__)

AGENT_UID = 1000


def current_uid() -> int:
    """Seam for tests: the uid the launcher believes it runs as."""
    return os.getuid()


def require_agent_uid() -> None:
    """The launcher must run after the base entrypoint's privilege drop."""
    uid = current_uid()
    if uid != AGENT_UID:
        raise LaunchError(
            "identity",
            f"launcher must run as uid {AGENT_UID} (after the base entrypoint's "
            f"privilege drop), got uid {uid}",
        )


def resolve_workspace(env: LauncherEnv) -> Path:
    """The sandbox runtime's mounted workspace, verified to exist."""
    if not env.workspace_dir:
        raise LaunchError(
            "config", "WORKSPACE_DIR is not set — is this a Docker Sandbox?"
        )
    workspace = Path(env.workspace_dir).resolve()
    if not workspace.is_dir():
        raise LaunchError("config", f"workspace does not exist: {workspace}")
    return workspace


def locate_config_path(env: LauncherEnv, workspace: Path) -> Path:
    """Honor the one supported path override, else the workspace default."""
    if env.band_kit_config_path:
        return Path(env.band_kit_config_path)
    return workspace / DEFAULT_CONFIG_FILENAME


def require_band_api_key(credentials: dict[str, str]) -> None:
    """The one credential the agent cannot start without."""
    if not credentials.get("BAND_API_KEY"):
        raise LaunchError(
            "credentials",
            "BAND_API_KEY missing: set it in the environment or provide it "
            "via the configured workspace env file",
        )


def resolve_uv_binary(env: LauncherEnv) -> Path:
    """The image's pinned uv, required by the image contract."""
    if not env.band_sdk_uv:
        raise LaunchError("sync", "BAND_SDK_UV is not set — image contract broken")
    return Path(env.band_sdk_uv)


def resolve_launch(env: LauncherEnv | None = None) -> ResolvedLaunch:
    """Fail-fast phases: identity, config, endpoints, paths, credentials."""
    env = env or LauncherEnv()

    require_agent_uid()
    workspace = resolve_workspace(env)
    config = load_workspace_config(locate_config_path(env, workspace))

    rest_url, ws_url = resolve_endpoints(config, env)
    agent_id = resolve_agent_id(config, env)
    paths = resolve_paths(config, env, workspace)
    credentials = resolve_credentials(config, env, workspace)
    require_band_api_key(credentials)

    return ResolvedLaunch(
        workspace=workspace,
        **paths._asdict(),
        uv_binary=resolve_uv_binary(env),
        agent_id=agent_id,
        rest_url=rest_url,
        ws_url=ws_url,
        credentials=credentials,
        repo=config.repo,
    )


def build_child_environment(launch: ResolvedLaunch) -> dict[str, str]:
    """Construct the exact child environment. Never logged."""
    child = dict(os.environ)
    # Canonical names, whatever casing or source validation accepted.
    child.update(launch.credentials)
    child["BAND_AGENT_ID"] = launch.agent_id
    child["BAND_REST_URL"] = launch.rest_url
    child["BAND_WS_URL"] = launch.ws_url
    # The startup chain inherits root's HOME across the setpriv drop.
    child["HOME"] = AGENT_HOME
    return child


def execute(launch: ResolvedLaunch) -> None:
    """Side-effect phases: repository bootstrap, the locked sync, the exec."""
    bootstrap_repository(launch)
    # With a repo section the project only exists after bootstrap, so its
    # existence check runs here instead of at resolve time.
    require_project_materialized(launch.project, launch.entrypoint)
    sync_customer_environment(launch)

    interpreter = launch.environment_path / "bin" / "python"
    if not interpreter.is_file():
        raise LaunchError(
            "exec", f"customer interpreter missing after sync: {interpreter}"
        )

    child_env = build_child_environment(launch)
    os.chdir(launch.project)
    logger.info(
        "Launching customer entrypoint %s with %s", launch.entrypoint, interpreter
    )
    os.execve(str(interpreter), [str(interpreter), str(launch.entrypoint)], child_env)


def launcher_formatter() -> logging.Formatter:
    """The one shape of every launcher diagnostic line, on every handler:
    str.format-style fields with ISO 8601 timestamps."""
    return logging.Formatter(
        "{asctime} {name} {levelname} {message}",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        style="{",
    )


def configure_logging() -> None:
    """Stream diagnostics to stderr (the startup dispatcher's log)."""
    handler = logging.StreamHandler()
    handler.setFormatter(launcher_formatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def add_log_file(log_path: Path) -> None:
    """Mirror diagnostics into the configured runtime log directory."""
    log_path.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path / "launcher.log")
    handler.setFormatter(launcher_formatter())
    logging.getLogger().addHandler(handler)


def main() -> None:
    configure_logging()
    # Correct the process-wide HOME once, at the boundary: the startup chain
    # inherits root's HOME across the setpriv drop, and everything below —
    # git subprocesses (credentials check, repo_init clone), uv, the customer
    # process — must see the agent user's home. The explicit HOME entries in
    # the sync/child environments stay as the contract for library callers
    # that bypass main().
    os.environ["HOME"] = AGENT_HOME
    try:
        launch = resolve_launch()
        add_log_file(launch.log_path)
        execute(launch)
    except LaunchError as exc:
        logger.error("Launch failed: %s", exc)
        raise SystemExit(1) from exc
    except OSError as exc:
        # Filesystem/exec errors outside a named phase (e.g. an unwritable
        # log directory, ENOEXEC from the customer interpreter) — still a
        # clean diagnostic, never a raw traceback.
        logger.error("Launch failed: %s", exc)
        raise SystemExit(1) from exc
