"""Strict configuration parsing, precedence, and endpoint validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.docker.launcher import (
    DEFAULT_REST_URL,
    DEFAULT_WS_URL,
    LaunchError,
    load_workspace_config,
    resolve_launch,
)

from .fakes import Workspace, default_config, make_env, write_config


def test_valid_config_resolves_fully(workspace: Workspace) -> None:
    launch = resolve_launch(make_env(workspace))
    assert launch.agent_id == "agent-123"
    assert launch.rest_url == "https://platform.example.test"
    assert launch.ws_url == "wss://platform.example.test/socket"
    assert launch.project == workspace.root.resolve()
    assert launch.entrypoint == (workspace.root / "main.py").resolve()
    assert launch.environment_path == workspace.runtime_root / "venv"


def test_unknown_top_level_field_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["surprise"] = True
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*surprise"):
        resolve_launch(make_env(workspace))


def test_unknown_nested_field_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["agent"]["model"] = "gpt-5"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*model"):
        resolve_launch(make_env(workspace))


def test_missing_runtime_section_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    del config["runtime"]
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*runtime"):
        resolve_launch(make_env(workspace))


def test_missing_config_file_fails_with_phase(workspace: Workspace) -> None:
    workspace.config_path.unlink()
    with pytest.raises(LaunchError, match=r"\[config\].*not found"):
        resolve_launch(make_env(workspace))


def test_config_path_override(workspace: Workspace, tmp_path: Path) -> None:
    moved = tmp_path / "elsewhere.yaml"
    moved.write_text(workspace.config_path.read_text(), encoding="utf-8")
    workspace.config_path.unlink()
    launch = resolve_launch(make_env(workspace, band_kit_config_path=str(moved)))
    assert launch.agent_id == "agent-123"


def test_env_overrides_beat_yaml(workspace: Workspace) -> None:
    launch = resolve_launch(
        make_env(
            workspace,
            band_agent_id="agent-env",
            band_rest_url="https://env.example.test",
            band_ws_url="wss://env.example.test/socket",
        )
    )
    assert launch.agent_id == "agent-env"
    assert launch.rest_url == "https://env.example.test"
    assert launch.ws_url == "wss://env.example.test/socket"


def test_endpoint_defaults_apply_only_when_unconfigured(
    workspace: Workspace,
) -> None:
    config = default_config(workspace)
    del config["band"]
    write_config(workspace, config)
    launch = resolve_launch(make_env(workspace))
    assert launch.rest_url == DEFAULT_REST_URL
    assert launch.ws_url == DEFAULT_WS_URL


def test_non_https_rest_url_rejected(workspace: Workspace) -> None:
    with pytest.raises(LaunchError, match=r"\[config\].*https"):
        resolve_launch(make_env(workspace, band_rest_url="http://insecure.test"))


def test_non_wss_ws_url_rejected(workspace: Workspace) -> None:
    with pytest.raises(LaunchError, match=r"\[config\].*wss"):
        resolve_launch(make_env(workspace, band_ws_url="ws://insecure.test"))


def test_missing_agent_id_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["agent"]["id"] = ""
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*agent id"):
        resolve_launch(make_env(workspace))


def test_yaml_that_is_not_a_mapping_rejected(workspace: Workspace) -> None:
    workspace.config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(LaunchError, match=r"\[config\].*mapping"):
        load_workspace_config(workspace.config_path)


def test_missing_workspace_dir_rejected(workspace: Workspace) -> None:
    env = make_env(workspace, workspace_dir="")
    with pytest.raises(LaunchError, match=r"\[config\].*WORKSPACE_DIR"):
        resolve_launch(env)


def test_wrong_uid_rejected(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from band.docker.launcher import run as launcher_run

    monkeypatch.setattr(launcher_run, "current_uid", lambda: 0)
    with pytest.raises(LaunchError, match=r"\[identity\].*uid 1000"):
        resolve_launch(make_env(workspace))


def test_missing_band_sdk_uv_rejected(workspace: Workspace) -> None:
    with pytest.raises(LaunchError, match=r"\[sync\].*BAND_SDK_UV"):
        resolve_launch(make_env(workspace, band_sdk_uv=""))
