"""Release metadata enforcement covers PRs and merge queue candidates only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.paths import CI_SCRIPTS


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
        candidate_gate: Path,
        event: str,
        head: str,
        queue: str,
        expected: int,
    ) -> None:
        (release_tree / "pyproject.toml").write_text('[project]\nversion="9.2.0"\n')
        result = subprocess.run(
            ["bash", str(CI_SCRIPTS / "check-release-candidate.sh")],
            cwd=release_tree,
            env={
                **os.environ,
                "PATH": str(candidate_gate) + os.pathsep + os.environ["PATH"],
                "EVENT_NAME": event,
                "PR_HEAD": head,
                "FIXTURE_HEAD": head,
                "QUEUE_REF": queue,
                "REPO": "fixture/repo",
                "BASE_SHA": "HEAD",
            },
            capture_output=True,
            check=False,
        )
        assert result.returncode == expected, result.stderr
