"""The docs review range starts after the newest documented release, never too far back."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.gitrepo import ThrowawayRepo

# The releases before TAG, by version.
EARLIER = [(6, 8, 0), (6, 9, 0), (6, 10, 0), (6, 11, 0), (6, 12, 0)]
TAG = "band-sdk-v6.13.0"


def prs(docsrange: ModuleType, *branches: tuple[int, str]) -> list[object]:
    return [
        docsrange.DocsPr(number=number, head_ref_name=name) for number, name in branches
    ]


def expected(docsrange: ModuleType, baseline: str, supersede: list[int]) -> object:
    return docsrange.DocsRange(baseline=baseline, supersede=supersede)


def gh_listing(path: Path, *branches: tuple[int, str]) -> Path:
    """Write ``gh pr list --json number,headRefName`` output."""
    rows = [{"number": number, "headRefName": name} for number, name in branches]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


class TestDocsRange:
    def test_baseline_is_the_newest_documented_release_by_version(
        self, docsrange: ModuleType
    ) -> None:
        merged = prs(
            docsrange,
            (1, "docs/python-sdk-band-sdk-v6.11.0"),
            (2, "docs/python-sdk-band-sdk-v6.12.0"),
            (3, "docs/python-sdk-band-sdk-v6.10.0"),
        )
        assert docsrange.docs_range(TAG, EARLIER, merged, []) == expected(
            docsrange, "band-sdk-v6.12.0", []
        )

    def test_versions_compare_numerically(self, docsrange: ModuleType) -> None:
        earlier = [(6, 9, 0), (6, 10, 0)]
        merged = prs(
            docsrange,
            (1, "docs/python-sdk-band-sdk-v6.9.0"),
            (2, "docs/python-sdk-band-sdk-v6.10.0"),
        )
        assert docsrange.docs_range(
            "band-sdk-v6.11.0", earlier, merged, []
        ) == expected(docsrange, "band-sdk-v6.10.0", [])

    @pytest.mark.parametrize(
        "merged",
        [[], [(1, "docs/python-sdk-band-sdk-v6.8.0")]],
        ids=["nothing-merged", "merged-before-the-floor"],
    )
    def test_baseline_never_reaches_further_back_than_the_floor(
        self, docsrange: ModuleType, merged: list[tuple[int, str]]
    ) -> None:
        floor = "band-sdk-v6.10.0"  # the third release before TAG
        found = docsrange.docs_range(TAG, EARLIER, prs(docsrange, *merged), [])
        assert found == expected(docsrange, floor, [])

    def test_short_history_floors_at_the_first_release(
        self, docsrange: ModuleType
    ) -> None:
        assert docsrange.docs_range("band-sdk-v1.1.0", [(1, 0, 0)], [], []) == expected(
            docsrange, "band-sdk-v1.0.0", []
        )

    def test_supersede_holds_only_releases_inside_the_range(
        self, docsrange: ModuleType
    ) -> None:
        open_prs = prs(
            docsrange,
            (10, "docs/python-sdk-band-sdk-v6.11.0"),
            (11, "docs/python-sdk-band-sdk-v6.12.0"),
            (12, "docs/python-sdk-band-sdk-v6.10.0"),
            (13, "docs/python-sdk-band-sdk-v6.14.0"),
            (14, "docs/python-sdk-band-sdk-v6.13.0"),
        )
        assert docsrange.docs_range(TAG, EARLIER, [], open_prs) == expected(
            docsrange, "band-sdk-v6.10.0", [10, 11]
        )

    @pytest.mark.parametrize(
        "branch",
        ["docs/python-sdk-band-sdk-v6.13.0", "docs/python-sdk-band-sdk-v6.14.0"],
        ids=["this-release", "newer-release"],
    )
    def test_review_older_than_merged_docs_is_refused(
        self, docsrange: ModuleType, branch: str
    ) -> None:
        merged = prs(docsrange, (1, "docs/python-sdk-band-sdk-v6.12.0"), (2, branch))
        with pytest.raises(ValueError, match="already merged"):
            docsrange.docs_range(TAG, EARLIER, merged, [])

    @pytest.mark.parametrize(
        "branch",
        [
            "docs/python-sdk-band-sdk-v6.12.0-test",
            "docs/python-sdk-band-sdk-v6.12.0-rehearsal",
            "docs/python-sdk-band-sdk-v3.2.0-v3.2.1",
            "docs/typescript-sdk-band-sdk-v6.12.0",
        ],
    )
    def test_other_branch_names_are_ignored(
        self, docsrange: ModuleType, branch: str
    ) -> None:
        found = prs(docsrange, (20, branch))
        assert docsrange.docs_range(TAG, EARLIER, found, found) == expected(
            docsrange, "band-sdk-v6.10.0", []
        )

    def test_command_reads_gh_listings_and_earlier_tags_from_history(
        self,
        docsrange: ModuleType,
        repo: ThrowawayRepo,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for version in ("6.10.0", "6.11.0", "6.12.0"):
            repo.commit(f"feat: {version}", {"src/band/a.py": version})
            repo.tag(f"band-sdk-v{version}")
        # A release on another line of history is not before TAG.
        repo.git("switch", "--quiet", "--create", "side", "band-sdk-v6.11.0")
        repo.commit("fix: side", {"src/band/b.py": "side"})
        repo.tag("band-sdk-v6.12.5")
        repo.git("switch", "--quiet", "main")
        repo.commit("feat: 6.13.0", {"src/band/a.py": "6.13.0"})
        repo.tag(TAG)
        merged = gh_listing(tmp_path / "merged.json")
        open_prs = gh_listing(
            tmp_path / "open.json", (7, "docs/python-sdk-band-sdk-v6.12.0")
        )
        argv = [TAG, str(merged), str(open_prs), "--repo", str(repo.root)]
        assert docsrange.main(argv) == 0
        assert json.loads(capsys.readouterr().out) == {
            "baseline": "band-sdk-v6.10.0",
            "supersede": [7],
        }

    @pytest.mark.parametrize(
        ("tag", "error"),
        [("band-sdk-v1.0.0", "precedes"), ("v6.13.0", "is not a")],
        ids=["first-release", "not-a-release-tag"],
    )
    def test_command_logs_why_there_is_no_range(
        self,
        docsrange: ModuleType,
        repo: ThrowawayRepo,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        tag: str,
        error: str,
    ) -> None:
        repo.commit("feat: first release", {"src/band/a.py": "1.0.0"})
        repo.tag("band-sdk-v1.0.0")
        listing = gh_listing(tmp_path / "prs.json")
        argv = [tag, str(listing), str(listing), "--repo", str(repo.root)]
        assert docsrange.main(argv) == 1
        assert error in caplog.text
