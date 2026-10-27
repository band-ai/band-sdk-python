"""Peer: a local bare git remote standing in for the Fern repository."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, encoding="utf-8"
    ).stdout


def write_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    """Write ``files`` (``/``-separated paths) under ``root``."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
    return root


class FernRemote:
    """A bare repository whose ``base`` branch holds the seeded Fern files."""

    def __init__(self, root: Path, base: str, files: dict[str, str | bytes]) -> None:
        self.base = base
        self.bare = root / "fern.git"
        self.seed = root / "seed"
        # Every repository here ignores the developer's signing and hooks, and
        # keeps the seeded bytes (Git for Windows defaults to CRLF checkouts).
        self.config = {
            "commit.gpgsign": "false",
            "core.hooksPath": str(root / "no-hooks"),
            "core.autocrlf": "false",
        }
        run_git("init", "--quiet", "--bare", f"--initial-branch={base}", str(self.bare))
        run_git("init", "--quiet", f"--initial-branch={base}", str(self.seed))
        seed_config = {
            "user.name": "Fern Tester",
            "user.email": "fern-tester@example.invalid",
            **self.config,
        }
        for key, value in seed_config.items():
            run_git("-C", str(self.seed), "config", key, value)
        run_git("-C", str(self.seed), "remote", "add", "origin", str(self.bare))
        self.commit_to_base("chore: seed", files)

    def commit_to_base(self, subject: str, files: dict[str, str | bytes]) -> None:
        """Land ``files`` on the base branch, as another team's merge would."""
        write_tree(self.seed, files)
        run_git("-C", str(self.seed), "add", "--all")
        run_git(
            "-C",
            str(self.seed),
            "commit",
            "--quiet",
            "--allow-empty",
            "--message",
            subject,
        )
        run_git("-C", str(self.seed), "push", "--quiet", "origin", f"HEAD:{self.base}")

    def clone(self, dest: Path) -> Path:
        configs = [
            arg for item in self.config.items() for arg in ("--config", "=".join(item))
        ]
        run_git(
            "clone",
            "--quiet",
            *configs,
            "--branch",
            self.base,
            str(self.bare),
            str(dest),
        )
        return dest

    def head(self, branch: str) -> str:
        return run_git(
            "-C", str(self.bare), "rev-parse", f"refs/heads/{branch}"
        ).strip()

    def has_branch(self, branch: str) -> bool:
        refs = run_git("-C", str(self.bare), "branch", "--list", branch)
        return bool(refs.strip())

    def file_on(self, branch: str, path: str) -> str:
        return run_git("-C", str(self.bare), "show", f"{branch}:{path}")

    def push_branch(self, branch: str, files: dict[str, str | bytes]) -> str:
        """Push a branch off the base holding ``files``; return its head."""
        run_git(
            "-C",
            str(self.seed),
            "checkout",
            "--quiet",
            "-B",
            branch,
            f"origin/{self.base}",
        )
        write_tree(self.seed, files)
        run_git("-C", str(self.seed), "add", "--all")
        run_git("-C", str(self.seed), "commit", "--quiet", "--message", branch)
        run_git(
            "-C",
            str(self.seed),
            "push",
            "--quiet",
            "--force",
            "origin",
            f"HEAD:refs/heads/{branch}",
        )
        run_git("-C", str(self.seed), "checkout", "--quiet", self.base)
        return self.head(branch)
