"""Child environment construction, repo-init passthrough, and the final exec."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from band.docker.launcher import run as launcher_run
from band.docker.launcher import (
    AGENT_HOME,
    LaunchError,
    build_child_environment,
    execute,
    resolve_launch,
)

from .fakes import Workspace, default_config, make_env, write_config


@pytest.fixture
def no_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_run, "sync_customer_environment", lambda launch: None)


def make_customer_interpreter(launch_environment_path: Path) -> Path:
    interpreter = launch_environment_path / "bin" / "python"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    return interpreter


def test_child_environment_exact(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNRELATED_VAR", "preserved")
    launch = resolve_launch(make_env(workspace))
    launch.file_credentials["OPENAI_API_KEY"] = "from-file"

    child = build_child_environment(launch)

    assert child["BAND_AGENT_ID"] == "agent-123"
    assert child["BAND_REST_URL"] == "https://platform.example.test"
    assert child["BAND_WS_URL"] == "wss://platform.example.test/socket"
    assert child["HOME"] == AGENT_HOME
    assert child["OPENAI_API_KEY"] == "from-file"
    assert child["UNRELATED_VAR"] == "preserved"


def test_execute_execs_customer_entrypoint(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, no_sync: None
) -> None:
    launch = resolve_launch(make_env(workspace))
    interpreter = make_customer_interpreter(launch.environment_path)

    captured: dict[str, Any] = {}

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        captured["path"] = path
        captured["argv"] = argv
        captured["env"] = env

    def fake_chdir(path: str | Path) -> None:
        captured["cwd"] = Path(path)

    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setattr(os, "chdir", fake_chdir)

    execute(launch)

    assert captured["path"] == str(interpreter)
    assert captured["argv"] == [str(interpreter), str(launch.entrypoint)]
    assert captured["cwd"] == launch.project
    assert captured["env"]["BAND_AGENT_ID"] == "agent-123"


def test_missing_customer_interpreter_rejected(
    workspace: Workspace, no_sync: None
) -> None:
    launch = resolve_launch(make_env(workspace))
    with pytest.raises(LaunchError, match=r"\[exec\].*interpreter"):
        execute(launch)


def test_repo_init_receives_configured_paths(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, no_sync: None
) -> None:
    config = default_config(workspace)
    config["repo"] = {"path": str(workspace.root), "index": False}
    write_config(workspace, config)
    launch = resolve_launch(make_env(workspace))
    make_customer_interpreter(launch.environment_path)

    captured: dict[str, Any] = {}

    def fake_initialize_repo(config: dict[str, Any], **kwargs: Any) -> None:
        captured["config"] = config
        captured.update(kwargs)

    monkeypatch.setattr(launcher_run, "initialize_repo", fake_initialize_repo)
    monkeypatch.setattr(os, "execve", lambda *a: None)
    monkeypatch.setattr(os, "chdir", lambda p: None)

    execute(launch)

    assert captured["config"] == {
        "repo": {
            "path": str(workspace.root),
            "url": None,
            "branch": None,
            "index": False,
        }
    }
    assert captured["agent_key"] == "agent-123"
    assert captured["state_dir"] == launch.state_path
    assert captured["context_dir"] == launch.state_path / "context"


def test_repo_init_failure_is_a_launch_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, no_sync: None
) -> None:
    config = default_config(workspace)
    config["repo"] = {"path": "/nonexistent/repo"}
    write_config(workspace, config)
    launch = resolve_launch(make_env(workspace))

    def failing_initialize_repo(*args: Any, **kwargs: Any) -> None:
        raise ValueError("repo.path does not exist")

    monkeypatch.setattr(launcher_run, "initialize_repo", failing_initialize_repo)
    with pytest.raises(LaunchError, match=r"\[repo-init\].*does not exist"):
        execute(launch)


def test_static_repo_defect_fails_in_config_phase(workspace: Workspace) -> None:
    """A relative repo.path is a config error at resolve time, not a
    repo-init failure after credentials and sync checks."""
    config = default_config(workspace)
    config["repo"] = {"path": "relative/repo"}
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*absolute"):
        resolve_launch(make_env(workspace))


def test_repository_path_env_override(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["repo"] = {"path": str(workspace.root)}
    write_config(workspace, config)
    launch = resolve_launch(
        make_env(workspace, band_kit_repository_path="/mnt/other-repo")
    )
    assert launch.repo_config is not None
    assert launch.repo_config["path"] == "/mnt/other-repo"


def test_main_exits_nonzero_on_launch_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_resolve(*args: Any, **kwargs: Any) -> None:
        raise LaunchError("config", "boom")

    monkeypatch.setattr(launcher_run, "resolve_launch", failing_resolve)
    with pytest.raises(SystemExit) as exc_info:
        launcher_run.main()
    assert exc_info.value.code == 1


def test_main_exits_cleanly_on_os_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filesystem errors outside a named phase still exit 1, not a traceback."""

    def failing_resolve(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("unwritable log directory")

    monkeypatch.setattr(launcher_run, "resolve_launch", failing_resolve)
    with pytest.raises(SystemExit) as exc_info:
        launcher_run.main()
    assert exc_info.value.code == 1
