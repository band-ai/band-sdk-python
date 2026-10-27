"""The Fern state decides each part's action and stops on a date-file conflict."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.ghpeer import CLOSED, MERGED, OPEN, FakeGh
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import RELEASE_DATE, RELEASE_TAG

CHANGELOG_BRANCH = f"changelog/python-sdk-{RELEASE_TAG}"
DOCS_BRANCH = f"docs/python-sdk-{RELEASE_TAG}"


def run(fernstate: ModuleType, gh: FakeGh, tmp_path: Path, *extra: str) -> int:
    argv = [
        RELEASE_TAG,
        "--out",
        str(tmp_path / "state"),
        "--outputs",
        str(tmp_path / "out"),
    ]
    return fernstate.main([*argv, *extra], gh=gh)


class TestFernState:
    @pytest.mark.parametrize(
        ("states", "expected"),
        [
            ([], "file"),
            ([CLOSED], "file"),
            ([OPEN], "reuse"),
            ([CLOSED, MERGED], "skip"),
            ([MERGED, OPEN], "skip"),
        ],
        ids=["absent", "closed", "open", "merged", "merged-and-reopened"],
    )
    def test_branch_pr_decides_the_action(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        tmp_path: Path,
        states: list[str],
        expected: str,
    ) -> None:
        for state in states:
            gh.add_pr(CHANGELOG_BRANCH, state)
            gh.add_pr(DOCS_BRANCH, state)
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 0
        assert read_outputs(tmp_path / "out") == {
            "changelog": expected,
            "docs": expected,
        }

    def test_rehearsal_reads_the_rehearsal_branches(
        self, fernstate: ModuleType, gh: FakeGh, tmp_path: Path
    ) -> None:
        gh.add_pr(CHANGELOG_BRANCH, MERGED)
        gh.add_pr(f"{DOCS_BRANCH}-rehearsal", OPEN)
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE, "--rehearsal") == 0
        assert read_outputs(tmp_path / "out") == {"changelog": "file", "docs": "reuse"}
        state = json.loads((tmp_path / "state" / "state.json").read_text("utf-8"))
        assert (state["changelog_branch"], state["docs_branch"]) == (
            f"{CHANGELOG_BRANCH}-rehearsal",
            f"{DOCS_BRANCH}-rehearsal",
        )

    @pytest.mark.parametrize(
        "files",
        [
            ["fern/changelog/sdks/2026-10-20.md"],
            [f"fern/docs/page-{n}.mdx" for n in range(100)]
            + ["fern/changelog/sdks/2026-10-20.md"],
        ],
        ids=["listed", "past-the-first-100-files"],
    )
    def test_another_open_pr_on_the_date_file_stops(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        files: list[str],
    ) -> None:
        conflict = gh.add_pr("docs/typescript-hand-fix", OPEN, files=files)
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 1
        assert read_outputs(tmp_path / "out") == {}
        assert conflict.url in caplog.text

    @pytest.mark.parametrize(
        "branch",
        [CHANGELOG_BRANCH, f"{CHANGELOG_BRANCH}-rehearsal"],
        ids=["own", "twin"],
    )
    def test_own_branch_and_its_twin_are_no_conflict(
        self, fernstate: ModuleType, gh: FakeGh, tmp_path: Path, branch: str
    ) -> None:
        gh.add_pr(branch, OPEN, files=[f"fern/changelog/sdks/{RELEASE_DATE}.md"])
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 0

    @pytest.mark.parametrize(
        ("extra", "branch_prs"),
        [((), []), (("--date", RELEASE_DATE), [(CHANGELOG_BRANCH, MERGED)])],
        ids=["every-item-held", "changelog-already-merged"],
    )
    def test_no_changelog_to_file_ignores_the_date_file(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        tmp_path: Path,
        extra: tuple[str, ...],
        branch_prs: list[tuple[str, str]],
    ) -> None:
        for branch, state in branch_prs:
            gh.add_pr(branch, state)
        gh.add_pr(
            "docs/hand-fix", OPEN, files=[f"fern/changelog/sdks/{RELEASE_DATE}.md"]
        )
        assert run(fernstate, gh, tmp_path, *extra) == 0
        assert read_outputs(tmp_path / "out") == {"changelog": "skip", "docs": "file"}

    def test_docs_listings_feed_docsrange(
        self, fernstate: ModuleType, gh: FakeGh, tmp_path: Path
    ) -> None:
        gh.add_pr("docs/python-sdk-band-sdk-v6.0.1", MERGED)
        gh.add_pr("docs/python-sdk-band-sdk-v6.0.2", OPEN)
        gh.add_pr("docs/typescript-sdk-v1.0.0", MERGED)
        assert run(fernstate, gh, tmp_path) == 0
        state = tmp_path / "state"
        assert json.loads((state / "docs-merged.json").read_text("utf-8")) == [
            {"number": 1, "headRefName": "docs/python-sdk-band-sdk-v6.0.1"}
        ]
        assert json.loads((state / "docs-open.json").read_text("utf-8")) == [
            {"number": 2, "headRefName": "docs/python-sdk-band-sdk-v6.0.2"}
        ]
