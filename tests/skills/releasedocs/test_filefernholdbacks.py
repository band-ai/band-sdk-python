"""Filing leaves held items out, explains them, and keeps held features undocumented."""

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
    OPEN,
    PASSING,
    FakeGh,
)
from tests.skills.releasedocs.samples import (
    HELD_PR,
    LEDGER,
    LEDGER_ID,
    LEDGER_REASON,
    RECONNECT,
    RELEASE_DATE,
    RELEASED_LEDGER,
    SEND_RETRIES,
    TOKEN_REDACTION,
)


class TestFernHoldbacks:
    @pytest.mark.parametrize(
        "held",
        [(1, 2), (2,)],
        ids=["held-by-holdbacks", "held-by-the-ledger-since-holdbacks-ran"],
    )
    def test_held_items_are_left_out_and_explained_in_the_pr(
        self,
        held: tuple[int, ...],
        releasedata: ModuleType,
        filefern: ModuleType,
        fernstate: ModuleType,
        holdbacks: ModuleType,
        renderentry: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        fern_remote.commit_to_base(
            "chore: hold room tasks", {releasedata.HELD_ITEMS: LEDGER}
        )
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        kept = make_item(*SEND_RETRIES)
        items = [
            kept,
            make_item(*RECONNECT, sources=(HELD_PR,)),
            make_item(*TOKEN_REDACTION),
        ]
        extra = notes_args(make_notes, items, tmp_path, *held)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        assert gh.on(fern_names.changelog)[0].body == renderentry.pr_body(
            make_notes([kept]),
            [
                f"- **Reconnect** — Rooms rejoin after a drop. ({LEDGER_ID}: {LEDGER_REASON})",
                f"- **Token Redaction** — Keys never reach logs. ({holdbacks.NO_LEDGER_HOLD})",
            ],
        )
        assert fern_remote.file_on(
            fern_names.changelog, fern_names.date_file
        ) == renderentry.render_standalone(make_notes([kept]))

    def test_item_whose_ledger_entry_was_released_since_is_not_called_review_flagged(
        self,
        releasedata: ModuleType,
        filefern: ModuleType,
        fernstate: ModuleType,
        holdbacks: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        fern_remote.commit_to_base(
            "chore: release room tasks", {releasedata.HELD_ITEMS: RELEASED_LEDGER}
        )
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES), make_item(*RECONNECT, sources=(HELD_PR,))]
        extra = notes_args(make_notes, items, tmp_path, 1)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        assert gh.on(fern_names.changelog)[0].body.endswith(
            "## Holdbacks\n\n"
            f"- **Reconnect** — Rooms rejoin after a drop. ({holdbacks.NO_LEDGER_HOLD})\n"
        )

    @pytest.mark.parametrize(
        ("ledger", "held"),
        [(LEDGER, []), (RELEASED_LEDGER, ["0"])],
        ids=["ledger-holds-a-feature", "release-holds-an-item"],
    )
    def test_active_holdbacks_open_the_docs_pr_unmerged(
        self,
        releasedata: ModuleType,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        tmp_path: Path,
        ledger: str,
        held: list[str],
    ) -> None:
        fern_remote.commit_to_base(
            "chore: update holdbacks", {releasedata.HELD_ITEMS: ledger}
        )
        gh.checks[fern_names.docs] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        extra += ["--held", *held]
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert docs.state == OPEN
        assert f"## Do not merge\n\n- {filefern.HOLDBACKS_ACTIVE}\n" in docs.body

    def test_breaking_change_held_since_holdbacks_ran_is_not_filed(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        releasedata: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        fern_remote.commit_to_base(
            "chore: hold room tasks", {releasedata.HELD_ITEMS: LEDGER}
        )
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path, breaking=(HELD_PR,))
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        assert (
            gh.on(fern_names.changelog),
            fern_remote.has_branch(fern_names.changelog),
        ) == ([], False)
