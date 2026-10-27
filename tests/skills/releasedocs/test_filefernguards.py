"""A docs PR is never merged with unchecked, breaching, or stale content."""

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
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import DOCS_PAGE, RELEASE_DATE, SEND_RETRIES


class TestFernDocsGuards:
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
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        (docs,) = gh.on(fern_names.docs)
        assert (docs.state, older.state) == (OPEN, OPEN)
        assert (
            f"## Do not merge\n\n- `{DOCS_PAGE}`: adds or changes a JSX tag or MDX expression\n"
            in docs.body
        )

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
            fern_names.docs, {DOCS_PAGE: "filed by an earlier run"}, filefern.COMMITTER
        )
        earlier = gh.add_pr(fern_names.docs, OPEN, sha=sha)
        gh.checks[fern_names.docs] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        summary = tmp_path / "summary.md"
        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                filefern.Mode.LIVE,
                "--summary",
                str(summary),
            )
            == 1
        )
        assert (earlier.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": "", "docs_pr": earlier.url},
        )
        assert summary.read_text("utf-8").splitlines()[-1] == (
            f"- Fern docs: {filefern.Outcome.LEFT_OPEN} {earlier.url}"
        )

    @pytest.mark.parametrize(
        ("files", "removed"),
        [({DOCS_PAGE: "Rewritten.\n"}, ()), ({}, (DOCS_PAGE,))],
        ids=["rewritten", "deleted"],
    )
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
        caplog: pytest.LogCaptureFixture,
        files: dict[str, str | bytes],
        removed: tuple[str, ...],
    ) -> None:
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        fern_remote.commit_to_base("docs: another team's edit", files, removed)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        assert (gh.on(fern_names.docs), fern_remote.has_branch(fern_names.docs)) == (
            [],
            False,
        )
        # The log is public; the page path is not.
        assert DOCS_PAGE not in caplog.text

    def test_open_pr_a_failed_refile_leaves_is_still_linked(
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
        sha = fern_remote.push_branch(
            fern_names.docs, {DOCS_PAGE: "filed by an earlier run"}, filefern.COMMITTER
        )
        earlier = gh.add_pr(fern_names.docs, OPEN, sha=sha)
        state = read_state(fernstate, gh, tmp_path)
        summary = tmp_path / "summary.md"
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        extra += ["--summary", str(summary)]
        fern_remote.commit_to_base("docs: another team's edit", {DOCS_PAGE: "New.\n"})
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        assert (earlier.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": "", "docs_pr": earlier.url},
        )
        assert summary.read_text("utf-8").splitlines()[-1] == (
            f"- Fern docs: {filefern.Outcome.ERROR} {earlier.url}"
        )

    def test_open_changelog_pr_is_reported_when_every_item_is_now_held(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        tmp_path: Path,
    ) -> None:
        sha = fern_remote.push_branch(
            fern_names.changelog,
            {fern_names.date_file: "filed by an earlier run"},
            filefern.COMMITTER,
        )
        earlier = gh.add_pr(fern_names.changelog, OPEN, sha=sha)
        # Every item is held now, so the workflow passes no date and no notes.
        state = read_state(fernstate, gh, tmp_path)
        summary = tmp_path / "summary.md"
        assert (
            file_fern(
                filefern,
                gh,
                fern_remote,
                tmp_path,
                state,
                filefern.Mode.LIVE,
                "--summary",
                str(summary),
            )
            == 1
        )
        assert (earlier.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": earlier.url, "docs_pr": ""},
        )
        assert summary.read_text("utf-8").splitlines()[0] == (
            f"- Fern changelog: {filefern.Outcome.LEFT_OPEN} {earlier.url}"
        )

    def test_rejected_push_logs_no_remote_output(
        self,
        filefern: ModuleType,
        fernstate: ModuleType,
        gh: FakeGh,
        fern_names: FernNames,
        fern_remote: FernRemote,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fern_remote.reject_pushes(f"push declined: {fern_names.date_file}")
        state = read_state(fernstate, gh, tmp_path, "--date", RELEASE_DATE)
        extra = notes_args(make_notes, [make_item(*SEND_RETRIES)], tmp_path)
        assert (
            file_fern(
                filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE, *extra
            )
            == 1
        )
        assert "git push failed" in caplog.text
        # The log is public; the remote's message may name private files.
        assert fern_names.date_file not in caplog.text
