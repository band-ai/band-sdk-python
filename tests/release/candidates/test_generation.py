"""The pinned Release Please library generates coordinated Python releases."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from tests.paths import RELEASE_CANDIDATES, REPO_ROOT


def generate(sdk: list[str], mcp: list[str], previous_mcp: str = "9.2.0") -> list[dict]:
    payload = {
        "versions": {".": "9.2.0", "packages/band-mcp": previous_mcp},
        "commits": {".": sdk, "packages/band-mcp": mcp},
        "files": {
            "pyproject.toml": '[project]\nname="band-sdk"\nversion="9.2.0"\n',
            "packages/band-mcp/pyproject.toml": f'[project]\nname="band-mcp"\nversion="{previous_mcp}"\ndependencies=[\n"band-sdk>=9.2.0", # x-release-please-version\n]\n',
        },
    }
    result = subprocess.run(
        ["node", str(RELEASE_CANDIDATES / "runner.cjs"), str(REPO_ROOT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


class TestCandidateGeneration:
    @pytest.mark.parametrize(
        "sdk,mcp,previous,expected",
        [
            (["fix: sdk"], [], "9.2.0", "9.2.1"),
            ([], ["fix: mcp"], "9.2.0", "9.2.1"),
            (["fix: sdk"], ["feat: mcp"], "9.2.0", "9.3.0"),
            (["feat!: sdk break"], [], "9.2.0", "10.0.0"),
            (
                ["fix: release coordination"],
                ["fix: release coordination"],
                "2.2.15",
                "9.2.1",
            ),
        ],
    )
    def test_shared_versions_and_composed_dependency(
        self, sdk: list[str], mcp: list[str], previous: str, expected: str
    ) -> None:
        (candidate,) = generate(sdk, mcp, previous)
        assert candidate["branch"] == "release-please--branches--main"
        root = tomllib.loads(candidate["files"]["pyproject.toml"])
        package = tomllib.loads(candidate["files"]["packages/band-mcp/pyproject.toml"])
        assert (
            root["project"]["version"],
            package["project"]["version"],
            package["project"]["dependencies"],
        ) == (expected, expected, [f"band-sdk>={expected}"])
        assert expected in candidate["files"]["CHANGELOG.md"]
        assert expected in candidate["files"]["packages/band-mcp/CHANGELOG.md"]

    def test_non_release_changes_do_not_create_candidates(self) -> None:
        assert generate(["chore: housekeeping"], []) == []

    def test_repeated_preparation_does_not_bump_again(self) -> None:
        assert generate(["fix: sdk"], []) == generate(["fix: sdk"], [])

    def test_unsafe_mcp_only_migration_is_rejected(
        self, releasecheck: ModuleType, release_tree: Path
    ) -> None:
        (candidate,) = generate([], ["fix: mcp"], "2.2.15")
        for name in ("pyproject.toml", "packages/band-mcp/pyproject.toml"):
            (release_tree / name).write_text(candidate["files"][name])
        version = tomllib.loads(candidate["files"]["pyproject.toml"])["project"][
            "version"
        ]
        (release_tree / ".release-please-manifest.json").write_text(
            json.dumps({".": version, "packages/band-mcp": version})
        )
        with pytest.raises(ValueError, match="must exceed"):
            releasecheck.check_release(
                release_tree, {".": "9.2.0", "packages/band-mcp": "2.2.15"}
            )
