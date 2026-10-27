"""The Fern state decides each part's action and stops on a date-file conflict."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.ghpeer import CLOSED, MERGED, OPEN, FakeGh
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import RELEASE_DATE, RELEASE_TAG


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
            ([], "FILE"),
            ([CLOSED], "FILE"),
            ([OPEN], "REUSE"),
            ([CLOSED, MERGED], "SKIP"),
            ([MERGED, OPEN], "SKIP"),
        ],
        ids=["absent", "closed", "open", "merged", "merged-and-reopened"],
    )
    def test_branch_pr_decides_the_action(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
        states: list[str],
        expected: str,
    ) -> None:
        for state in states:
            gh.add_pr(fern_names.changelog, state)
            gh.add_pr(fern_names.docs, state)
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 0
        action = fernstate.Action[expected]
        assert read_outputs(tmp_path / "out") == {"changelog": action, "docs": action}

    def test_rehearsal_reads_the_rehearsal_branches(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
    ) -> None:
        gh.add_pr(fern_names.changelog, MERGED)
        gh.add_pr(fern_names.docs_rehearsal, OPEN)
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE, "--rehearsal") == 0
        assert read_outputs(tmp_path / "out") == {
            "changelog": fernstate.Action.FILE,
            "docs": fernstate.Action.REUSE,
        }
        state = json.loads(
            (tmp_path / "state" / fernstate.STATE_FILE).read_text("utf-8")
        )
        assert (state["changelog_branch"], state["docs_branch"]) == (
            fern_names.changelog_rehearsal,
            fern_names.docs_rehearsal,
        )

    @pytest.mark.parametrize(
        "unrelated",
        [0, 100],
        ids=["listed", "past-the-first-100-files"],
    )
    def test_another_open_pr_on_the_date_file_stops(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        unrelated: int,
    ) -> None:
        files = [f"fern/docs/page-{n}.mdx" for n in range(unrelated)]
        conflict = gh.add_pr(
            "docs/typescript-hand-fix", OPEN, files=[*files, fern_names.date_file]
        )
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 1
        assert read_outputs(tmp_path / "out") == {}
        assert conflict.url in caplog.text

    @pytest.mark.parametrize(
        "branch",
        [lambda names: names.changelog, lambda names: names.changelog_rehearsal],
        ids=["own", "twin"],
    )
    def test_own_branch_and_its_twin_are_no_conflict(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
        branch: Callable[[FernNames], str],
    ) -> None:
        gh.add_pr(branch(fern_names), OPEN, files=[fern_names.date_file])
        assert run(fernstate, gh, tmp_path, "--date", RELEASE_DATE) == 0

    @pytest.mark.parametrize(
        ("extra", "earlier_filing"),
        [
            ((), lambda gh, names: None),
            (
                ("--date", RELEASE_DATE),
                lambda gh, names: gh.add_pr(names.changelog, MERGED),
            ),
        ],
        ids=["every-item-held", "changelog-already-merged"],
    )
    def test_no_changelog_to_file_ignores_the_date_file(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
        extra: tuple[str, ...],
        earlier_filing: Callable[[FakeGh, FernNames], object],
    ) -> None:
        earlier_filing(gh, fern_names)
        gh.add_pr("docs/hand-fix", OPEN, files=[fern_names.date_file])
        assert run(fernstate, gh, tmp_path, *extra) == 0
        assert read_outputs(tmp_path / "out") == {
            "changelog": fernstate.Action.SKIP,
            "docs": fernstate.Action.FILE,
        }

    def test_docs_listings_feed_docsrange(
        self,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        tmp_path: Path,
    ) -> None:
        merged = gh.add_pr(fern_names.older_docs, MERGED)
        pending = gh.add_pr(fern_names.docs, OPEN)
        gh.add_pr("docs/typescript-sdk-v1.0.0", MERGED)
        assert run(fernstate, gh, tmp_path) == 0
        state = tmp_path / "state"
        listings = (fernstate.DOCS_MERGED_FILE, fernstate.DOCS_OPEN_FILE)
        assert [json.loads((state / name).read_text("utf-8")) for name in listings] == [
            [{"number": merged.number, "headRefName": merged.head}],
            [{"number": pending.number, "headRefName": pending.head}],
        ]
