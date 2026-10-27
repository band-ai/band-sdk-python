"""A merged docs PR closes the open docs PRs it supersedes, once, across re-runs."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.fernremote import FernRemote
from tests.skills.releasedocs.filingkit import (
    EDITED_PAGE,
    docs_args,
    file_fern,
    read_state,
)
from tests.skills.releasedocs.ghpeer import (
    CLOSED,
    MERGED,
    OPEN,
    PASSING,
    FakeGh,
)
from tests.skills.releasedocs.outputsfile import read_outputs


class TestFernSupersede:
    def test_rerun_closes_the_superseded_prs_a_failed_close_left_open(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(fern_names.older_docs, OPEN)
        gh.checks[fern_names.docs] = PASSING
        gh.failing_closes.add(older.number)
        state = read_state(fernstate, gh, tmp_path)
        summary = tmp_path / "summary.md"
        extra = docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )
        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                filefern.Mode.LIVE,
                *extra,
                "--summary",
                str(summary),
            )
            == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (MERGED, OPEN)
        # The failed run still links the merged docs PR.
        assert read_outputs(tmp_path / "outputs") == {
            "changelog_pr": "",
            "docs_pr": docs.url,
        }
        assert summary.read_text("utf-8").splitlines()[-1] == (
            f"- Fern docs: {filefern.Outcome.ERROR} {docs.url}"
        )
        # The re-run's docs chain skips the merged PR, so it files no docs.
        state = read_state(fernstate, gh, tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE)
            == 0
        )
        assert (older.state, older.comments) == (
            CLOSED,
            [f"Superseded by #{docs.number}"],
        )

    def test_rerun_after_the_superseded_prs_closed_closes_nothing_again(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(fern_names.older_docs, OPEN)
        gh.checks[fern_names.docs] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        state = read_state(fernstate, gh, tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE)
            == 0
        )
        (docs,) = gh.on(fern_names.docs)
        assert older.comments == [f"Superseded by #{docs.number}"]
