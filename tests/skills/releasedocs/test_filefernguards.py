"""A docs PR is never merged with unchecked, breaching, or stale content."""

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
    OPEN,
    PASSING,
    FakeGh,
)
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import DOCS_PAGE


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
            fern_names.docs, {DOCS_PAGE: "pushed since the last run"}
        )
        earlier = gh.add_pr(fern_names.docs, OPEN, sha=sha)
        gh.checks[fern_names.docs] = PASSING
        state = read_state(fernstate, gh, tmp_path)
        assert (
            file_fern(filefern, gh, fern_remote, tmp_path, state, filefern.Mode.LIVE)
            == 1
        )
        assert (earlier.state, read_outputs(tmp_path / "outputs")) == (
            OPEN,
            {"changelog_pr": "", "docs_pr": earlier.url},
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
    ) -> None:
        state = read_state(fernstate, gh, tmp_path)
        extra = docs_args(checkpatch, fern_files, tmp_path, EDITED_PAGE)
        fern_remote.commit_to_base(
            "docs: another team's edit", {DOCS_PAGE: "Rewritten.\n"}
        )
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
