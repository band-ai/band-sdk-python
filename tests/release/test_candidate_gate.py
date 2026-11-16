"""Release metadata enforcement covers PRs and merge queue candidates only."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


class TestCandidateGate:
    @pytest.mark.parametrize(
        "event,head,queue,expected",
        [
            ("pull_request", "feature/work", "", 0),
            ("pull_request", "release-please--branches--main", "", 1),
            (
                "merge_group",
                "release-please--branches--main",
                "gh-readonly-queue/main/pr-123-abc",
                1,
            ),
            ("merge_group", "feature/work", "gh-readonly-queue/main/pr-123-abc", 0),
            ("merge_group", "feature/work", "unexpected", 1),
        ],
    )
    def test_mismatch_blocks_only_release_candidates(
        self,
        release_tree: Path,
        run_candidate_gate: Callable[..., subprocess.CompletedProcess[str]],
        event: str,
        head: str,
        queue: str,
        expected: int,
    ) -> None:
        (release_tree / "pyproject.toml").write_text('[project]\nversion="9.2.0"\n')
        result = run_candidate_gate(event, head, queue)
        assert result.returncode == expected, result.stderr

    @pytest.mark.parametrize(
        "event,queue",
        [("pull_request", ""), ("merge_group", "gh-readonly-queue/main/pr-123-abc")],
    )
    def test_advancing_shared_candidate_is_accepted(
        self,
        run_candidate_gate: Callable[..., subprocess.CompletedProcess[str]],
        event: str,
        queue: str,
    ) -> None:
        result = run_candidate_gate(event, "release-please--branches--main", queue)
        assert result.returncode == 0, result.stderr
