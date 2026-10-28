"""Select recent unannounced releases in publication order."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import ModuleType

import pytest

from tests.skills.releasedocs.ghpeer import MERGED, FakeGh

NOW = datetime(2026, 10, 28, tzinfo=UTC)
RUN_ID = 123
SDK = "band-ai/band-sdk-python"


class TestReleaseScan:
    @pytest.mark.parametrize(
        ("marker_run", "expected"),
        [(RUN_ID, "band-sdk-v6.1.0"), (RUN_ID + 1, "")],
        ids=["backlog", "nothing-new"],
    )
    def test_selects_oldest_unannounced_release_or_nothing(
        self,
        scanreleases: ModuleType,
        releasedata: ModuleType,
        gh: FakeGh,
        marker_run: int,
        expected: str,
    ) -> None:
        gh.sdk_repo = SDK
        recent = {"published_at": NOW.isoformat(), "draft": False, "prerelease": False}
        gh.releases = [
            recent | {"tag_name": "band-sdk-v6.1.2"},
            recent
            | {
                "tag_name": "band-sdk-v6.1.1",
                "published_at": (NOW - timedelta(days=1)).isoformat(),
            },
            recent
            | {
                "tag_name": "band-sdk-v6.1.0",
                "published_at": (
                    NOW - timedelta(days=releasedata.LOOKBACK_DAYS)
                ).isoformat(),
            },
            recent | {"tag_name": "band-sdk-v6.0.1"},
            recent | {"tag_name": "band-sdk-v6.0.0"},
            recent
            | {
                "tag_name": "band-sdk-v5.0.0",
                "published_at": (
                    NOW - timedelta(days=releasedata.LOOKBACK_DAYS, seconds=1)
                ).isoformat(),
            },
            recent | {"tag_name": "band-sdk-v5.1.0", "draft": True},
            recent | {"tag_name": "band-sdk-v5.2.0", "prerelease": True},
            recent | {"tag_name": "band-mcp-v1.0.0"},
            recent | {"tag_name": "band-sdk-v6.2.0rc1"},
        ]
        gh.add_marker(
            releasedata.ANNOUNCED_MARKER_PREFIX + "band-sdk-v6.0.1", RUN_ID + 1
        )
        gh.add_pr(
            releasedata.fern_branch(
                releasedata.CHANGELOG_BRANCH_PREFIX, "band-sdk-v6.0.0", rehearsal=False
            ),
            MERGED,
        )
        for version in ("6.1.0", "6.1.1", "6.1.2"):
            gh.add_marker(
                releasedata.ANNOUNCED_MARKER_PREFIX
                + releasedata.SDK_TAG_PREFIX
                + version,
                marker_run,
            )
        assert scanreleases.scan(SDK, gh.repo, RUN_ID, now=NOW, github=gh) == expected

    def test_empty_release_listing_selects_nothing(
        self,
        scanreleases: ModuleType,
        gh: FakeGh,
    ) -> None:
        gh.sdk_repo = SDK
        assert scanreleases.scan(SDK, gh.repo, RUN_ID, now=NOW, github=gh) == ""
