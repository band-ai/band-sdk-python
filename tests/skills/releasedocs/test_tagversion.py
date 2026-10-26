"""``tag_version`` accepts only strict band-sdk release tags."""

from __future__ import annotations

from types import ModuleType

import pytest


class TestTagVersion:
    @pytest.mark.parametrize(
        ("tag", "version"),
        [("band-sdk-v6.10.0", (6, 10, 0)), ("band-sdk-v0.0.1", (0, 0, 1))],
    )
    def test_release_tag_parses(
        self, collectprs: ModuleType, tag: str, version: tuple[int, int, int]
    ) -> None:
        assert collectprs.tag_version(tag) == version

    @pytest.mark.parametrize(
        "tag",
        [
            "band-sdk-v3.2.0-v3.2.1",
            "band-sdk-v6.1.0-rc.1",
            "band-sdk-v6.1",
            "band-mcp-v6.1.0",
            "v6.1.0",
        ],
    )
    def test_other_tags_are_not_releases(
        self, collectprs: ModuleType, tag: str
    ) -> None:
        assert collectprs.tag_version(tag) is None
