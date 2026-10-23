"""pytest-split groups with a shared seed partition the selected tests."""

from __future__ import annotations

from typing import cast

import pytest


class TestSplitGroupsPartition:
    def test_two_groups_cover_each_node_once(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            "\n".join(f"def test_{index}():\n    pass\n" for index in range(4))
        )

        def collect(*args: str) -> set[str]:
            recorder = pytester.inline_run(
                *args,
                "--splitting-algorithm",
                "least_duration",
                "--randomly-seed",
                "1",
                "--collect-only",
                "-q",
            )
            assert recorder.ret == pytest.ExitCode.OK
            session = cast(
                pytest.Session, recorder.getcall("pytest_collection_finish").session
            )
            return {item.nodeid for item in session.items}

        group1 = collect("--splits", "2", "--group", "1")
        group2 = collect("--splits", "2", "--group", "2")
        full = collect()
        assert group1.isdisjoint(group2)
        assert group1 | group2 == full
        assert group1 and group2
