from __future__ import annotations

from pathlib import Path

import pytest

from band.docker import repo_init


def test_parse_repo_config_absent() -> None:
    assert repo_init.parse_repo_config({}) is None


def test_parse_repo_config_invalid_url() -> None:
    with pytest.raises(ValueError, match="repo.url"):
        repo_init.parse_repo_config(
            {
                "repo": {
                    "url": "ftp://example.com/repo.git",
                    "path": "/workspace/repo",
                }
            }
        )


def test_initialize_repo_clone_and_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "repo"
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"

    def fake_preflight(_: str) -> None:
        return None

    def fake_clone(_: repo_init.RepoConfig, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / ".git").mkdir()
        (destination / "README.md").write_text("hello\n", encoding="utf-8")

    def fake_git(cwd: Path | None, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "abc123\n"
        if args[:3] == ("remote", "get-url", "origin"):
            return "https://github.com/org/repo.git\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "main\n"
        raise AssertionError(f"Unexpected git args: cwd={cwd} args={args}")

    monkeypatch.setattr(repo_init, "_preflight_repo_auth", fake_preflight)
    monkeypatch.setattr(repo_init, "_clone_repo", fake_clone)
    monkeypatch.setattr(repo_init, "_git", fake_git)

    result = repo_init.initialize_repo(
        {
            "repo": {
                "url": "https://github.com/org/repo.git",
                "path": str(repo_path),
                "branch": "main",
                "index": True,
            }
        },
        agent_key="planner",
        state_dir=state_dir,
        context_dir=context_dir,
    )

    assert result.enabled is True
    assert result.cloned is True
    assert result.indexed is True
    assert result.repo_path == str(repo_path)
    assert "structure.md" in result.context_bundle
    assert (context_dir / "structure.md").exists()
    assert (context_dir / "patterns.md").exists()
    assert (context_dir / "dependencies.md").exists()
    assert (state_dir / "repo_init_meta.json").exists()


def test_initialize_repo_skips_clone_for_existing_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"

    def fake_git(cwd: Path | None, *args: str) -> str:
        if args == ("remote", "get-url", "origin"):
            return "git@github.com:org/repo.git\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "main\n"
        if args == ("rev-parse", "HEAD"):
            return "abc123\n"
        raise AssertionError(f"Unexpected git args: cwd={cwd} args={args}")

    monkeypatch.setattr(repo_init, "_git", fake_git)
    monkeypatch.setattr(repo_init, "_preflight_repo_auth", lambda _: None)

    result = repo_init.initialize_repo(
        {
            "repo": {
                "url": "https://github.com/org/repo.git",
                "path": str(repo_path),
                "branch": "main",
                "index": False,
            }
        },
        agent_key="reviewer",
        state_dir=state_dir,
        context_dir=context_dir,
    )

    assert result.enabled is True
    assert result.cloned is False
    assert result.indexed is False


def test_initialize_repo_accepts_git_worktree_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").write_text(
        "gitdir: /tmp/main-repo/.git/worktrees/repo\n", encoding="utf-8"
    )
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"

    def fake_git(cwd: Path | None, *args: str) -> str:
        if args == ("remote", "get-url", "origin"):
            return "git@github.com:org/repo.git\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "main\n"
        if args == ("rev-parse", "HEAD"):
            return "abc123\n"
        raise AssertionError(f"Unexpected git args: cwd={cwd} args={args}")

    monkeypatch.setattr(repo_init, "_git", fake_git)
    monkeypatch.setattr(repo_init, "_preflight_repo_auth", lambda _: None)

    result = repo_init.initialize_repo(
        {
            "repo": {
                "url": "https://github.com/org/repo.git",
                "path": str(repo_path),
                "branch": "main",
                "index": False,
            }
        },
        agent_key="reviewer",
        state_dir=state_dir,
        context_dir=context_dir,
    )

    assert result.enabled is True
    assert result.cloned is False
    assert result.indexed is False


def test_initialize_repo_rejects_non_git_non_empty_directory(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "file.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty and not a git repository"):
        repo_init.initialize_repo(
            {
                "repo": {
                    "url": "https://github.com/org/repo.git",
                    "path": str(repo_path),
                }
            },
            agent_key="planner",
            state_dir=tmp_path / "state",
            context_dir=tmp_path / "context",
        )


def test_ssh_preflight_requires_ssh_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_init.Path, "home", lambda: tmp_path)

    with pytest.raises(ValueError, match="SSH directory is missing"):
        repo_init._preflight_repo_auth("git@github.com:org/repo.git")


def test_ssh_and_https_detection() -> None:
    assert repo_init.is_ssh_url("git@github.com:org/repo.git")
    assert repo_init.is_ssh_url("ssh://git@github.com/org/repo.git")
    assert repo_init.is_https_url("https://github.com/org/repo.git")
    assert not repo_init.is_https_url("git@github.com:org/repo.git")


def test_https_preflight_is_noop() -> None:
    repo_init._preflight_repo_auth("https://github.com/org/repo.git")


def test_remote_canonicalization_equivalent_protocols() -> None:
    ssh = repo_init._canonical_remote("git@github.com:org/repo.git")
    https = repo_init._canonical_remote("https://github.com/org/repo.git")
    assert ssh == https


# ---------------------------------------------------------------------------
# Local-only mode (repo.path without repo.url)
# ---------------------------------------------------------------------------


def test_repo_config_accepts_path_only() -> None:
    """RepoConfig should accept path without url for local-only mode."""
    config = repo_init.RepoConfig(path="/workspace/repo")
    assert config.url is None
    assert config.path == "/workspace/repo"


def test_repo_config_empty_url_becomes_none() -> None:
    """An empty-string url should be normalized to None."""
    config = repo_init.RepoConfig(url="", path="/workspace/repo")
    assert config.url is None


def test_parse_repo_config_path_only() -> None:
    """parse_repo_config should accept repo with path but no url."""
    config = repo_init.parse_repo_config(
        {"repo": {"path": "/workspace/repo", "index": True}}
    )
    assert config is not None
    assert config.url is None
    assert config.path == "/workspace/repo"
    assert config.index is True


def test_initialize_repo_local_only_skips_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-only mode: existing directory, no clone, indexing works."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"

    def fake_git(cwd: Path | None, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "abc123\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "main\n"
        raise AssertionError(f"Unexpected git args: cwd={cwd} args={args}")

    monkeypatch.setattr(repo_init, "_git", fake_git)

    result = repo_init.initialize_repo(
        {"repo": {"path": str(repo_path), "index": True}},
        agent_key="local_agent",
        state_dir=state_dir,
        context_dir=context_dir,
    )

    assert result.enabled is True
    assert result.cloned is False
    assert result.indexed is True
    assert result.repo_path == str(repo_path)
    assert (context_dir / "structure.md").exists()


def test_initialize_repo_local_only_nonexistent_raises(tmp_path: Path) -> None:
    """Local-only mode should raise if the path does not exist."""
    missing_path = tmp_path / "does_not_exist"

    with pytest.raises(ValueError, match="repo.path does not exist"):
        repo_init.initialize_repo(
            {"repo": {"path": str(missing_path)}},
            agent_key="local_agent",
            state_dir=tmp_path / "state",
            context_dir=tmp_path / "context",
        )


def test_initialize_repo_local_only_not_a_dir_raises(tmp_path: Path) -> None:
    """Local-only mode should raise if the path exists but is a file."""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        repo_init.initialize_repo(
            {"repo": {"path": str(file_path)}},
            agent_key="local_agent",
            state_dir=tmp_path / "state",
            context_dir=tmp_path / "context",
        )


def test_initialize_repo_local_only_with_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-only mode should checkout the specified branch."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"

    checkout_calls: list[str] = []

    def fake_git(cwd: Path | None, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "abc123\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "main\n"
        if args == ("checkout", "feature"):
            checkout_calls.append("feature")
            return ""
        raise AssertionError(f"Unexpected git args: cwd={cwd} args={args}")

    monkeypatch.setattr(repo_init, "_git", fake_git)

    result = repo_init.initialize_repo(
        {"repo": {"path": str(repo_path), "branch": "feature"}},
        agent_key="local_agent",
        state_dir=state_dir,
        context_dir=context_dir,
    )

    assert result.enabled is True
    assert result.cloned is False
    assert checkout_calls == ["feature"]
