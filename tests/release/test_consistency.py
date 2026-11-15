"""Release candidates must agree and advance beyond prior releases."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


class TestReleaseConsistency:
    def test_shared_release_advances_both_packages(
        self, releasecheck: ModuleType, release_tree: Path
    ) -> None:
        releasecheck.check_release(
            release_tree, {".": "9.2.0", "packages/band-mcp": "2.2.15"}
        )

    @pytest.mark.parametrize("previous", ["9.3.0", "10.0.0"])
    def test_rejects_reused_or_downgraded_release(
        self, releasecheck: ModuleType, release_tree: Path, previous: str
    ) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            releasecheck.check_release(release_tree, {".": previous})

    @pytest.mark.parametrize(
        "file,old,new",
        [
            ("pyproject.toml", "9.3.0", "9.2.0"),
            ("packages/band-mcp/pyproject.toml", "band-sdk>=9.3.0", "band-sdk>=9.2.0"),
            (".release-please-manifest.json", "9.3.0", "9.2.0"),
        ],
    )
    def test_rejects_incomplete_preparation(
        self,
        releasecheck: ModuleType,
        release_tree: Path,
        file: str,
        old: str,
        new: str,
    ) -> None:
        target = release_tree / file
        target.write_text(target.read_text().replace(old, new))
        with pytest.raises(ValueError, match="disagree"):
            releasecheck.check_release(release_tree)

    def test_historical_independent_release_is_accepted(
        self, releasecheck: ModuleType, release_tree: Path
    ) -> None:
        (release_tree / "release-please-config.json").write_text(
            json.dumps({"packages": {}})
        )
        (release_tree / "pyproject.toml").unlink()
        releasecheck.check_release(release_tree)

    @pytest.mark.parametrize(
        "requirements",
        [
            [],
            ["band-sdk>=1", "band-sdk>=2"],
            ["band-sdk>=1,<2"],
            ["band-sdk>=1; python_version > '3.11'"],
        ],
    )
    def test_rejects_ambiguous_floor(
        self, releasecheck: ModuleType, requirements: list[str]
    ) -> None:
        with pytest.raises(ValueError):
            releasecheck.sdk_floor(requirements)

    def test_missing_manifest_entry_is_rejected(
        self, releasecheck: ModuleType, release_tree: Path
    ) -> None:
        (release_tree / ".release-please-manifest.json").write_text("{}")
        with pytest.raises(ValueError, match="manifest entries"):
            releasecheck.check_release(release_tree)
