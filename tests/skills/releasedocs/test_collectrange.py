"""The collector gathers a band-sdk release range into ``release-prs.json``."""

from __future__ import annotations

from types import ModuleType

import pytest

from tests.skills.releasedocs.gitrepo import ThrowawayRepo
from tests.skills.releasedocs.samples import (
    PREVIOUS_TAG,
    PUBLISHED_RELEASE,
    RELEASE_TAG,
)

# More files than ``gh pr view --json files`` returns, all internal but the last.
LARGE_COMMIT_FILES = {
    f"tests/generated/test_{index}.py": "" for index in range(120)
} | {"src/band/late change.py": ""}
RELEASE_PR_BODY = "release-please release notes."
# The only PRs the collector may look up; internal-only PRs (12, 14) and the
# band-mcp PR (11) are absent, so fetching one raises.
PR_TABLE = {
    10: {"title": "Earlier", "body": "Earlier work.", "labels": []},
    13: {"title": "Reshape send", "body": "Send keeps order.", "labels": []},
    15: {
        "title": "Release 6.1.0",
        "body": RELEASE_PR_BODY,
        "labels": [{"name": "autorelease: tagged"}],
    },
    16: {"title": "Rename tools", "body": "Tools are prefixed.", "labels": []},
    17: {"title": "Config shape", "body": "New config.", "labels": []},
    18: {"title": "Many files", "body": "Generated.", "labels": []},
}


@pytest.fixture
def history(repo: ThrowawayRepo) -> ThrowawayRepo:
    """A history with interleaved band-mcp tags, a junk tag, and every PR shape."""
    repo.tag("band-sdk-v6.0.0")
    repo.commit("feat: earlier release work (#10)", {"src/band/early.py": ""})
    repo.tag(PREVIOUS_TAG)
    repo.commit("feat: band-mcp only (#11)", {"packages/band-mcp/x.py": ""})
    repo.tag("band-mcp-v0.2.0")
    repo.commit("test: cover retry (#12)", {"tests/test_retry.py": ""})
    repo.tag("band-sdk-v6.0.1-v6.0.2")
    repo.commit("refactor: reshape send (#13)", {"src/band/send.py": ""})
    repo.commit(
        "fix: mcp and tests (#14)", {"packages/band-mcp/y.py": "", "tests/t.py": ""}
    )
    repo.commit("chore(main): release band-sdk 6.1.0 (#15)", {"CHANGELOG.md": "6.1.0"})
    repo.commit("ci: tweak a workflow", {".github/workflows/x.yml": ""})
    repo.commit("docs: fix the readme", {"README.md": "fixed"})
    repo.commit("feat!: rename platform tools (#16)", {"src/band/tools.py": ""})
    repo.commit(
        "feat: new config shape (#17)",
        {"src/band/config.py": ""},
        body="BREAKING CHANGE: the old config keys are gone.",
    )
    repo.commit("fix: many generated files (#18)", LARGE_COMMIT_FILES)
    repo.tag(RELEASE_TAG)
    return repo


def collected(
    collectprs: ModuleType, repo: ThrowawayRepo, since: str | None = None
) -> object:
    release = collectprs.Release.model_validate(
        PUBLISHED_RELEASE | {"publishedAt": "2026-10-21T01:30:00+02:00"}
    )
    return collectprs.collect(
        repo.root,
        RELEASE_TAG,
        release=release,
        pr_meta=lambda number: collectprs.PrMeta.model_validate(PR_TABLE[number]),
        since=since,
    )


class TestReleaseRange:
    def test_range_starts_at_the_previous_strict_sdk_tag(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        assert collected(collectprs, history).previous_tag == PREVIOUS_TAG

    def test_user_facing_prs_are_kept_with_their_type(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        prs = collected(collectprs, history).pull_requests
        assert [(pr.number, pr.type) for pr in prs] == [
            (13, "refactor"),
            (16, "feat"),
            (17, "feat"),
            (18, "fix"),
        ]

    def test_internal_and_release_prs_are_listed_by_number_only(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        result = collected(collectprs, history)
        assert result.excluded_internal == [12, 14, 15]
        assert RELEASE_PR_BODY not in result.model_dump_json()

    def test_breaking_comes_from_the_bang_or_the_footer(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        prs = collected(collectprs, history).pull_requests
        assert {pr.number: pr.breaking for pr in prs} == {
            13: False,
            16: True,
            17: True,
            18: False,
        }

    def test_loose_commits_keep_only_user_facing_ones(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        commits = collected(collectprs, history).loose_commits
        assert [(c.subject, c.files) for c in commits] == [
            ("docs: fix the readme", ["README.md"])
        ]

    def test_large_commit_is_classified_from_every_file(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        prs = {pr.number: pr for pr in collected(collectprs, history).pull_requests}
        assert sorted(prs[18].files) == sorted(LARGE_COMMIT_FILES)

    def test_exclude_paths_come_from_the_tagged_config(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        (history.root / collectprs.RELEASE_PLEASE_CONFIG).write_text(
            '{"packages": {".": {}}}', encoding="utf-8"
        )
        result = collected(collectprs, history)
        assert 11 not in [pr.number for pr in result.pull_requests]
        assert result.excluded_internal == [12, 14, 15]

    def test_since_spans_every_release_after_the_given_tag(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        result = collected(collectprs, history, since="band-sdk-v6.0.0")
        assert (result.previous_tag, [pr.number for pr in result.pull_requests]) == (
            "band-sdk-v6.0.0",
            [10, 13, 16, 17, 18],
        )

    def test_release_date_is_the_utc_publish_date(
        self, collectprs: ModuleType, history: ThrowawayRepo
    ) -> None:
        assert collected(collectprs, history).release_date == "2026-10-20"

    def test_release_without_an_earlier_sdk_tag_is_refused(
        self, collectprs: ModuleType, repo: ThrowawayRepo
    ) -> None:
        repo.commit("feat: first release (#1)", {"src/band/first.py": ""})
        repo.tag(RELEASE_TAG)
        with pytest.raises(ValueError, match="precedes"):
            collectprs.previous_sdk_tag(repo.root, RELEASE_TAG)
