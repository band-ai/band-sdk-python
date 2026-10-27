"""A rehearsal files on its own branches and always closes its PRs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.fernremote import FernRemote
from tests.skills.releasedocs.filingkit import (
    EDITED_PAGE,
    docs_args,
    file_fern,
    notes_args,
    read_state,
)
from tests.skills.releasedocs.ghpeer import (
    CLOSED,
    FAILING,
    OPEN,
    PASSING,
    PENDING,
    FakeGh,
)
from tests.skills.releasedocs.samples import (
    RELEASE_DATE,
    SEND_RETRIES,
)


class TestFernRehearsal:
    def test_test_mode_closes_both_rehearsal_prs_unmerged(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(fern_names.older_docs, OPEN)
        gh.checks |= {
            fern_names.changelog_rehearsal: PASSING,
            fern_names.docs_rehearsal: PASSING,
        }
        state = read_state(
            fernstate, gh, tmp_path, "--date", RELEASE_DATE, "--rehearsal"
        )
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        extra += docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.TEST, *extra
            )
            == 0
        )
        changelog, docs = (
            gh.on(fern_names.changelog_rehearsal)[0],
            gh.on(fern_names.docs_rehearsal)[0],
        )
        assert (changelog.state, docs.state, older.state) == (CLOSED, CLOSED, OPEN)
        assert (gh.on(fern_names.changelog), gh.on(fern_names.docs)) == ([], [])

    @pytest.mark.parametrize("checks", [FAILING, PENDING], ids=["failed", "unfinished"])
    def test_rehearsal_pr_whose_checks_do_not_pass_is_closed_and_fails(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        checks: list[dict[str, str | None]],
    ) -> None:
        gh.checks[fern_names.changelog_rehearsal] = checks
        state = read_state(
            fernstate, gh, tmp_path, "--date", RELEASE_DATE, "--rehearsal"
        )
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.TEST, *extra
            )
            == 1
        )
        assert gh.on(fern_names.changelog_rehearsal)[0].state == CLOSED

    def test_rehearsal_closes_a_docs_pr_held_by_holdbacks(
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
        gh.checks[fern_names.docs_rehearsal] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--rehearsal")
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        extra += ["--held", "0"]
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.TEST, *extra
            )
            == 0
        )
        assert gh.on(fern_names.docs_rehearsal)[0].state == CLOSED
