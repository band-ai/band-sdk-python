"""Launch assembly: resolve everything, then exec the customer entrypoint.

`resolve_launch` performs every check that can fail fast (identity, config,
paths, credentials) and returns a fully validated `ResolvedLaunch`.
`execute` then does the work with side effects — optional repository
initialization, the locked dependency sync — and finally replaces this
process with the customer entrypoint via `os.execve`, so signals (e.g.
SIGTERM from `sbx stop`) reach customer code directly.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from band.docker.launcher.config import (
    AGENT_HOME,
    DEFAULT_CONFIG_FILENAME,
    LauncherEnv,
    load_workspace_config,
    resolve_agent_id,
    resolve_endpoints,
)
from band.docker.launcher.credentials import load_file_credentials
from band.docker.launcher.errors import LaunchError
from band.docker.launcher.paths import resolve_paths
from band.docker.launcher.sync import sync_customer_environment
from band.docker.repo_init import initialize_repo

logger = logging.getLogger(__name__)

AGENT_UID = 1000


class ResolvedLaunch(BaseModel):
    """Everything the launch needs, fully resolved and validated."""

    model_config = ConfigDict(extra="forbid")

    workspace: Path
    project: Path
    entrypoint: Path
    environment_path: Path
    state_path: Path
    cache_path: Path
    log_path: Path
    uv_binary: Path
    agent_id: str
    rest_url: str
    ws_url: str
    repo_config: dict[str, object] | None = None
    # Name -> value for credentials resolved from the opt-in file. Never
    # logged; merged into the child environment only.
    file_credentials: dict[str, str] = {}


def current_uid() -> int:
    """Seam for tests: the uid the launcher believes it runs as."""
    return os.getuid()


def resolve_launch(env: LauncherEnv | None = None) -> ResolvedLaunch:
    """Fail-fast phases: identity, config, endpoints, paths, credentials."""
    env = env or LauncherEnv()

    uid = current_uid()
    if uid != AGENT_UID:
        raise LaunchError(
            "identity",
            f"launcher must run as uid {AGENT_UID} (after the base entrypoint's "
            f"privilege drop), got uid {uid}",
        )

    if not env.workspace_dir:
        raise LaunchError(
            "config", "WORKSPACE_DIR is not set — is this a Docker Sandbox?"
        )
    workspace = Path(env.workspace_dir).resolve()
    if not workspace.is_dir():
        raise LaunchError("config", f"workspace does not exist: {workspace}")

    config_path = (
        Path(env.band_kit_config_path)
        if env.band_kit_config_path
        else workspace / DEFAULT_CONFIG_FILENAME
    )
    config = load_workspace_config(config_path)

    rest_url, ws_url = resolve_endpoints(config, env)
    agent_id = resolve_agent_id(config, env)
    paths = resolve_paths(config, env, workspace)
    file_credentials = load_file_credentials(config, env, workspace)

    api_key = env.band_api_key or file_credentials.get("BAND_API_KEY", "")
    if not api_key:
        raise LaunchError(
            "credentials",
            "BAND_API_KEY missing: set it in the environment or provide it "
            "via the configured workspace env file",
        )

    if not env.band_sdk_uv:
        raise LaunchError("sync", "BAND_SDK_UV is not set — image contract broken")

    repo_config: dict[str, object] | None = None
    if config.repo is not None:
        repo = config.repo.model_dump()
        if env.band_kit_repository_path:
            repo["path"] = env.band_kit_repository_path
        repo_config = repo

    return ResolvedLaunch(
        workspace=workspace,
        project=paths.project,
        entrypoint=paths.entrypoint,
        environment_path=paths.environment,
        state_path=paths.state,
        cache_path=paths.cache,
        log_path=paths.log,
        uv_binary=Path(env.band_sdk_uv),
        agent_id=agent_id,
        rest_url=rest_url,
        ws_url=ws_url,
        repo_config=repo_config,
        file_credentials=file_credentials,
    )


def build_child_environment(launch: ResolvedLaunch) -> dict[str, str]:
    """Construct the exact child environment. Never logged."""
    child = dict(os.environ)
    child.update(launch.file_credentials)
    child["BAND_AGENT_ID"] = launch.agent_id
    child["BAND_REST_URL"] = launch.rest_url
    child["BAND_WS_URL"] = launch.ws_url
    # The startup chain inherits root's HOME across the setpriv drop.
    child["HOME"] = AGENT_HOME
    return child


def execute(launch: ResolvedLaunch) -> None:
    """Side-effect phases: repo init, locked sync, and the final exec."""
    if launch.repo_config is not None:
        try:
            initialize_repo(
                {"repo": launch.repo_config},
                agent_key=launch.agent_id,
                state_dir=launch.state_path,
                context_dir=launch.state_path / "context",
            )
        except ValueError as exc:
            raise LaunchError("repo-init", str(exc)) from exc

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


def configure_logging(log_path: Path | None = None) -> None:
    """Stream diagnostics to stderr and, when available, the runtime log dir."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_path is not None:
        log_path.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path / "launcher.log"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> None:
    configure_logging()
    try:
        launch = resolve_launch()
        configure_logging(launch.log_path)
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
