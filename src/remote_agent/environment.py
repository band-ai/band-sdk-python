"""Allowlisted child environments; host credentials never propagate by default."""

from __future__ import annotations

from pathlib import Path

from remote_agent.settings import runner_settings


def minimal_environment(
    *, path: str | None = None, pythonpath: str | Path | None = None
) -> dict[str, str]:
    # PATH is trusted executable configuration, not the rest of provider.env.
    environment = {
        "PATH": runner_settings().path if path is None else path,
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }
    if pythonpath is not None:
        environment["PYTHONPATH"] = str(pythonpath)
    return environment
