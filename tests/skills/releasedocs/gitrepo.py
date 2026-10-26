"""A throwaway git repository the collector reads release ranges from."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Mirrors the root package entry of this repository's release-please config.
RELEASE_PLEASE_CONFIG = {"packages": {".": {"exclude-paths": ["packages"]}}}


class ThrowawayRepo:
    """A real git repository with squash-style commits and release tags."""

    def __init__(self, root: Path, config_file: str) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Release Tester")
        self.git("config", "user.email", "release-tester@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.commit("chore: initial", {config_file: json.dumps(RELEASE_PLEASE_CONFIG)})

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def commit(self, subject: str, files: dict[str, str], body: str = "") -> str:
        """Write ``files`` and commit them as one squash commit; return its SHA."""
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
        self.git("add", "--all")
        message = f"{subject}\n\n{body}" if body else subject
        self.git("commit", "--quiet", "--message", message)
        return self.git("rev-parse", "HEAD").strip()

    def tag(self, name: str) -> None:
        self.git("tag", name)
