"""Optional repository bootstrap: materialize the project from Git.

Reuses :mod:`band.docker.repo_init` — clone into an empty destination,
validate remote/branch on an existing checkout, all under a file lock, with
optional context indexing. The clone destination is never configured
directly: it is always the resolved project path, which `paths` has already
fenced inside the workspace, so a workspace config cannot direct clone
writes anywhere else in the sandbox. State and context live under the
sandbox-owned runtime state path, never repo_init's `/workspace` defaults.
"""

from __future__ import annotations

import logging

from band.docker.launcher.errors import LaunchError
from band.docker.launcher.launch import ResolvedLaunch
from band.docker.repo_init import initialize_repo

logger = logging.getLogger(__name__)

REPO_LOCK_TIMEOUT_S = 120.0


def bootstrap_repository(launch: ResolvedLaunch) -> None:
    """Clone or validate the configured repository at the project path."""
    repo = launch.repo
    if repo is None:
        return
    config = {
        "repo": {
            "url": repo.url,
            "path": str(launch.project),
            "branch": repo.branch,
            "index": repo.index,
        }
    }
    try:
        result = initialize_repo(
            config,
            agent_key=launch.agent_id,
            state_dir=launch.state_path,
            context_dir=launch.state_path / "context",
            lock_timeout_s=REPO_LOCK_TIMEOUT_S,
        )
    except LaunchError:
        raise
    except ValueError as exc:
        raise LaunchError("repo", str(exc)) from exc
    logger.info(
        "Repository bootstrap at %s: cloned=%s indexed=%s",
        launch.project,
        result.cloned,
        result.indexed,
    )
