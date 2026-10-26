"""The docs review range starts after the newest documented release, never too far back."""

from __future__ import annotations

from types import ModuleType

import pytest

TAGS = [
    "band-sdk-v6.8.0",
    "band-sdk-v6.9.0",
    "band-sdk-v6.10.0",
    "band-sdk-v6.11.0",
    "band-sdk-v6.12.0",
    "band-sdk-v6.13.0",
    "band-mcp-v0.9.0",
    "band-sdk-v3.2.0-v3.2.1",
]
TAG = "band-sdk-v6.13.0"


def prs(docsrange: ModuleType, *branches: tuple[int, str]) -> list[object]:
    return [
        docsrange.DocsPr(number=number, head_ref_name=name) for number, name in branches
    ]


class TestDocsRange:
    def test_baseline_is_the_newest_documented_release_by_version(
        self, docsrange: ModuleType
    ) -> None:
        merged = prs(
            docsrange,
            (1, "docs/python-sdk-band-sdk-v6.11.0"),
            (2, "docs/python-sdk-band-sdk-v6.12.0"),
            (3, "docs/python-sdk-band-sdk-v6.10.0"),
        )
        assert docsrange.docs_baseline(TAG, TAGS, merged) == "band-sdk-v6.12.0"

    def test_versions_compare_numerically(self, docsrange: ModuleType) -> None:
        tags = ["band-sdk-v6.9.0", "band-sdk-v6.10.0", "band-sdk-v6.11.0"]
        merged = prs(
            docsrange,
            (1, "docs/python-sdk-band-sdk-v6.9.0"),
            (2, "docs/python-sdk-band-sdk-v6.10.0"),
        )
        assert (
            docsrange.docs_baseline("band-sdk-v6.11.0", tags, merged)
            == "band-sdk-v6.10.0"
        )

    @pytest.mark.parametrize(
        "merged",
        [[], [(1, "docs/python-sdk-band-sdk-v6.8.0")]],
        ids=["nothing-merged", "merged-before-the-floor"],
    )
    def test_baseline_never_reaches_further_back_than_the_floor(
        self, docsrange: ModuleType, merged: list[tuple[int, str]]
    ) -> None:
        floor = "band-sdk-v6.10.0"  # the third release before TAG
        assert docsrange.docs_baseline(TAG, TAGS, prs(docsrange, *merged)) == floor

    def test_short_history_floors_at_the_first_release(
        self, docsrange: ModuleType
    ) -> None:
        tags = ["band-sdk-v1.0.0", "band-sdk-v1.1.0"]
        assert docsrange.docs_baseline("band-sdk-v1.1.0", tags, []) == "band-sdk-v1.0.0"

    def test_supersede_holds_only_releases_inside_the_range(
        self, docsrange: ModuleType
    ) -> None:
        open_prs = prs(
            docsrange,
            (10, "docs/python-sdk-band-sdk-v6.11.0"),
            (11, "docs/python-sdk-band-sdk-v6.12.0"),
            (12, "docs/python-sdk-band-sdk-v6.10.0"),
            (13, "docs/python-sdk-band-sdk-v6.14.0"),
            (14, "docs/python-sdk-band-sdk-v6.13.0"),
        )
        assert docsrange.superseded(TAG, "band-sdk-v6.10.0", open_prs) == [10, 11]

    @pytest.mark.parametrize(
        "branch",
        [
            "docs/python-sdk-band-sdk-v6.12.0-test",
            "docs/python-sdk-band-sdk-v6.12.0-rehearsal",
            "docs/python-sdk-band-sdk-v3.2.0-v3.2.1",
            "docs/typescript-sdk-band-sdk-v6.12.0",
        ],
    )
    def test_other_branch_names_are_ignored(
        self, docsrange: ModuleType, branch: str
    ) -> None:
        found = prs(docsrange, (20, branch))
        assert (
            docsrange.docs_baseline(TAG, TAGS, found),
            docsrange.superseded(TAG, "band-sdk-v6.10.0", found),
        ) == ("band-sdk-v6.10.0", [])
