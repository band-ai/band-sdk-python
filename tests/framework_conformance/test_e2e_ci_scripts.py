"""Behavioural guards for the e2e workflow's helper scripts (``.github/scripts``).

These scripts carry real branching logic — which lane×OS cells exist, whether a retry
has anything to retry, whether a roster is empty — but they only ever execute inside
the nightly E2E job, where a wrong branch surfaces as a confusing red run hours later
(or, worse, as a silently skipped step). Exercising them here puts that logic in the
ordinary unit suite on every PR.

Each test drives the *real* script as a subprocess, so it is a tripwire on the shipped
file rather than on a re-implementation of it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import CI_SCRIPTS, REPO_ROOT

_EMIT_LANE_MATRIX = CI_SCRIPTS / "emit-lane-matrix.py"
_READ_MENTIONS = CI_SCRIPTS / "read-integrations-mentions.sh"
_ROSTER = Path(".github") / "integrations-team.txt"

requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="no bash on this platform"
)


def _emit_lane_matrix(lane: str, os_id: str) -> subprocess.CompletedProcess[str]:
    """Run the lane-matrix emitter for one dispatch selection."""
    return subprocess.run(
        [sys.executable, str(_EMIT_LANE_MATRIX)],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT), "SELECTED_LANE": lane, "SELECTED_OS": os_id},
        capture_output=True,
        text=True,
    )


def test_lane_matrix_rejects_a_selection_with_no_runnable_cell() -> None:
    """A Linux-only lane asked for on Windows must fail, not emit an empty matrix.

    Both halves are offered by the dispatch dropdowns, so the pair is reachable by
    hand. Emitting ``include: []`` instead would leave the downstream
    ``EXPECTED_LANES`` blank, trip ``merge-scorecard.sh``'s required-variable guard,
    and report a red digest — blaming the suite for an impossible request.
    """
    result = _emit_lane_matrix("letta", "windows")

    assert result.returncode != 0
    assert "no runnable cell" in result.stderr
    # Names the actual remedy, not just the rejection.
    assert "letta" in result.stderr and "ubuntu" in result.stderr


def test_lane_matrix_emits_the_linux_only_lane_on_its_own_os() -> None:
    """The guard above rejects only the empty pair, not the lane itself."""
    result = _emit_lane_matrix("letta", "ubuntu")

    assert result.returncode == 0
    assert '"lane": "letta"' in result.stdout
    assert "windows" not in result.stdout


def test_lane_matrix_full_selection_spans_both_operating_systems() -> None:
    """The default nightly selection still fans out over the whole matrix."""
    result = _emit_lane_matrix("all", "all")

    assert result.returncode == 0
    assert "ubuntu-latest" in result.stdout
    assert "windows-latest" in result.stdout


def _read_mentions(
    tmp_path: Path, roster: str | None
) -> subprocess.CompletedProcess[str]:
    """Run the mentions reader against a throwaway roster (``None`` = no file)."""
    script = tmp_path / _READ_MENTIONS.name
    shutil.copy(_READ_MENTIONS, script)
    if roster is not None:
        (tmp_path / _ROSTER).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / _ROSTER).write_text(roster)
    return subprocess.run(
        ["bash", script.name],
        cwd=tmp_path,
        env={"GITHUB_OUTPUT": str(tmp_path / "out.txt")},
        capture_output=True,
        text=True,
    )


@requires_bash
@pytest.mark.parametrize(
    ("roster", "expected"),
    [
        ("# only comments\n\n", "has no usernames"),
        (None, "is missing"),
    ],
    ids=["empty-roster", "absent-roster"],
)
def test_mentions_reader_diagnoses_an_unusable_roster(
    tmp_path: Path, roster: str | None, expected: str
) -> None:
    """An unusable roster must fail *with* a diagnostic, not silently.

    ``grep -v`` exits 1 when every line is a comment or blank, so under ``set -e``
    the script used to abort at the pipeline — before its own error message could
    run — and failed with no explanation of why the digest had nobody to cc.
    """
    result = _read_mentions(tmp_path, roster)

    assert result.returncode != 0
    assert expected in result.stdout + result.stderr


@requires_bash
def test_mentions_reader_emits_at_handles_for_a_real_roster(tmp_path: Path) -> None:
    result = _read_mentions(tmp_path, "# team\nalice\nbob\n")

    assert result.returncode == 0
    assert (tmp_path / "out.txt").read_text().strip() == "mentions=@alice @bob"
