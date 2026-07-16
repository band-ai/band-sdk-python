"""Configurable path resolution, traversal rejection, and boundary rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.docker.launcher import LaunchError, resolve_launch

from .fakes import Workspace, default_config, make_env, write_config


def test_project_traversal_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["project"]["path"] = "../outside"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[paths\].*escapes"):
        resolve_launch(make_env(workspace))


def test_project_symlink_escape_rejected(workspace: Workspace, tmp_path: Path) -> None:
    outside = tmp_path / "outside-project"
    outside.mkdir()
    (workspace.root / "linked").symlink_to(outside, target_is_directory=True)
    config = default_config(workspace)
    config["project"]["path"] = "linked"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[paths\].*escapes"):
        resolve_launch(make_env(workspace))


def test_entrypoint_outside_project_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    subdir = workspace.root / "app"
    subdir.mkdir()
    (subdir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    config["project"]["path"] = "app"
    config["agent"]["entrypoint"] = "../main.py"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[paths\].*escapes"):
        resolve_launch(make_env(workspace))


def test_missing_entrypoint_rejected(workspace: Workspace) -> None:
    (workspace.root / "main.py").unlink()
    with pytest.raises(LaunchError, match=r"\[paths\].*entrypoint"):
        resolve_launch(make_env(workspace))


def test_runtime_path_inside_workspace_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["runtime"]["environmentPath"] = str(workspace.root / ".venv")
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[paths\].*outside the mounted workspace"):
        resolve_launch(make_env(workspace))


def test_runtime_path_inside_sdk_home_rejected(
    workspace: Workspace, tmp_path: Path
) -> None:
    sdk_home = tmp_path / "sdk-home"
    sdk_home.mkdir()
    config = default_config(workspace)
    config["runtime"]["statePath"] = str(sdk_home / "state")
    write_config(workspace, config)
    env = make_env(workspace, band_sdk_home=str(sdk_home))
    with pytest.raises(LaunchError, match=r"\[paths\].*outside the SDK home"):
        resolve_launch(env)


def test_empty_sdk_home_keeps_default_fence(workspace: Workspace) -> None:
    """A present-but-empty BAND_SDK_HOME must fence /opt/band, not the cwd."""
    config = default_config(workspace)
    config["runtime"]["cachePath"] = "/opt/band/cache"
    write_config(workspace, config)
    env = make_env(workspace, band_sdk_home="")
    with pytest.raises(LaunchError, match=r"\[paths\].*outside the SDK home"):
        resolve_launch(env)


def test_relative_runtime_path_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["runtime"]["cachePath"] = "relative/cache"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[paths\].*absolute"):
        resolve_launch(make_env(workspace))


def test_project_env_override_wins(workspace: Workspace) -> None:
    subdir = workspace.root / "svc"
    subdir.mkdir()
    (subdir / "main.py").write_text("print('svc')\n", encoding="utf-8")
    (subdir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (subdir / "uv.lock").write_text("# lock\n", encoding="utf-8")
    launch = resolve_launch(make_env(workspace, band_kit_project_path="svc"))
    assert launch.project == subdir.resolve()
    assert launch.entrypoint == (subdir / "main.py").resolve()


def test_runtime_env_overrides_win(workspace: Workspace, tmp_path: Path) -> None:
    alt = tmp_path / "alt-runtime"
    launch = resolve_launch(
        make_env(
            workspace,
            band_kit_environment_path=str(alt / "venv"),
            band_kit_state_path=str(alt / "state"),
            band_kit_cache_path=str(alt / "cache"),
            band_kit_log_path=str(alt / "logs"),
        )
    )
    assert launch.environment_path == alt / "venv"
    assert launch.state_path == alt / "state"
    assert launch.cache_path == alt / "cache"
    assert launch.log_path == alt / "logs"
