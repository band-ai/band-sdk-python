"""Shared builders for launcher unit tests: a realistic customer workspace."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from band.docker.launcher import LauncherEnv


@dataclass
class Workspace:
    """A disposable customer workspace plus its sandbox-side runtime root."""

    root: Path
    runtime_root: Path
    uv_binary: Path

    @property
    def config_path(self) -> Path:
        return self.root / "band.yaml"


def default_config(workspace: Workspace) -> dict[str, Any]:
    """A fully valid band.yaml payload for ``workspace``."""
    rt = workspace.runtime_root
    return {
        "schemaVersion": "1",
        "agent": {"id": "agent-123", "entrypoint": "main.py"},
        "band": {
            "restUrl": "https://platform.example.test",
            "wsUrl": "wss://platform.example.test/socket",
        },
        "project": {"path": "."},
        "runtime": {
            "environmentPath": str(rt / "venv"),
            "statePath": str(rt / "state"),
            "cachePath": str(rt / "cache"),
            "logPath": str(rt / "logs"),
        },
    }


def write_config(workspace: Workspace, config: dict[str, Any]) -> None:
    workspace.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")


def make_workspace(tmp_path: Path, *, git: bool = True) -> Workspace:
    """Create a valid locked customer project under ``tmp_path``.

    The runtime root and the fake pinned uv binary live outside the
    workspace, matching the sandbox layout the path rules enforce.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("print('agent')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "customer"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text("# lock\n", encoding="utf-8")
    (root / ".gitignore").write_text(".band/\n", encoding="utf-8")

    runtime_root = tmp_path / "band-kit-runtime"
    runtime_root.mkdir()

    uv_binary = tmp_path / "pinned-uv"
    uv_binary.write_text("#!/bin/sh\n", encoding="utf-8")

    workspace = Workspace(root=root, runtime_root=runtime_root, uv_binary=uv_binary)
    write_config(workspace, default_config(workspace))

    if git:
        subprocess.run(
            ["git", "init", "-q", str(root)], check=True, capture_output=True
        )
    return workspace


def make_env(workspace: Workspace, **overrides: str) -> LauncherEnv:
    """A LauncherEnv wired to ``workspace`` with a Band key present."""
    values: dict[str, str] = {
        "workspace_dir": str(workspace.root),
        "band_sdk_uv": str(workspace.uv_binary),
        "band_api_key": "test-band-key",
    }
    values.update(overrides)
    return LauncherEnv(**values)


def write_credentials(workspace: Workspace, content: str, *, mode: int = 0o600) -> Path:
    """Write the opt-in credential file at the conventional example path."""
    cred_dir = workspace.root / ".band"
    cred_dir.mkdir(exist_ok=True)
    cred_path = cred_dir / "secrets.env"
    cred_path.write_text(content, encoding="utf-8")
    cred_path.chmod(mode)
    return cred_path


def enable_repo(
    config: dict[str, Any],
    url: str = "https://github.com/example/agent-project.git",
    **fields: Any,
) -> dict[str, Any]:
    """Add a repo section and point the project at its clone subdirectory."""
    config["repo"] = {"url": url, **fields}
    config["project"] = {"path": "app"}
    return config


def enable_credentials(config: dict[str, Any]) -> dict[str, Any]:
    config["credentials"] = {
        "source": "workspace-env-file",
        "path": ".band/secrets.env",
        "acknowledgePlaintextInSandbox": True,
    }
    return config
