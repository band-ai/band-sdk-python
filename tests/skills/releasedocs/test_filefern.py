"""Filing pushes checked edits, merges only on passing checks, and never repeats a merged part."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.fernremote import FernRemote, run_git, write_tree
from tests.skills.releasedocs.ghpeer import (
    CLOSED,
    FAILING,
    MERGED,
    OPEN,
    PARTIAL,
    PASSING,
    PENDING,
    FakeGh,
)
from tests.skills.releasedocs.holdbackfiles import release_prs
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import (
    DOCS_PAGE,
    DOCS_PAGE_TEXT,
    DOCS_REPORT,
    HELD_PR,
    LEDGER,
    LEDGER_ID,
    LEDGER_REASON,
    RECONNECT,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASED_LEDGER,
    SEND_RETRIES,
    TOKEN_REDACTION,
)

EDITED_PAGE = DOCS_PAGE_TEXT.replace("Agent.create()", "Agent.start()")


def read_state(
    fernstate: ModuleType, gh: FakeGh, tmp_path: Path, *extra: str
) -> list[str]:
    """Record the Fern state; return the filing's arguments that point at it."""
    out = tmp_path / "state"
    assert fernstate.main([RELEASE_TAG, "--out", str(out), *extra], gh=gh) == 0
    return [
        "--state",
        str(out / fernstate.STATE_FILE),
        "--docs-open",
        str(out / fernstate.DOCS_OPEN_FILE),
    ]


def docs_args(
    checkpatch: ModuleType,
    fern_files: dict[str, str | bytes],
    tmp_path: Path,
    page: str,
    supersede: tuple[int, ...] = (),
) -> list[str]:
    """Review ``page`` into an edited copy of Fern and check it, as the workflow does."""
    pristine = write_tree(tmp_path / "pristine", fern_files)
    edited = tmp_path / "edited"
    shutil.copytree(pristine, edited)
    write_tree(edited, {DOCS_PAGE: page})
    check, report, docs_range = (
        tmp_path / name for name in ("patch.json", "report.json", "range.json")
    )
    assert checkpatch.main([str(pristine), str(edited), "--out", str(check)]) == 0
    report.write_text(json.dumps(DOCS_REPORT), encoding="utf-8")
    docs_range.write_text(
        json.dumps({"baseline": "band-sdk-v6.0.0", "supersede": list(supersede)}),
        "utf-8",
    )
    return [
        "--docs-check",
        str(check),
        "--docs-report",
        str(report),
        "--docs-range",
        str(docs_range),
        "--edited",
        str(edited),
        "--pristine",
        str(pristine),
    ]


def notes_args(
    make_notes: Callable[..., object],
    items: list[object],
    tmp_path: Path,
    *held: int,
    breaking: tuple[str, ...] = (),
) -> list[str]:
    path, prs = tmp_path / "release-notes.json", tmp_path / "release-prs.json"
    path.write_text(make_notes(items).model_dump_json(), encoding="utf-8")  # type: ignore[attr-defined]
    prs.write_text(json.dumps(release_prs(breaking)), encoding="utf-8")
    return ["--notes", str(path), "--release-prs", str(prs), "--held", *map(str, held)]


def file_fern(
    filefern: ModuleType,
    gh: FakeGh,
    remote: FernRemote,
    tmp_path: Path,
    state: list[str],
    mode: str,
    *extra: str,
    wait: Callable[[], None] = lambda: None,
) -> int:
    """File from a fresh clone, as each workflow run does; ``wait`` runs between polls."""
    dest = tmp_path / "fern-write"
    shutil.rmtree(dest, ignore_errors=True)
    fern = remote.clone(dest)
    argv = [RELEASE_TAG, "--fern", str(fern), *state, "--mode", mode]
    argv += ["--outputs", str(tmp_path / "outputs"), *extra]
    return filefern.main(argv, gh=gh, poll=filefern.Poll(2, wait))


class TestFileFern:
    def test_live_files_merges_both_and_closes_the_superseded(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        renderentry: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(fern_names.older_docs, OPEN)
        gh.checks |= {fern_names.changelog: PASSING, fern_names.docs: PASSING}
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        summary = tmp_path / "summary.md"
        extra = notes_args(make_notes, items, tmp_path) + docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )
        extra += ["--summary", str(summary)]
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        changelog, docs = gh.on(fern_names.changelog)[0], gh.on(fern_names.docs)[0]
        assert (changelog.state, docs.state, older.state) == (MERGED, MERGED, CLOSED)
        assert older.comments == [f"Superseded by #{docs.number}"]
        assert fern_remote.file_on(
            fern_names.changelog, fern_names.date_file
        ) == renderentry.render_standalone(make_notes(items))
        assert fern_remote.file_on(fern_names.docs, DOCS_PAGE) == EDITED_PAGE
        assert read_outputs(tmp_path / "outputs") == {
            "changelog_pr": changelog.url,
            "docs_pr": docs.url,
        }
        assert summary.read_text("utf-8") == (
            f"- Fern changelog: {filefern.Outcome.MERGED} {changelog.url}\n"
            f"- Fern docs: {filefern.Outcome.MERGED} {docs.url}\n"
        )

    def test_commits_carry_the_release_bot_and_no_ai_attribution(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        commit = run_git(
            "-C",
            str(fern_remote.bare),
            "log",
            "-1",
            "--format=%an <%ae>%n%B",
            fern_names.changelog,
        )
        assert (
            commit.strip()
            == f"band-release-bot <release-bot@band.ai>\ndocs(changelog): Python SDK {RELEASE_TAG}"
        )

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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        assert gh.on(fern_names.changelog)[0].body == renderentry.pr_body(
            make_notes([kept]),
            [
                f"- **Reconnect** — Rooms rejoin after a drop. ({LEDGER_ID}: {LEDGER_REASON})",
                f"- **Token Redaction** — Keys never reach logs. ({holdbacks.FLAGGED_BY_REVIEW})",
            ],
        )
        assert fern_remote.file_on(
            fern_names.changelog, fern_names.date_file
        ) == renderentry.render_standalone(make_notes([kept]))

    def test_open_pr_is_refiled_from_this_runs_content_before_merging(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        renderentry: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        pushed = {
            fern_names.date_file: "pushed since the last run",
            "fern/docs.yml": "x",
        }
        earlier = gh.add_pr(
            fern_names.changelog,
            OPEN,
            sha=fern_remote.push_branch(fern_names.changelog, pushed),
        )
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        assert (gh.on(fern_names.changelog), earlier.state) == ([earlier], MERGED)
        assert earlier.sha == fern_remote.head(fern_names.changelog)
        assert fern_remote.file_on(
            fern_names.changelog, fern_names.date_file
        ) == renderentry.render_standalone(make_notes(items))
        assert run_git(
            "-C",
            str(fern_remote.bare),
            "diff",
            "--name-only",
            f"{fern_remote.base}...{fern_names.changelog}",
        ).split() == [fern_names.date_file]

    def test_merged_pr_is_not_filed_again(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        merged = gh.add_pr(fern_names.changelog, MERGED)
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        assert (
            gh.on(fern_names.changelog),
            fern_remote.has_branch(fern_names.changelog),
        ) == ([merged], False)
        assert read_outputs(tmp_path / "outputs") == {"changelog_pr": merged.url}

    @pytest.mark.parametrize(
        "checks", [FAILING, PENDING, PARTIAL], ids=["failed", "unfinished", "partial"]
    )
    def test_pr_whose_checks_do_not_pass_stays_open_and_fails(
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
        gh.checks[fern_names.changelog] = checks
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (pr,) = gh.on(fern_names.changelog)
        assert (pr.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": pr.url},
        )

    def test_checks_still_running_at_first_merge_once_they_pass(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        gh.checks[fern_names.changelog] = PENDING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)

        def checks_finish() -> None:
            gh.checks[fern_names.changelog] = PASSING

        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                "live",
                *extra,
                wait=checks_finish,
            )
            == 0
        )
        assert gh.on(fern_names.changelog)[0].state == MERGED

    def test_head_moved_after_the_checks_is_not_merged(
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
        gh.checks[fern_names.docs] = PENDING
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )

        def push_while_checks_finish() -> None:
            (docs,) = gh.on(fern_names.docs)
            docs.move_head(
                fern_remote.push_branch(fern_names.docs, {DOCS_PAGE: "Unchecked.\n"})
            )
            gh.checks[fern_names.docs] = PASSING

        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                "live",
                *extra,
                wait=push_while_checks_finish,
            )
            == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (OPEN, OPEN)

    def test_content_breach_opens_the_docs_pr_unmerged(
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
        breach = EDITED_PAGE + "<Callout>New.</Callout>\n"
        extra = docs_args(checkpatch, fern_files, tmp_path, breach, (older.number,))
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (OPEN, OPEN)
        assert (
            f"## Do not merge\n\n- `{DOCS_PAGE}`: adds or changes a JSX tag or MDX expression\n"
            in docs.body
        )

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
        extra = docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE, (older.number,)
        )
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (MERGED, OPEN)
        # The re-run's docs chain skips the merged PR, so it files no docs.
        state = read_state(fernstate, gh, tmp_path)
        assert file_fern(filefern, gh, fern_remote, tmp_path, state, "live") == 0
        assert (older.state, older.comments) == (
            CLOSED,
            [f"Superseded by #{docs.number}"],
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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert docs.state == OPEN
        assert f"## Do not merge\n\n- {filefern.HOLDBACKS_ACTIVE}\n" in docs.body

    def test_open_pr_this_run_has_nothing_to_file_for_is_never_merged(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        tmp_path: Path,
    ) -> None:
        sha = fern_remote.push_branch(
            fern_names.docs, {DOCS_PAGE: "pushed since the last run"}
        )
        earlier = gh.add_pr(fern_names.docs, OPEN, sha=sha)
        gh.checks[fern_names.docs] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        assert file_fern(filefern, gh, fern_remote, tmp_path, state, "live") == 1
        assert (earlier.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"docs_pr": earlier.url},
        )

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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "test", *extra) == 0
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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "test", *extra) == 1
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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "test", *extra) == 0
        )
        assert gh.on(fern_names.docs_rehearsal)[0].state == CLOSED

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
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        assert (
            gh.on(fern_names.changelog),
            fern_remote.has_branch(fern_names.changelog),
        ) == ([], False)

    def test_page_changed_in_fern_since_the_review_is_not_filed(
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
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        fern_remote.commit_to_base(
            "docs: another team's edit", {DOCS_PAGE: "Rewritten.\n"}
        )
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        assert (gh.on(fern_names.docs), fern_remote.has_branch(fern_names.docs)) == (
            [],
            False,
        )
