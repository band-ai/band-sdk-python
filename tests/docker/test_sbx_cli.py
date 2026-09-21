"""Regression coverage for the sbx toolkit's pure/host-side pieces:
the sandbox proxy certificate probe, the secret-redacting runner, and the
pty-attached process lifecycle."""

from __future__ import annotations

import os
import select
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.docker import markers
from tests.docker.toolkit import sbx_cli
from tests.docker.toolkit.sbx_cli import (
    _CERT_PROBE,
    _spawn_pty_process,
    _stop_pty_process,
    attached_run,
    run_redacting_secret,
)


@contextmanager
def connect_proxy(status: bytes) -> Iterator[tuple[str, dict[str, object]]]:
    """Serve one deliberately split CONNECT response and capture client timing.

    ``observed`` doubles as a positive control: ``received_connect`` records that
    the probe actually reached the proxy with a well-formed CONNECT, and ``error``
    surfaces a server-thread failure (e.g. ``accept()`` timing out because the
    probe never connected) that would otherwise be swallowed on the daemon thread
    — without both, the timing assertion could pass vacuously over a probe that
    never ran.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    port = listener.getsockname()[1]
    observed: dict[str, object] = {
        "received_connect": False,
        "sent_tls_before_response_completed": False,
        "error": None,
    }

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                observed["received_connect"] = request.startswith(b"CONNECT ")
                connection.sendall(b"HTTP/1.1 " + status[:3])
                readable, _, _ = select.select([connection], [], [], 0.5)
                observed["sent_tls_before_response_completed"] = bool(readable)
                connection.sendall(status[3:] + b"\r\n\r\n")
        except OSError as exc:  # accept()/recv() timeout or reset: probe never arrived
            observed["error"] = exc

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", observed
    finally:
        thread.join(timeout=5)
        listener.close()


def run_probe(proxy_url: str) -> subprocess.CompletedProcess[str]:
    # Drop any casing of the proxy var first: on Windows os.environ keys are
    # normalized to uppercase, so merging in both "HTTPS_PROXY" and
    # "https_proxy" produces two distinct keys in the plain dict `|` yields,
    # and the child process can end up reading the empty lowercase one.
    env = {k: v for k, v in os.environ.items() if k.upper() != "HTTPS_PROXY"}
    env["HTTPS_PROXY"] = proxy_url
    return subprocess.run(
        [sys.executable, "-c", _CERT_PROBE, "band.example.test"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=5,
    )


def test_cert_probe_waits_for_all_connect_headers_before_tls() -> None:
    with connect_proxy(b"200 Connection Established") as (proxy_url, observed):
        run_probe(proxy_url)

    # Positive control: the probe reached the proxy and sent CONNECT (so the
    # timing assertion isn't passing over a probe that never connected).
    assert observed["error"] is None
    assert observed["received_connect"]
    assert not observed["sent_tls_before_response_completed"]


def test_cert_probe_rejects_non_successful_connect_response() -> None:
    with connect_proxy(b"407 Proxy Authentication Required") as (proxy_url, observed):
        result = run_probe(proxy_url)

    assert observed["error"] is None
    assert observed["received_connect"]
    assert "cert-probe-error: RuntimeError proxy CONNECT refused" in result.stdout
    assert not observed["sent_tls_before_response_completed"]


def test_run_redacting_secret_masks_the_secret_on_failure() -> None:
    # The never-in-VM proof passes real Band keys through secret-bearing argv
    # (set-custom --value, the files_containing grep). A failure must raise
    # with the key masked — including a key EMBEDDED inside a longer argument,
    # not just one that is a whole argument.
    secret = "band-sk-neverVM-SEKRET"
    argv = [
        sys.executable,
        "-c",
        "import sys; print('daemon hiccup', file=sys.stderr); sys.exit(3)",
        f"grep -ralF -- {secret} /workspace",
        secret,
    ]

    with pytest.raises(RuntimeError) as excinfo:
        run_redacting_secret(argv, secret=secret)

    message = str(excinfo.value)
    assert secret not in message
    assert "***" in message
    # The useful diagnostics survive redaction.
    assert "exit 3" in message
    assert "daemon hiccup" in message


def test_run_redacting_secret_returns_stdout_on_success() -> None:
    secret = "band-sk-neverVM-SEKRET"
    argv = [sys.executable, "-c", "print('clean')", secret]

    assert run_redacting_secret(argv, secret=secret) == "clean\n"


def _open_fd_count() -> int:
    """Number of this process's open file descriptors, POSIX-portable
    (procfs on Linux, fdescfs on macOS)."""
    return len(os.listdir("/dev/fd"))


def _assert_stopped_and_fd_closed(
    process: subprocess.Popen[bytes], controller_fd: int
) -> None:
    process.wait(timeout=5)  # raises TimeoutExpired if it wasn't actually stopped
    with pytest.raises(OSError):
        os.close(controller_fd)  # already closed by _stop_pty_process


@markers.requires_posix_pty
def test_spawn_pty_process_returns_a_live_readable_pty() -> None:
    process, controller_fd = _spawn_pty_process([sys.executable, "-c", "print('hi')"])
    try:
        os.set_blocking(controller_fd, True)
        assert b"hi" in os.read(controller_fd, 1024)
    finally:
        _stop_pty_process(process, controller_fd)


@markers.requires_posix_pty
def test_spawn_pty_process_closes_both_fds_when_the_child_fails_to_start() -> None:
    # A missing executable fails Popen synchronously, before any child process
    # exists to leave behind -- the only way this leaks is the two pty fds.
    before = _open_fd_count()

    with pytest.raises(FileNotFoundError):
        _spawn_pty_process(["definitely-not-a-real-executable-xyz"])

    assert _open_fd_count() == before


@markers.requires_posix_pty
def test_stop_pty_process_terminates_a_cooperative_process() -> None:
    process, controller_fd = _spawn_pty_process(
        [sys.executable, "-c", "import time; time.sleep(100)"]
    )

    _stop_pty_process(process, controller_fd)

    _assert_stopped_and_fd_closed(process, controller_fd)


@markers.requires_posix_pty
def test_stop_pty_process_escalates_to_sigkill_when_sigterm_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sbx_cli, "ATTACH_STOP_TIMEOUT_S", 0.2)
    ignore_sigterm = (
        "import signal; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); "
        "import time; time.sleep(100)"
    )
    process, controller_fd = _spawn_pty_process([sys.executable, "-c", ignore_sigterm])
    try:
        os.set_blocking(controller_fd, True)
        # Wait for the child to actually install the ignore handler -- sending
        # SIGTERM any earlier would kill it under the default disposition and
        # the escalation this test targets would never run.
        assert b"ready" in os.read(controller_fd, 1024)
    except BaseException:
        _stop_pty_process(process, controller_fd)
        raise

    _stop_pty_process(process, controller_fd)

    assert process.returncode == -signal.SIGKILL  # proves the escalation fired
    with pytest.raises(OSError):
        os.close(controller_fd)  # already closed by _stop_pty_process


@markers.requires_posix_pty
def test_attached_run_holds_the_child_open_then_releases_it_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # attached_run always runs `[SBX, "run", "--name", name]`; substituting the
    # interpreter for SBX exercises the real spawn/stop wiring without `sbx`.
    monkeypatch.setattr(sbx_cli, "SBX", sys.executable)
    before = _open_fd_count()

    with attached_run("unused-sandbox-name"):
        pass

    assert _open_fd_count() == before
