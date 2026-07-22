"""The resolved launch: the one data model every launcher phase works on.

Produced by `run.resolve_launch` and consumed by `sync` and the exec —
kept in its own module so producer and consumers share it without a cycle.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from band.docker.launcher.config import RepoSection


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
    # Canonical credential name -> value for the child environment: process
    # environment first, the opt-in workspace file filling gaps. Never logged.
    credentials: dict[str, str] = {}
    # Optional repository bootstrap; the clone destination is always the
    # fenced project path above, never configured separately.
    repo: RepoSection | None = None
