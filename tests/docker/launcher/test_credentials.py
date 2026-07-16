"""Opt-in workspace credential file: safeguards, parsing, and precedence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from band.docker.launcher import LaunchError, resolve_launch

from .fakes import (
    Workspace,
    default_config,
    enable_credentials,
    make_env,
    make_workspace,
    write_config,
    write_credentials,
)


def enable(workspace: Workspace) -> None:
    write_config(workspace, enable_credentials(default_config(workspace)))


def test_no_credentials_section_needs_env_key(workspace: Workspace) -> None:
    launch = resolve_launch(make_env(workspace))
    assert launch.file_credentials == {}


def test_band_api_key_required_from_somewhere(workspace: Workspace) -> None:
    with pytest.raises(LaunchError, match=r"\[credentials\].*BAND_API_KEY"):
        resolve_launch(make_env(workspace, band_api_key=""))


def test_file_fills_missing_band_api_key(workspace: Workspace) -> None:
    enable(workspace)
    write_credentials(workspace, "BAND_API_KEY=from-file\n")
    launch = resolve_launch(make_env(workspace, band_api_key=""))
    assert launch.file_credentials == {"BAND_API_KEY": "from-file"}


def test_process_env_wins_over_file(workspace: Workspace) -> None:
    enable(workspace)
    write_credentials(workspace, "BAND_API_KEY=from-file\nOPENAI_API_KEY=file-openai\n")
    launch = resolve_launch(make_env(workspace, band_api_key="from-env"))
    # The env key wins; only the missing name is taken from the file.
    assert launch.file_credentials == {"OPENAI_API_KEY": "file-openai"}


def test_unsupported_source_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["credentials"] = {
        "source": "vault",
        "path": ".band/secrets.env",
        "acknowledgePlaintextInSandbox": True,
    }
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[credentials\].*source"):
        resolve_launch(make_env(workspace))


def test_missing_acknowledgement_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["credentials"] = {
        "source": "workspace-env-file",
        "path": ".band/secrets.env",
    }
    write_config(workspace, config)
    write_credentials(workspace, "BAND_API_KEY=x\n")
    with pytest.raises(
        LaunchError, match=r"\[credentials\].*acknowledgePlaintextInSandbox"
    ):
        resolve_launch(make_env(workspace))


def test_missing_file_rejected(workspace: Workspace) -> None:
    enable(workspace)
    with pytest.raises(LaunchError, match=r"\[credentials\].*not found"):
        resolve_launch(make_env(workspace))


def test_symlinked_file_rejected_even_inside_workspace(
    workspace: Workspace,
) -> None:
    """The dedicated symlink guard must fire even when the link's target
    stays inside the workspace (containment alone would accept it)."""
    enable(workspace)
    real = workspace.root / "real-secrets.env"
    real.write_text("BAND_API_KEY=x\n", encoding="utf-8")
    real.chmod(0o600)
    cred_dir = workspace.root / ".band"
    cred_dir.mkdir()
    (cred_dir / "secrets.env").symlink_to(real)
    with pytest.raises(
        LaunchError, match=r"\[credentials\].*must not traverse a symlink"
    ):
        resolve_launch(make_env(workspace))


def test_symlinked_parent_dir_rejected(workspace: Workspace) -> None:
    """A symlinked directory on the credentials path is the same unexpected
    indirection as a symlinked leaf."""
    enable(workspace)
    hidden = workspace.root / "elsewhere"
    hidden.mkdir()
    secrets = hidden / "secrets.env"
    secrets.write_text("BAND_API_KEY=x\n", encoding="utf-8")
    secrets.chmod(0o600)
    (workspace.root / ".band").symlink_to(hidden, target_is_directory=True)
    with pytest.raises(
        LaunchError, match=r"\[credentials\].*must not traverse a symlink"
    ):
        resolve_launch(make_env(workspace))


def test_group_readable_file_rejected(workspace: Workspace) -> None:
    enable(workspace)
    write_credentials(workspace, "BAND_API_KEY=x\n", mode=0o640)
    with pytest.raises(LaunchError, match=r"\[credentials\].*owner-only"):
        resolve_launch(make_env(workspace))


def test_traversal_outside_workspace_rejected(workspace: Workspace) -> None:
    config = default_config(workspace)
    config["credentials"] = {
        "source": "workspace-env-file",
        "path": "../secrets.env",
        "acknowledgePlaintextInSandbox": True,
    }
    write_config(workspace, config)
    with pytest.raises(LaunchError, match=r"\[credentials\].*escapes"):
        resolve_launch(make_env(workspace))


def test_git_tracked_file_rejected(workspace: Workspace) -> None:
    enable(workspace)
    cred_path = write_credentials(workspace, "BAND_API_KEY=x\n")
    # Force-add past the .gitignore to simulate a committed secrets file.
    subprocess.run(
        ["git", "-C", str(workspace.root), "add", "-f", str(cred_path)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(LaunchError, match=r"\[credentials\].*tracked"):
        resolve_launch(make_env(workspace))


def test_unignored_file_rejected(workspace: Workspace) -> None:
    (workspace.root / ".gitignore").write_text("# nothing\n", encoding="utf-8")
    enable(workspace)
    write_credentials(workspace, "BAND_API_KEY=x\n")
    with pytest.raises(LaunchError, match=r"\[credentials\].*gitignored"):
        resolve_launch(make_env(workspace))


def test_non_git_workspace_allowed(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path, git=False)
    write_config(workspace, enable_credentials(default_config(workspace)))
    write_credentials(workspace, "BAND_API_KEY=from-file\n")
    launch = resolve_launch(make_env(workspace, band_api_key=""))
    assert launch.file_credentials == {"BAND_API_KEY": "from-file"}


def test_undocumented_names_rejected(workspace: Workspace) -> None:
    enable(workspace)
    write_credentials(workspace, "BAND_API_KEY=x\nAWS_SECRET_ACCESS_KEY=nope\n")
    with pytest.raises(LaunchError, match=r"\[credentials\].*AWS_SECRET_ACCESS_KEY"):
        resolve_launch(make_env(workspace))


def test_error_messages_never_contain_values(workspace: Workspace) -> None:
    enable(workspace)
    write_credentials(
        workspace, "BAND_API_KEY=super-secret-value\nBOGUS_NAME=leak-me\n"
    )
    with pytest.raises(LaunchError) as exc_info:
        resolve_launch(make_env(workspace))
    assert "super-secret-value" not in str(exc_info.value)
    assert "leak-me" not in str(exc_info.value)
