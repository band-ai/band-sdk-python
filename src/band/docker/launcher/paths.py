"""Path rules: every customer-configurable path is resolved and fenced.

Workspace-relative paths (project, entrypoint, credential file) must stay
inside their permitted root after resolving symlinks — traversal and link
escapes fail the launch. Sandbox-owned runtime paths (venv, state, cache,
logs) must be absolute and live *outside* both the mounted workspace (a
direct mount is the host directory) and the immutable SDK home.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from band.docker.launcher.config import DEFAULT_SDK_HOME, LauncherEnv, WorkspaceConfig
from band.docker.launcher.errors import LaunchError


class ResolvedPaths(NamedTuple):
    """Validated project, entrypoint, and sandbox-owned runtime paths."""

    project: Path
    entrypoint: Path
    environment: Path
    state: Path
    cache: Path
    log: Path


def resolve_inside(base: Path, value: str, *, name: str, phase: str) -> Path:
    """Resolve ``value`` against ``base`` and require the result stays inside."""
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise LaunchError(phase, f"{name} escapes its permitted root {base}: {value!r}")
    return resolved


def require_outside(
    path: Path, *, forbidden: list[tuple[Path, str]], name: str
) -> Path:
    """Require an absolute path that lives outside every forbidden root."""
    if not path.is_absolute():
        raise LaunchError("paths", f"{name} must be an absolute path: {path}")
    resolved = path.resolve()
    for root, label in forbidden:
        if resolved.is_relative_to(root.resolve()):
            raise LaunchError("paths", f"{name} must live outside {label}: {resolved}")
    return resolved


def resolve_paths(
    config: WorkspaceConfig, env: LauncherEnv, workspace: Path
) -> ResolvedPaths:
    """Resolve and validate project, entrypoint, and runtime paths."""
    project = resolve_inside(
        workspace,
        env.band_kit_project_path or config.project.path,
        name="project path",
        phase="paths",
    )
    if not project.is_dir():
        raise LaunchError("paths", f"project path is not a directory: {project}")

    entrypoint = resolve_inside(
        project,
        env.band_kit_entrypoint_path or config.agent.entrypoint,
        name="entrypoint",
        phase="paths",
    )
    if not entrypoint.is_file():
        raise LaunchError(
            "paths", f"entrypoint is not a file inside the project: {entrypoint}"
        )

    # `or DEFAULT_SDK_HOME`: a present-but-empty BAND_SDK_HOME must not
    # collapse the SDK-home fence to the current directory (Path("") == ".").
    sdk_home = Path(env.band_sdk_home or DEFAULT_SDK_HOME)
    forbidden = [(workspace, "the mounted workspace"), (sdk_home, "the SDK home")]
    environment_path = require_outside(
        Path(env.band_kit_environment_path or config.runtime.environment_path),
        forbidden=forbidden,
        name="runtime.environmentPath",
    )
    state_path = require_outside(
        Path(env.band_kit_state_path or config.runtime.state_path),
        forbidden=forbidden,
        name="runtime.statePath",
    )
    cache_path = require_outside(
        Path(env.band_kit_cache_path or config.runtime.cache_path),
        forbidden=forbidden,
        name="runtime.cachePath",
    )
    log_path = require_outside(
        Path(env.band_kit_log_path or config.runtime.log_path),
        forbidden=forbidden,
        name="runtime.logPath",
    )
    return ResolvedPaths(
        project=project,
        entrypoint=entrypoint,
        environment=environment_path,
        state=state_path,
        cache=cache_path,
        log=log_path,
    )
