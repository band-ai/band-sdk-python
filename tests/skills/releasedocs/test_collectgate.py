"""The collector stops, without failing, on releases that must not be announced."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.gitrepo import ThrowawayRepo
from tests.skills.releasedocs.samples import (
    PREVIOUS_TAG,
    PUBLISHED_RELEASE,
    RELEASE_TAG,
)

# The only PR the gate may look up; any other lookup raises.
PR_TABLE = {20: {"title": "Retry sends", "body": "", "labels": []}}
ANNOUNCED = PUBLISHED_RELEASE | {"assets": [{"name": "release-notes.json"}]}


def release_of(collectprs: ModuleType, fields: dict[str, object] | None) -> object:
    return None if fields is None else collectprs.Release.model_validate(fields)


def gate(
    collectprs: ModuleType,
    repo: ThrowawayRepo,
    outputs: Path,
    fields: dict[str, object] | None,
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
        pr_meta=lambda number: collectprs.PrMeta.model_validate(PR_TABLE[number]),
    )


@pytest.fixture
def tagged(repo: ThrowawayRepo) -> ThrowawayRepo:
    repo.tag(PREVIOUS_TAG)
    repo.commit("feat: retry sends (#20)", {"src/band/send.py": ""})
    repo.tag(RELEASE_TAG)
    return repo


class TestReleaseGate:
    @pytest.mark.parametrize(
        ("fields", "reason"),
        [
            (None, "NOT_VISIBLE"),
            (PUBLISHED_RELEASE | {"isDraft": True}, "DRAFT"),
            (PUBLISHED_RELEASE | {"isPrerelease": True}, "PRERELEASE"),
            (ANNOUNCED, "ANNOUNCED"),
        ],
    )
    def test_unannounceable_release_stops_with_its_reason(
        self,
        collectprs: ModuleType,
        tagged: ThrowawayRepo,
        tmp_path: Path,
        fields: dict[str, object] | None,
        reason: str,
    ) -> None:
        outputs = tmp_path / "outputs"
        assert gate(collectprs, tagged, outputs, fields, live=True) is None
        assert outputs.read_text(encoding="utf-8") == (
            f"proceed=false\nhas_changes=false\nreason={collectprs.StopReason[reason]}\n"
        )

    def test_announced_release_proceeds_outside_live_mode(
        self, collectprs: ModuleType, tagged: ThrowawayRepo, tmp_path: Path
    ) -> None:
        outputs = tmp_path / "outputs"
        collected = gate(collectprs, tagged, outputs, ANNOUNCED, live=False)
        assert [pr.number for pr in collected.pull_requests] == [20]
        assert outputs.read_text(encoding="utf-8") == (
            "proceed=true\nhas_changes=true\nreason=\n"
        )

    def test_internal_only_release_has_no_changes(
        self, collectprs: ModuleType, repo: ThrowawayRepo, tmp_path: Path
    ) -> None:
        repo.tag(PREVIOUS_TAG)
        repo.commit("test: more coverage (#21)", {"tests/test_send.py": ""})
        repo.commit("ci: faster lanes", {".github/workflows/ci.yml": ""})
        repo.tag(RELEASE_TAG)
        outputs = tmp_path / "outputs"
        collected = gate(collectprs, repo, outputs, PUBLISHED_RELEASE, live=True)
        assert collected.excluded_internal == [21]
        assert outputs.read_text(encoding="utf-8") == (
            "proceed=true\nhas_changes=false\n"
            f"reason={collectprs.StopReason.NO_CHANGES}\n"
        )
