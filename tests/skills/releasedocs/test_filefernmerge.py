"""A live filing merges only this run's checked content, once every check passes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.fernremote import MAINTAINER, FernRemote, run_git
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
    MERGED,
    OPEN,
    PARTIAL,
    PASSING,
    PENDING,
    FakeGh,
    check_run,
)
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import (
    DOCS_PAGE,
    RELEASE_DATE,
    RELEASE_TAG,
    SEND_RETRIES,
)


class TestFernMerge:
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
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
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

    @pytest.mark.parametrize(
        ("checks", "expected_state", "exit_code"),
        [
            (PASSING, MERGED, 0),
            (FAILING, OPEN, 1),
            (PENDING, OPEN, 1),
        ],
    )
    def test_read_token_decides_merge_while_write_token_cannot_read_checks(
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
        expected_state: str,
        exit_code: int,
    ) -> None:
        gh.allow_checks = False
        gh.checks[fern_names.changelog] = checks
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                filefern.Mode.LIVE,
                *extra,
                checks=gh.read_checks,
            )
            == exit_code
        )
        (pr,) = gh.on(fern_names.changelog)
        assert (pr.state, read_outputs(tmp_path / "outputs")) == (
            expected_state,
            {"changelog_pr": pr.url, "docs_pr": ""},
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
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        commit = run_git(
            "-C",
            str(fern_remote.bare),
            "log",
            "-1",
            "--format=%an <%ae>%n%B",
            fern_names.changelog,
        )
        name, email = filefern.COMMITTER
        assert (
            commit.strip()
            == f"{name} <{email}>\ndocs(changelog): Python SDK {RELEASE_TAG}"
        )

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
            fern_names.date_file: "filed by an earlier run",
            "fern/docs.yml": "x",
        }
        earlier = gh.add_pr(
            fern_names.changelog,
            OPEN,
            sha=fern_remote.push_branch(
                fern_names.changelog, pushed, filefern.COMMITTER
            ),
        )
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        items = [make_item(*SEND_RETRIES)]
        extra = notes_args(make_notes, items, tmp_path)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
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
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        assert (
            gh.on(fern_names.changelog),
            fern_remote.has_branch(fern_names.changelog),
        ) == ([merged], False)
        assert read_outputs(tmp_path / "outputs") == {
            "changelog_pr": merged.url,
            "docs_pr": "",
        }

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
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        (pr,) = gh.on(fern_names.changelog)
        assert (pr.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": pr.url, "docs_pr": ""},
        )

    @pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
    def test_neutral_or_skipped_check_still_merges(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        conclusion: str,
    ) -> None:
        gh.checks[fern_names.changelog] = [
            check_run("completed", "success"),
            check_run("completed", conclusion),
        ]
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 0
        )
        assert gh.on(fern_names.changelog)[0].state == MERGED

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
                filefern.Mode.LIVE,
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
                filefern.Mode.LIVE,
                *extra,
                wait=push_while_checks_finish,
            )
            == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (OPEN, OPEN)

    @pytest.mark.parametrize(
        "author",
        [lambda filefern: MAINTAINER, lambda filefern: filefern.COMMITTER],
        ids=["filed-by-hand", "bot-commit-amended-by-hand"],
    )
    def test_open_pr_filed_by_hand_is_never_taken_over(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        author: Callable[[ModuleType], tuple[str, str]],
    ) -> None:
        sha = fern_remote.push_branch(
            fern_names.changelog,
            {fern_names.date_file: "a maintainer's entry"},
            committer=MAINTAINER,
            author=author(filefern),
        )
        hand_filed = gh.add_pr(fern_names.changelog, OPEN, sha=sha)
        gh.checks[fern_names.changelog] = PASSING
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        summary = tmp_path / "summary.md"
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        extra += ["--summary", str(summary)]
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        assert (hand_filed.state, fern_remote.head(fern_names.changelog)) == (OPEN, sha)
        assert summary.read_text("utf-8").splitlines()[0] == (
            f"- Fern changelog: {filefern.Outcome.HAND_FILED} {hand_filed.url}"
        )
        assert read_outputs(tmp_path / "outputs") == {
            "changelog_pr": hand_filed.url,
            "docs_pr": "",
        }
