"""Behavioural guards for the e2e workflow's helper scripts (``.github/scripts``).

These scripts carry real branching logic — which lane×OS cells exist, whether a retry
has anything to retry — but they only ever execute inside the nightly E2E job, where a
wrong branch surfaces as a confusing red run hours later (or, worse, as a silently
skipped step). Exercising them here puts that logic in the ordinary unit suite on
every PR.

Each test drives the *real* script as a subprocess, so it is a tripwire on the shipped
file rather than on a re-implementation of it.
"""

from __future__ import annotations

import os
import subprocess
import sys

from tests.paths import CI_SCRIPTS, REPO_ROOT

_EMIT_LANE_MATRIX = CI_SCRIPTS / "emit-lane-matrix.py"


def _emit_lane_matrix(lane: str, os_id: str) -> subprocess.CompletedProcess[str]:
    """Run the lane-matrix emitter for one dispatch selection.

    Overlays onto the inherited environment rather than replacing it: a bare
    ``env={...}`` drops ``SYSTEMROOT`` on Windows, and CPython needs it to load
    the winsock extension ``asyncio`` imports — the interpreter then dies with
    ``WinError 10106`` before the script's own code runs.
    """
    return subprocess.run(
        [sys.executable, str(_EMIT_LANE_MATRIX)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "SELECTED_LANE": lane,
            "SELECTED_OS": os_id,
        },
        capture_output=True,
        text=True,
    )


def test_lane_matrix_rejects_a_selection_with_no_runnable_cell() -> None:
    """A Linux-only lane asked for on Windows must fail, not emit an empty matrix.

    Both halves are offered by the dispatch dropdowns, so the pair is reachable by
    hand. Emitting ``include: []`` instead would leave the downstream
    ``EXPECTED_LANES`` blank, which ``scorecard.py``'s ``--expected-lanes``
    validation then rejects as an unknown lane id, and report a red digest —
    blaming the suite for an impossible request.
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
