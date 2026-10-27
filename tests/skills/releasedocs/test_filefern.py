"""Filing pushes checked edits, merges only on passing checks, and never repeats a merged part."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

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
    HELD_PR,
    LEDGER,
    RECONNECT,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASED_LEDGER,
    SEND_RETRIES,
    TOKEN_REDACTION,
)

CHANGELOG = f"changelog/python-sdk-{RELEASE_TAG}"
DOCS = f"docs/python-sdk-{RELEASE_TAG}"
OLDER_DOCS = "docs/python-sdk-band-sdk-v6.0.1"
DATE_FILE = f"fern/changelog/sdks/{RELEASE_DATE}.md"
EDITED_PAGE = DOCS_PAGE_TEXT.replace("Agent.create()", "Agent.start()")
REPORT = {
    "summary": "Agent creation now uses `Agent.start()`.",
    "pages_reviewed": [{"page": DOCS_PAGE, "reason": "Renamed", "sources": [812]}],
    "claims": [],
    "also_stale": [],
    "pages_missing": [],
}


def read_state(fernstate: ModuleType, gh: FakeGh, tmp_path: Path, *extra: str) -> Path:
    out = tmp_path / "state"
    assert fernstate.main([RELEASE_TAG, "--out", str(out), *extra], gh=gh) == 0
    return out / "state.json"


def docs_args(
    checkpatch: ModuleType,
    fern_files: dict[str, str | bytes],
    tmp_path: Path,
    page: str,
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
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    docs_range.write_text(
        json.dumps({"baseline": "band-sdk-v6.0.0", "supersede": [1]}), "utf-8"
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
    state: Path,
    mode: str,
    *extra: str,
) -> int:
    fern = remote.clone(tmp_path / "fern-write")
    argv = [RELEASE_TAG, "--fern", str(fern), "--state", str(state), "--mode", mode]
    argv += ["--outputs", str(tmp_path / "outputs"), *extra]
    return filefern.main(argv, gh=gh, poll=filefern.Poll(2, lambda: None))


class TestFileFern:
    def test_live_files_merges_both_and_closes_the_superseded(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        renderentry: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(OLDER_DOCS, OPEN)
        gh.checks |= {CHANGELOG: PASSING, DOCS: PASSING}
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path) + docs_args(
            checkpatch, fern_files, tmp_path, EDITED_PAGE
        )
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        changelog, docs = gh.on(CHANGELOG)[0], gh.on(DOCS)[0]
        assert (changelog.state, docs.state, older.state) == (MERGED, MERGED, CLOSED)
        assert older.comments == [f"Superseded by #{docs.number}"]
        assert fern_remote.file_on(
            CHANGELOG, DATE_FILE
        ) == renderentry.render_standalone(make_notes(items))
        assert fern_remote.file_on(DOCS, DOCS_PAGE) == EDITED_PAGE
        assert read_outputs(tmp_path / "outputs") == {
            "changelog_pr": changelog.url,
            "docs_pr": docs.url,
        }

    def test_commits_carry_the_release_bot_and_no_ai_attribution(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        gh.checks[CHANGELOG] = PASSING
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
            CHANGELOG,
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
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        fern_remote.commit_to_base(
            "chore: hold room tasks", {releasedata.HELD_ITEMS: LEDGER}
        )
        gh.checks[CHANGELOG] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [
            make_item(*SEND_RETRIES),
            make_item(*RECONNECT, sources=(HELD_PR,)),
            make_item(*TOKEN_REDACTION),
        ]
        extra = notes_args(make_notes, items, tmp_path, *held)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        body = gh.on(CHANGELOG)[0].body
        holdbacks_section = body.split("## Holdbacks\n\n", 1)[1]
        assert holdbacks_section == (
            "- **Reconnect** — Rooms rejoin after a drop. (sdk-901-room-tasks: Not public yet.)\n"
            f"- **Token Redaction** — Keys never reach logs. ({holdbacks.FLAGGED_BY_REVIEW})\n"
        )
        assert "Reconnect" not in fern_remote.file_on(CHANGELOG, DATE_FILE)

    def test_open_pr_is_refiled_from_this_runs_content_before_merging(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        renderentry: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        pushed = {DATE_FILE: "pushed since the last run", "fern/docs.yml": "x"}
        earlier = gh.add_pr(
            CHANGELOG, OPEN, sha=fern_remote.push_branch(CHANGELOG, pushed)
        )
        gh.checks[CHANGELOG] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        assert (gh.on(CHANGELOG), earlier.state) == ([earlier], MERGED)
        assert earlier.sha == fern_remote.head(CHANGELOG)
        assert fern_remote.file_on(
            CHANGELOG, DATE_FILE
        ) == renderentry.render_standalone(make_notes(items))
        assert run_git(
            "-C",
            str(fern_remote.bare),
            "diff",
            "--name-only",
            f"{fern_remote.base}...{CHANGELOG}",
        ).split() == [DATE_FILE]

    def test_merged_pr_is_not_filed_again(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        merged = gh.add_pr(CHANGELOG, MERGED)
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 0
        )
        assert (gh.on(CHANGELOG), fern_remote.has_branch(CHANGELOG)) == (
            [merged],
            False,
        )
        assert read_outputs(tmp_path / "outputs") == {"changelog_pr": merged.url}

    @pytest.mark.parametrize(
        "checks", [FAILING, PENDING, PARTIAL], ids=["failed", "unfinished", "partial"]
    )
    def test_pr_whose_checks_do_not_pass_stays_open_and_fails(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        checks: list[dict[str, str | None]],
    ) -> None:
        gh.checks[CHANGELOG] = checks
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (pr,) = gh.on(CHANGELOG)
        assert (pr.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": pr.url},
        )

    def test_content_breach_opens_the_docs_pr_unmerged(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(OLDER_DOCS, OPEN)
        gh.checks[DOCS] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        breach = EDITED_PAGE + "<Callout>New.</Callout>\n"
        extra = docs_args(checkpatch, fern_files, tmp_path, breach)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (docs,) = gh.on(DOCS)
        assert (docs.state, older.state) == (OPEN, OPEN)
        assert (
            f"## Do not merge\n\n- `{DOCS_PAGE}`: adds or changes a JSX tag or MDX expression\n"
            in docs.body
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
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        ledger: str,
        held: list[str],
    ) -> None:
        fern_remote.commit_to_base(
            "chore: update holdbacks", {releasedata.HELD_ITEMS: ledger}
        )
        gh.checks[DOCS] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        extra += ["--held", *held]
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        (docs,) = gh.on(DOCS)
        assert docs.state == OPEN
        assert f"## Do not merge\n\n- {filefern.HOLDBACKS_ACTIVE}\n" in docs.body

    def test_open_pr_this_run_has_nothing_to_file_for_is_never_merged(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        tmp_path: Path,
    ) -> None:
        sha = fern_remote.push_branch(DOCS, {DOCS_PAGE: "pushed since the last run"})
        earlier = gh.add_pr(DOCS, OPEN, sha=sha)
        gh.checks[DOCS] = PASSING
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
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        older = gh.add_pr(OLDER_DOCS, OPEN)
        gh.checks |= {f"{CHANGELOG}-rehearsal": PASSING, f"{DOCS}-rehearsal": PASSING}
        state = read_state(
            fernstate, gh, tmp_path, "--date", RELEASE_DATE, "--rehearsal"
        )
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        extra += docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "test", *extra) == 0
        )
        changelog, docs = (
            gh.on(f"{CHANGELOG}-rehearsal")[0],
            gh.on(f"{DOCS}-rehearsal")[0],
        )
        assert (changelog.state, docs.state, older.state) == (CLOSED, CLOSED, OPEN)
        assert (gh.on(CHANGELOG), gh.on(DOCS)) == ([], [])

    def test_rehearsal_closes_a_docs_pr_held_by_holdbacks(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        fern_files: dict[str, str | bytes],
        tmp_path: Path,
    ) -> None:
        gh.checks[f"{DOCS}-rehearsal"] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--rehearsal")
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        extra += ["--held", "0"]
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "test", *extra) == 0
        )
        assert gh.on(f"{DOCS}-rehearsal")[0].state == CLOSED

    def test_breaking_change_held_since_holdbacks_ran_is_not_filed(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        releasedata: ModuleType,
        gh: FakeGh,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        fern_remote.commit_to_base(
            "chore: hold room tasks", {releasedata.HELD_ITEMS: LEDGER}
        )
        gh.checks[CHANGELOG] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path, breaking=(HELD_PR,))
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, "live", *extra) == 1
        )
        assert (gh.on(CHANGELOG), fern_remote.has_branch(CHANGELOG)) == ([], False)

    def test_page_changed_in_fern_since_the_review_is_not_filed(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        checkpatch: ModuleType,
        gh: FakeGh,
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
        assert (gh.on(DOCS), fern_remote.has_branch(DOCS)) == ([], False)
