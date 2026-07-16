"""Path rules: customer-configurable paths are resolved and fenced.

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
    """Validated project, entrypoint, and sandbox-owned runtime paths.

    Field names match ``ResolvedLaunch``'s so values flow through by name.
    """

    project: Path
    entrypoint: Path
    environment_path: Path
    state_path: Path
    cache_path: Path
    log_path: Path


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
        workspace, config.project.path, name="project path", phase="paths"
    )
    if not project.is_dir():
        raise LaunchError("paths", f"project path is not a directory: {project}")

    entrypoint = resolve_inside(
        project, config.agent.entrypoint, name="entrypoint", phase="paths"
    )
    if not entrypoint.is_file():
        raise LaunchError(
            "paths", f"entrypoint is not a file inside the project: {entrypoint}"
        )

    # `or DEFAULT_SDK_HOME`: a present-but-empty BAND_SDK_HOME must not
    # collapse the SDK-home fence to the current directory (Path("") == ".").
    sdk_home = Path(env.band_sdk_home or DEFAULT_SDK_HOME)
    forbidden = [(workspace, "the mounted workspace"), (sdk_home, "the SDK home")]
    runtime = {
        field: require_outside(Path(configured), forbidden=forbidden, name=name)
        for field, configured, name in (
            (
                "environment_path",
                config.runtime.environment_path,
                "runtime.environmentPath",
            ),
            ("state_path", config.runtime.state_path, "runtime.statePath"),
            ("cache_path", config.runtime.cache_path, "runtime.cachePath"),
            ("log_path", config.runtime.log_path, "runtime.logPath"),
        )
    }
    return ResolvedPaths(project=project, entrypoint=entrypoint, **runtime)
