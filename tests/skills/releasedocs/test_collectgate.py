"""The collector stops, without failing, on releases that must not be announced."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.ghpeer import MERGED, FakeGh
from tests.skills.releasedocs.gitrepo import ThrowawayRepo
from tests.skills.releasedocs.samples import (
    PREVIOUS_TAG,
    PUBLISHED_RELEASE,
    RELEASE_TAG,
)

# The only PR the gate may look up; any other lookup raises.
PR_TABLE = {20: {"title": "Retry sends", "body": "", "labels": []}}
RUN_ID = 123
OTHER_RUN_ID = RUN_ID + 1


def release_of(collectprs: ModuleType, fields: dict[str, object] | None) -> object:
    return None if fields is None else collectprs.Release.model_validate(fields)


def gate(
    collectprs: ModuleType,
    repo: ThrowawayRepo,
    outputs: Path,
    fields: dict[str, object] | None,
    gh: FakeGh,
    *,
    live: bool,
) -> object:
    return collectprs.run(
        repo.root,
        RELEASE_TAG,
        live=live,
        since=None,
        outputs=outputs,
        release_lookup=lambda _tag: release_of(collectprs, fields),
        caller=gh.repo,
        run_id=RUN_ID,
        github=gh,
        pr_meta=lambda number: collectprs.PrMeta.model_validate(PR_TABLE[number]),
    )


class TestReleaseGate:
    @pytest.mark.parametrize(
        ("fields", "reason"),
        [
            (None, "NOT_VISIBLE"),
            (PUBLISHED_RELEASE | {"isDraft": True}, "DRAFT"),
            (PUBLISHED_RELEASE | {"isPrerelease": True}, "PRERELEASE"),
        ],
    )
    def test_unannounceable_release_stops_with_its_reason(
        self,
        collectprs: ModuleType,
        tagged: ThrowawayRepo,
        tmp_path: Path,
        fields: dict[str, object] | None,
        gh: FakeGh,
        reason: str,
    ) -> None:
        outputs = tmp_path / "outputs"
        assert gate(collectprs, tagged, outputs, fields, gh, live=True) is None
        assert outputs.read_text(encoding="utf-8") == (
            f"proceed=false\nhas_changes=false\nreason={collectprs.StopReason[reason]}\n"
        )

    def test_live_release_stops_on_another_runs_marker(
        self,
        collectprs: ModuleType,
        releasedata: ModuleType,
        tagged: ThrowawayRepo,
        tmp_path: Path,
        gh: FakeGh,
    ) -> None:
        gh.add_marker(releasedata.ANNOUNCED_MARKER_PREFIX + RELEASE_TAG, OTHER_RUN_ID)
        outputs = tmp_path / "outputs"
        assert (
            gate(collectprs, tagged, outputs, PUBLISHED_RELEASE, gh, live=True) is None
        )
        assert (
            outputs.read_text()
            == f"proceed=false\nhas_changes=false\nreason={collectprs.StopReason.ANNOUNCED}\n"
        )

    def test_live_release_stops_on_a_merged_changelog_without_marker(
        self,
        collectprs: ModuleType,
        releasedata: ModuleType,
        tagged: ThrowawayRepo,
        tmp_path: Path,
        gh: FakeGh,
    ) -> None:
        gh.add_pr(
            releasedata.fern_branch(
                releasedata.CHANGELOG_BRANCH_PREFIX, RELEASE_TAG, rehearsal=False
            ),
            MERGED,
        )
        outputs = tmp_path / "outputs"
        assert (
            gate(collectprs, tagged, outputs, PUBLISHED_RELEASE, gh, live=True) is None
        )
        assert (
            outputs.read_text()
            == f"proceed=false\nhas_changes=false\nreason={collectprs.StopReason.ANNOUNCED}\n"
        )

    @pytest.mark.parametrize(
        ("expired", "marker_run", "live"),
        [
            (False, RUN_ID, True),
            (True, OTHER_RUN_ID, True),
            (False, OTHER_RUN_ID, False),
        ],
    )
    def test_retry_expired_marker_and_rehearsals_proceed(
        self,
        collectprs: ModuleType,
        releasedata: ModuleType,
        tagged: ThrowawayRepo,
        tmp_path: Path,
        gh: FakeGh,
        expired: bool,
        marker_run: int,
        live: bool,
    ) -> None:
        gh.add_marker(
            releasedata.ANNOUNCED_MARKER_PREFIX + RELEASE_TAG,
            marker_run,
            expired=expired,
        )
        outputs = tmp_path / "outputs"
        collected = gate(collectprs, tagged, outputs, PUBLISHED_RELEASE, gh, live=live)
        assert [pr.number for pr in collected.pull_requests] == [20]
        assert outputs.read_text() == "proceed=true\nhas_changes=true\nreason=\n"

    def test_internal_only_release_has_no_changes(
        self, collectprs: ModuleType, repo: ThrowawayRepo, tmp_path: Path, gh: FakeGh
    ) -> None:
        repo.tag(PREVIOUS_TAG)
        repo.commit("test: more coverage (#21)", {"tests/test_send.py": ""})
        repo.commit("ci: faster lanes", {".github/workflows/ci.yml": ""})
        repo.tag(RELEASE_TAG)
        outputs = tmp_path / "outputs"
        collected = gate(collectprs, repo, outputs, PUBLISHED_RELEASE, gh, live=True)
        assert collected.excluded_internal == [21]
        assert outputs.read_text(encoding="utf-8") == (
            "proceed=true\nhas_changes=false\n"
            f"reason={collectprs.StopReason.NO_CHANGES}\n"
        )

    def test_non_release_tag_is_refused_before_collecting(
        self, collectprs: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert collectprs.main(["v6.13.0"]) == 2
        assert "is not a band-sdk-vX.Y.Z tag" in caplog.text
