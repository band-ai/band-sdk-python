"""Repository bootstrap: fenced destination, error wrapping, and ordering."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from band.docker import repo_init
from band.docker.launcher import bootstrap as launcher_bootstrap
from band.docker.launcher import run as launcher_run
from band.docker.launcher import (
    LaunchError,
    bootstrap_repository,
    execute,
    resolve_launch,
)
from band.docker.launcher.bootstrap import REPO_LOCK_TIMEOUT_S

from .fakes import Workspace, default_config, enable_repo, make_env, write_config

REPO_URL = "https://github.com/example/agent-project.git"


def enable(workspace: Workspace, **fields: Any) -> None:
    write_config(workspace, enable_repo(default_config(workspace), **fields))


def materialize_project(path: Path) -> None:
    """What a successful clone leaves behind: the project the launch needs."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.py").write_text("print('agent')\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "cloned"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (path / "uv.lock").write_text("# lock\n", encoding="utf-8")


def test_bootstrap_passes_fenced_destination_and_runtime_dirs(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone destination is the fenced project path — never configured —
    and state/context live under the sandbox-owned runtime state path."""
    enable(workspace, branch="main", index=True)
    captured: dict[str, Any] = {}

    def fake_init(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        captured["config"] = config
        captured["kwargs"] = kwargs
        materialize_project(Path(config["repo"]["path"]))
        return repo_init.RepoInitResult(enabled=True, cloned=True, indexed=True)

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", fake_init)
    launch = resolve_launch(make_env(workspace))
    bootstrap_repository(launch)

    assert captured["config"] == {
        "repo": {
            "url": REPO_URL,
            "path": str(workspace.root.resolve() / "app"),
            "branch": "main",
            "index": True,
        }
    }
    assert captured["kwargs"] == {
        "agent_key": "agent-123",
        "state_dir": launch.state_path,
        "context_dir": launch.state_path / "context",
        "lock_timeout_s": REPO_LOCK_TIMEOUT_S,
    }


def test_no_repo_section_skips_bootstrap(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        raise AssertionError("initialize_repo must not run without a repo section")

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", never)
    bootstrap_repository(resolve_launch(make_env(workspace)))


def test_repo_init_failure_becomes_repo_phase_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every repo_init ValueError (config, auth, git, lock) fails the launch
    attributed to its phase, never as a raw traceback."""
    enable(workspace)

    def failing(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        raise ValueError("git clone failed: remote hung up")

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", failing)
    launch = resolve_launch(make_env(workspace))
    with pytest.raises(LaunchError, match=r"\[repo\].*remote hung up"):
        bootstrap_repository(launch)


def test_launch_error_passes_through_unwrapped(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LaunchError subclasses ValueError — it must keep its own phase."""
    enable(workspace)

    def failing(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        raise LaunchError("paths", "already attributed")

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", failing)
    launch = resolve_launch(make_env(workspace))
    with pytest.raises(LaunchError, match=r"^\[paths\] already attributed$"):
        bootstrap_repository(launch)


def test_bootstrap_runs_before_sync_then_launch_execs(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty-workspace flow: only band.yaml at the root, the project arrives
    with the clone, then sync and exec proceed against it."""
    enable(workspace)
    order: list[str] = []

    def fake_init(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        order.append("bootstrap")
        materialize_project(Path(config["repo"]["path"]))
        return repo_init.RepoInitResult(enabled=True, cloned=True)

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", fake_init)
    monkeypatch.setattr(
        launcher_run, "sync_customer_environment", lambda launch: order.append("sync")
    )
    monkeypatch.setattr("os.execve", lambda *args: order.append("exec"))
    monkeypatch.setattr("os.chdir", lambda path: None)

    launch = resolve_launch(make_env(workspace))
    interpreter = launch.environment_path / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")

    execute(launch)
    assert order == ["bootstrap", "sync", "exec"]


def test_unmaterialized_clone_fails_before_sync(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If bootstrap reports success but the project is not on disk, the
    launch fails at the paths check instead of deep inside the sync."""
    enable(workspace)

    def hollow(config: dict[str, Any], **kwargs: Any) -> repo_init.RepoInitResult:
        return repo_init.RepoInitResult(enabled=True, cloned=True)

    monkeypatch.setattr(launcher_bootstrap, "initialize_repo", hollow)
    launch = resolve_launch(make_env(workspace))
    with pytest.raises(LaunchError, match=r"\[paths\].*not a directory"):
        execute(launch)


def test_existing_repo_reused_without_clone(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart flow with the real repo_init: an existing checkout with the
    configured remote is validated and reused, never re-cloned. HTTPS
    preflight is a no-op and validation only reads local git state, so no
    network is touched."""
    enable(workspace)
    app = workspace.root / "app"
    materialize_project(app)
    identity = ["-c", "user.email=test@example.test", "-c", "user.name=Test"]
    for command in (
        ["git", "init", "-q", str(app)],
        ["git", "-C", str(app), "remote", "add", "origin", REPO_URL],
        ["git", "-C", str(app), "add", "."],
        ["git", "-C", str(app), *identity, "commit", "-q", "-m", "init"],
    ):
        subprocess.run(command, check=True, capture_output=True)

    def never_clone(repo: Any, path: Path) -> None:
        raise AssertionError("an existing checkout must not be re-cloned")

    monkeypatch.setattr(repo_init, "_clone_repo", never_clone)
    launch = resolve_launch(make_env(workspace))
    bootstrap_repository(launch)
    assert (launch.state_path / "repo_init_meta.json").is_file()
