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
from band.docker.launcher import run as launcher_run

from .fakes import Workspace, default_config, enable_repo, make_env, write_config


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


def test_unsupported_schema_version_rejected(workspace: Workspace) -> None:
    """A file declaring another schema version was written for different
    semantics and must not be interpreted with this launcher's model."""
    config = default_config(workspace)
    config["schemaVersion"] = "2"
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*schemaVersion"):
        resolve_launch(make_env(workspace))


def test_missing_runtime_section_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    del config["runtime"]
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*runtime"):
        resolve_launch(make_env(workspace))


def test_repo_section_parsed(workspace: Workspace) -> None:
    write_config(workspace, enable_repo(default_config(workspace), branch="main"))
    config = load_workspace_config(workspace.config_path)
    assert config.repo is not None
    assert config.repo.url == "https://github.com/example/agent-project.git"
    assert config.repo.branch == "main"
    assert config.repo.index is False


def test_repo_path_field_rejected(workspace: Workspace) -> None:
    """The clone destination is always the fenced project path — a repo.path
    field would let the config direct clone writes elsewhere."""
    write_config(workspace, enable_repo(default_config(workspace), path="/tmp/x"))
    with pytest.raises(LaunchError, match=r"\[config\].*path"):
        resolve_launch(make_env(workspace))


def test_repo_url_missing_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["repo"] = {"branch": "main"}
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[config\].*repo\.url"):
        resolve_launch(make_env(workspace))


@pytest.mark.parametrize(
    "url",
    [
        "",  # blank: repo_init would normalize to None → local-only mode
        "   ",
        "ftp://host/repo.git",  # unsupported scheme
        "/local/path",
        "https://",  # no host, no path
        "https://:443/repo.git",  # port but no host
        "https://example.test",  # no repository path
        "ssh://",  # no host
        "git@github.com",  # SCP form without :path
        "git@:org/repo.git",  # SCP form without host
    ],
)
def test_repo_url_unsupported_rejected(workspace: Workspace, url: str) -> None:
    """Malformed remotes must fail in [config], not at git time."""
    write_config(workspace, enable_repo(default_config(workspace), url=url))
    with pytest.raises(LaunchError, match=r"\[config\].*repo\.url"):
        resolve_launch(make_env(workspace))


@pytest.mark.parametrize(
    "url",
    [
        "https://token:secret-value@example.test/repo.git",
        "https://secret-token@example.test/repo.git",
        "ssh://git:secret-value@example.test/repo.git",
    ],
)
def test_repo_url_with_embedded_credentials_rejected(
    workspace: Workspace, url: str
) -> None:
    """band.yaml is committed and repo_init logs the URL — a userinfo token
    must be rejected in [config], and the error must not echo it."""
    write_config(workspace, enable_repo(default_config(workspace), url=url))
    with pytest.raises(LaunchError, match=r"\[config\].*repo\.url") as exc_info:
        resolve_launch(make_env(workspace))
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "ssh://git@github.com/org/repo.git",  # canonical SSH login user
        "git@github.com:org/repo.git",
    ],
)
def test_repo_url_supported_forms_accepted(workspace: Workspace, url: str) -> None:
    write_config(workspace, enable_repo(default_config(workspace), url=url))
    config = load_workspace_config(workspace.config_path)
    assert config.repo is not None
    assert config.repo.url == url


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


@pytest.mark.parametrize(
    "rest_url",
    [
        "https://",  # no host at all
        "https://:443",  # port but no host (netloc is truthy)
        "https://user@",  # userinfo but no host (netloc is truthy)
    ],
)
def test_rest_url_without_host_rejected(workspace: Workspace, rest_url: str) -> None:
    """A scheme with no parseable hostname must fail the config phase, not
    the first connect attempt after the dependency sync."""
    with pytest.raises(LaunchError, match=r"\[config\].*host"):
        resolve_launch(make_env(workspace, band_rest_url=rest_url))


@pytest.mark.parametrize("ws_url", ["wss://", "wss://user@"])
def test_ws_url_without_host_rejected(workspace: Workspace, ws_url: str) -> None:
    with pytest.raises(LaunchError, match=r"\[config\].*host"):
        resolve_launch(make_env(workspace, band_ws_url=ws_url))


def test_rest_url_with_malformed_port_rejected(workspace: Workspace) -> None:
    """urlsplit parses the port lazily — the validator must force it."""
    with pytest.raises(LaunchError, match=r"\[config\].*not a valid URL"):
        resolve_launch(make_env(workspace, band_rest_url="https://host:not-a-port"))


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
    monkeypatch.setattr(launcher_run, "current_uid", lambda: 0)
    with pytest.raises(LaunchError, match=r"\[identity\].*uid 1000"):
        resolve_launch(make_env(workspace))


def test_missing_band_sdk_uv_rejected(workspace: Workspace) -> None:
    with pytest.raises(LaunchError, match=r"\[sync\].*BAND_SDK_UV"):
        resolve_launch(make_env(workspace, band_sdk_uv=""))
