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

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import CI_SCRIPTS, REPO_ROOT

_EMIT_LANE_MATRIX = CI_SCRIPTS / "emit-lane-matrix.py"
_READ_MENTIONS = CI_SCRIPTS / "read-integrations-mentions.sh"
_RUN_BASELINE_E2E = CI_SCRIPTS / "run-baseline-e2e.sh"
_WATCH_PROGRESS = CI_SCRIPTS / "watch-progress.py"
_ROSTER = Path(".github") / "integrations-team.txt"

# POSIX-shell only. On Windows, `shutil.which("bash")` finds System32\bash.exe —
# the WSL launcher, not a shell — which on a runner with no WSL distro installed
# prints a UTF-16 "no installed distributions" notice and exits 1. So a
# which()-based guard does not skip, it just fails confusingly. The script under
# test only ever runs in the ubuntu-only `mark-baseline` job anyway, so a POSIX
# shell is its real contract.
posix_shell_only = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX bash (Windows `bash` is the WSL launcher)",
)


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


def _read_mentions(
    tmp_path: Path, roster: str | None
) -> subprocess.CompletedProcess[str]:
    """Run the mentions reader against a throwaway roster (``None`` = no file).

    Overlays the environment (see ``_emit_lane_matrix``) so the script keeps a real
    ``PATH`` for the ``grep``/``sed``/``paste`` it pipes through, rather than relying
    on bash's fallback default.
    """
    script = tmp_path / _READ_MENTIONS.name
    shutil.copy(_READ_MENTIONS, script)
    if roster is not None:
        (tmp_path / _ROSTER).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / _ROSTER).write_text(roster)
    return subprocess.run(
        ["bash", script.name],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_OUTPUT": str(tmp_path / "out.txt")},
        capture_output=True,
        text=True,
    )


@posix_shell_only
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


@posix_shell_only
def test_mentions_reader_emits_at_handles_for_a_real_roster(tmp_path: Path) -> None:
    result = _read_mentions(tmp_path, "# team\nalice\nbob\n")

    assert result.returncode == 0
    assert (tmp_path / "out.txt").read_text().strip() == "mentions=@alice @bob"


def _watch_progress(
    tmp_path: Path, code: str, *, idle_seconds: float
) -> subprocess.CompletedProcess[str]:
    """Run the shipped progress watchdog around a tiny deterministic child."""
    return subprocess.run(
        [
            sys.executable,
            str(_WATCH_PROGRESS),
            "--idle-seconds",
            str(idle_seconds),
            "--diagnostic",
            str(tmp_path / "diagnostic.json"),
            "--",
            sys.executable,
            "-c",
            code,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_progress_watchdog_terminates_a_silent_child_with_safe_diagnostic(
    tmp_path: Path,
) -> None:
    """A harness hang must be red and identify only the safe current node id."""
    result = _watch_progress(
        tmp_path,
        "import time; print('E2E_PROGRESS nodeid=tests/e2e/test_hang.py::test_hang', flush=True); time.sleep(2)",
        idle_seconds=0.1,
    )

    assert result.returncode == 124
    assert "current node: tests/e2e/test_hang.py::test_hang" in result.stderr
    diagnostic = (tmp_path / "diagnostic.json").read_text(encoding="utf-8")
    assert '"kind": "e2e_progress_timeout"' in diagnostic
    assert '"nodeid": "tests/e2e/test_hang.py::test_hang"' in diagnostic


def test_progress_watchdog_preserves_a_completed_child_output(tmp_path: Path) -> None:
    """The watchdog observes progress; it does not change a healthy command's verdict."""
    result = _watch_progress(
        tmp_path,
        "print('E2E_PROGRESS nodeid=tests/e2e/test_ok.py::test_ok', flush=True)",
        idle_seconds=1,
    )

    assert result.returncode == 0
    assert "E2E_PROGRESS nodeid=tests/e2e/test_ok.py::test_ok" in result.stdout
    assert not (tmp_path / "diagnostic.json").exists()


@posix_shell_only
def test_baseline_runner_keeps_a_progress_timeout_red_without_scorecard(
    tmp_path: Path,
) -> None:
    """A killed pytest child cannot become a retry pass or a green empty fragment."""
    scripts = tmp_path / ".github" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_RUN_BASELINE_E2E, scripts)
    shutil.copy(_WATCH_PROGRESS, scripts)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nsleep 2\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    attempts = tmp_path / "artifacts" / "attempts"
    diagnostics = tmp_path / "artifacts" / "diagnostics"
    result = subprocess.run(
        ["bash", str(scripts / _RUN_BASELINE_E2E.name)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": os.pathsep.join(
                [str(fake_bin), str(Path(sys.executable).parent), os.environ["PATH"]]
            ),
            "ATTEMPT1": str(attempts / "one.json"),
            "ATTEMPT2": str(attempts / "two.json"),
            "FINAL": str(tmp_path / "artifacts" / "scorecard-core-ubuntu.json"),
            "PROGRESS_DIAGNOSTIC1": str(diagnostics / "one.json"),
            "PROGRESS_DIAGNOSTIC2": str(diagnostics / "two.json"),
            "E2E_PROGRESS_DEADLINE_SECONDS": "0.1",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 124
    assert (diagnostics / "one.json").exists()
    assert not (tmp_path / "artifacts" / "scorecard-core-ubuntu.json").exists()
