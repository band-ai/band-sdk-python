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

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs

import pytest

from tests.paths import CI_SCRIPTS, REPO_ROOT

_EMIT_LANE_MATRIX = CI_SCRIPTS / "emit-lane-matrix.py"
_RUN_BASELINE_E2E = CI_SCRIPTS / "run-baseline-e2e.sh"
_READ_MENTIONS = CI_SCRIPTS / "read-integrations-mentions.sh"
_ZEN_SIGN_IN = CI_SCRIPTS / "opencode-zen-sign-in.sh"
_RECORD_BACKENDS_ENVIRONMENT = CI_SCRIPTS / "record-backends-environment.py"
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


def _run_baseline(
    tmp_path: Path, *, extra: dict[str, str] | None = None, fail: bool = False
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the baseline script against a uv stub and return its recorded command.

    ``BAND_E2E_LOG_LEVEL`` is dropped from the inherited environment so a
    developer's shell cannot change which flags the script is expected to pass.
    """
    stub = tmp_path / "uv"
    exit_line = "sys.exit(1)\n" if fail else ""
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CALL_LOG']).open('a') as log:\n"
        "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "Path(os.environ['BAND_E2E_SCORECARD_JSON']).write_text('[]')\n"
        f"{exit_line}"
    )
    stub.chmod(0o755)
    call_log = tmp_path / "calls.txt"
    result = subprocess.run(
        ["bash", str(_RUN_BASELINE_E2E)],
        cwd=REPO_ROOT,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "BAND_E2E_LOG_LEVEL"
        }
        | {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "CALL_LOG": str(call_log),
            "FINAL": str(tmp_path / "scorecard.json"),
            **(extra or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    (command,) = call_log.read_text().splitlines()
    return result, command


@posix_shell_only
def test_baseline_runner_records_one_failed_attempt(tmp_path: Path) -> None:
    result, command = _run_baseline(tmp_path, fail=True)

    assert result.returncode == 1
    assert "-p no:rerunfailures" in command
    assert "--log-cli-level=INFO" in command
    assert "--log-level=INFO" in command
    assert "--last-failed" not in command


@posix_shell_only
@pytest.mark.parametrize(
    ("extra", "present", "absent"),
    [
        (
            {"BAND_E2E_LOG_LEVEL": "DEBUG"},
            ("--log-level=DEBUG", "--log-cli-level=DEBUG"),
            ("--log-cli-level=WARNING",),
        ),
        (
            {
                "BAND_E2E_LOG_LEVEL": "DEBUG",
                "BAND_E2E_FIRST_ATTEMPT_DIAGNOSTICS": "true",
            },
            ("--log-level=DEBUG", "--log-cli-level=DEBUG"),
            ("--log-cli-level=WARNING",),
        ),
        (
            {"BAND_E2E_FIRST_ATTEMPT_DIAGNOSTICS": "true"},
            ("--log-cli-level=INFO", "--log-level=INFO"),
            ("--log-cli-level=WARNING",),
        ),
    ],
    ids=["level-only", "level-and-diagnostics", "diagnostics-only"],
)
def test_baseline_runner_forwards_a_diagnostic_log_level(
    tmp_path: Path,
    extra: dict[str, str],
    present: tuple[str, ...],
    absent: tuple[str, ...],
) -> None:
    result, command = _run_baseline(tmp_path, extra=extra)

    assert result.returncode == 0
    assert all(flag in command for flag in present)
    assert all(flag not in command for flag in absent)


def _emit_lane_matrix(lane: str, os_id: str) -> subprocess.CompletedProcess[str]:
    """Run the lane-matrix emitter for one dispatch selection.

    Overlays onto the inherited environment rather than replacing it: a bare
    ``env={...}`` drops ``SYSTEMROOT`` on Windows, and CPython needs it to load
    the winsock extension ``asyncio`` imports — the interpreter then dies with
    ``WinError 10106`` before the script's own code runs.
    """
    return subprocess.run(
        [sys.executable, str(_EMIT_LANE_MATRIX)],
        check=False,
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
        check=False,
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


def _fake_cli(
    directory: Path,
    name: str,
    *,
    stdout: str = "",
    exit_code: int = 0,
    sleep_s: float = 0,
    echo_args: bool = False,
) -> None:
    """Install a host-runnable CLI stub that works on POSIX and Windows."""
    script = directory / f"_{name}_impl.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "import time\n"
        f"time.sleep({sleep_s})\n"
        + (f"print({stdout!r})\n" if stdout else "")
        + ("print(' '.join(sys.argv[1:]))\n" if echo_args else "")
        + f"raise SystemExit({exit_code})\n"
    )
    if sys.platform == "win32":
        (directory / f"{name}.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n'
        )
        return
    launcher = directory / name
    launcher.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n")
    launcher.chmod(0o755)


def _load_record_backends_environment() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_backends_environment", _RECORD_BACKENDS_ENVIRONMENT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_environment_record_reports_each_cli_version_probe(tmp_path: Path) -> None:
    """The scorecard evidence names each CLI's version, or why it has none."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_cli(bin_dir, "fake-cursor", stdout="2026.01.01-abc123")
    _fake_cli(bin_dir, "copilot", exit_code=3)

    result = subprocess.run(
        [sys.executable, str(_RECORD_BACKENDS_ENVIRONMENT)],
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            # Only the stubs — host CLIs must not satisfy these probes.
            "PATH": str(bin_dir),
            "PYTHONPATH": str(REPO_ROOT),
            "CURSOR_COMMAND": "fake-cursor acp",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    [record] = (tmp_path / "artifacts").glob("environment-backends-*.json")
    environment = json.loads(record.read_text())
    assert environment["cursor_cli"] == "2026.01.01-abc123"
    assert environment["copilot_cli"] == "exited 3"


@pytest.mark.parametrize(
    ("command", "probe"),
    [
        ("fake-cursor acp", "--version"),
        # Windows CI launches Cursor's bundled node and entrypoint directly.
        ("fake-node index.js acp", "index.js --version"),
    ],
    ids=["launcher", "direct-runtime"],
)
def test_cursor_version_probes_the_launch_command_without_acp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, probe: str
) -> None:
    module = _load_record_backends_environment()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_cli(bin_dir, command.split()[0], echo_args=True)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert module.cli_version(*module.cursor_launch(command)) == probe


def test_cli_version_reports_not_found_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing or hung binary is recorded as the probe outcome, not raised."""
    module = _load_record_backends_environment()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_cli(bin_dir, "slow-cli", stdout="never-seen", sleep_s=2)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(module, "VERSION_TIMEOUT_S", 0.2)

    assert module.cli_version("missing-cli") == "not found"
    assert module.cli_version("slow-cli") == "timed out after 0.2s"

    _fake_cli(bin_dir, "empty-cli")
    monkeypatch.setattr(module, "VERSION_TIMEOUT_S", 30)
    assert module.cli_version("empty-cli") == "empty version output"


# --- opencode-zen-sign-in.sh ---------------------------------------------------------------

_OIDC_TOKEN = "github-oidc-jwt"
_ZEN_KEY = "zen-short-lived-key"
_OIDC_OK = (200, {"value": _OIDC_TOKEN})
_UNREACHED = (500, {})
_SIGN_IN_FAILED = "OpenCode Zen sign-in failed: "


@dataclass
class _SignInEndpoints:
    """A local stand-in for GitHub's OIDC endpoint and the Zen token exchange."""

    oidc: tuple[int, dict[str, object]]
    exchange: tuple[int, dict[str, object]]
    oidc_requests: list[tuple[str, str | None]] = field(default_factory=list)
    exchanges: list[dict[str, list[str]]] = field(default_factory=list)


@contextmanager
def _serving(endpoints: _SignInEndpoints) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            endpoints.oidc_requests.append(
                (self.path, self.headers.get("Authorization"))
            )
            self._reply(endpoints.oidc)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            endpoints.exchanges.append(parse_qs(body))
            self._reply(endpoints.exchange)

        def _reply(self, reply: tuple[int, dict[str, object]]) -> None:
            status, payload = reply
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _sign_in(
    tmp_path: Path, endpoints: _SignInEndpoints
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the real sign-in script against ``endpoints``; return it and $GITHUB_ENV."""
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    with _serving(endpoints) as base:
        result = subprocess.run(
            ["bash", str(_ZEN_SIGN_IN)],
            env={
                "PATH": os.environ["PATH"],
                "OPENCODE_ZEN_CLIENT_ID": "client-1",
                "ACTIONS_ID_TOKEN_REQUEST_URL": f"{base}/oidc?v=1",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "OPENCODE_ZEN_TOKEN_URL": f"{base}/token",
                "GITHUB_ENV": str(github_env),
            },
            capture_output=True,
            text=True,
            check=False,
        )
    return result, github_env.read_text()


@posix_shell_only
def test_zen_sign_in_masks_and_exports_a_granted_key(tmp_path: Path) -> None:
    endpoints = _SignInEndpoints(
        _OIDC_OK, (200, {"access_token": _ZEN_KEY, "expires_in": 3600})
    )

    result, github_env = _sign_in(tmp_path, endpoints)

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"::add-mask::{_ZEN_KEY}",
        "OpenCode Zen token expires_in=3600",
    ]
    assert github_env == f"OPENCODE_ZEN_API_KEY={_ZEN_KEY}\n"
    assert endpoints.oidc_requests == [
        ("/oidc?v=1&audience=https://opencode.ai/console", "Bearer request-token")
    ]
    assert endpoints.exchanges == [
        {
            "grant_type": ["urn:ietf:params:oauth:grant-type:token-exchange"],
            "subject_token_type": ["urn:ietf:params:oauth:token-type:jwt"],
            "client_id": ["client-1"],
            "subject_token": [_OIDC_TOKEN],
        }
    ]


@posix_shell_only
def test_zen_sign_in_logs_a_rejection_reason_and_exports_nothing(
    tmp_path: Path,
) -> None:
    rejection = {"error": "invalid_grant", "error_description": "branch not trusted"}

    result, github_env = _sign_in(
        tmp_path, _SignInEndpoints(_OIDC_OK, (400, rejection))
    )

    assert result.returncode == 1
    assert f"{_SIGN_IN_FAILED}token exchange:" in result.stderr
    assert "branch not trusted" in result.stderr
    assert github_env == ""


@posix_shell_only
def test_zen_sign_in_rejects_an_exchange_that_grants_no_key(tmp_path: Path) -> None:
    result, github_env = _sign_in(
        tmp_path, _SignInEndpoints(_OIDC_OK, (200, {"expires_in": 3600}))
    )

    assert result.returncode == 1
    assert f"{_SIGN_IN_FAILED}token exchange returned no access_token" in result.stderr
    assert github_env == ""


@posix_shell_only
def test_zen_sign_in_stops_before_the_exchange_without_an_oidc_token(
    tmp_path: Path,
) -> None:
    endpoints = _SignInEndpoints((200, {}), _UNREACHED)

    result, github_env = _sign_in(tmp_path, endpoints)

    assert result.returncode == 1
    assert f"{_SIGN_IN_FAILED}GitHub OIDC response has no token" in result.stderr
    assert endpoints.exchanges == []
    assert github_env == ""


@posix_shell_only
def test_zen_sign_in_logs_a_refused_oidc_request_and_skips_the_exchange(
    tmp_path: Path,
) -> None:
    refusal = {"message": "Unable to get ACTIONS_ID_TOKEN_REQUEST_URL"}
    endpoints = _SignInEndpoints((403, refusal), _UNREACHED)

    result, github_env = _sign_in(tmp_path, endpoints)

    assert result.returncode == 1
    assert f"{_SIGN_IN_FAILED}GitHub OIDC request:" in result.stderr
    assert refusal["message"] in result.stderr
    assert endpoints.exchanges == []
    assert github_env == ""
