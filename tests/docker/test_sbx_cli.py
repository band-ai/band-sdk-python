"""Regression coverage for the sbx toolkit's pure/host-side pieces:
the sandbox proxy certificate probe, the secret-redacting runner, and the
pty-attached process lifecycle."""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
from tests.paths import REPO_ROOT


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
) -> int:
    """Assert `process` actually exited and `controller_fd` was closed by
    `_stop_pty_process`; return the exit code for callers that care how."""
    process.wait(timeout=5)  # raises TimeoutExpired if it wasn't actually stopped
    with pytest.raises(OSError):
        os.close(controller_fd)  # already closed by _stop_pty_process
    return process.returncode


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

    returncode = _assert_stopped_and_fd_closed(process, controller_fd)
    assert returncode == -signal.SIGKILL  # proves the escalation fired


@markers.requires_posix_pty
def test_attached_run_holds_the_child_open_then_releases_it_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[tuple[subprocess.Popen[bytes], int]] = []

    def spawn_owner(argv: list[str]) -> tuple[subprocess.Popen[bytes], int]:
        child = _spawn_pty_process(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        children.append(child)
        return child

    monkeypatch.setattr(sbx_cli, "_spawn_pty_process", spawn_owner)
    before = _open_fd_count()
    with attached_run("owned-sandbox"):
        assert children[0][0].poll() is None
    _assert_stopped_and_fd_closed(*children[0])
    assert _open_fd_count() == before


@markers.requires_posix_pty
@pytest.mark.parametrize("exit_code", [0, 3])
def test_attached_run_rejects_a_premature_owner_exit(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    children: list[subprocess.Popen[bytes]] = []

    def spawn_owner(argv: list[str]) -> tuple[subprocess.Popen[bytes], int]:
        process, fd = _spawn_pty_process(
            [sys.executable, "-c", f"raise SystemExit({exit_code})"]
        )
        children.append(process)
        return process, fd

    monkeypatch.setattr(sbx_cli, "_spawn_pty_process", spawn_owner)
    with (
        pytest.raises(RuntimeError, match=f"exited {exit_code}"),
        attached_run("owned-sandbox"),
    ):
        children[0].wait(timeout=5)


def test_secret_timeout_does_not_expose_command_or_captured_output() -> None:
    secret = "band-timeout-secret"
    argv = [sys.executable, "-c", "import time; time.sleep(5)", secret]
    with pytest.raises(RuntimeError, match="timed out") as error:
        run_redacting_secret(argv, secret=secret, timeout=1)
    assert secret not in str(error.value)
    assert "***" in str(error.value)


@markers.requires_posix_pty
def test_owned_session_waits_for_reader_before_releasing_its_pty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging_entered = threading.Event()
    release_logging = threading.Event()
    end_body = threading.Event()
    owner_done = threading.Event()
    errors: list[BaseException] = []

    def spawn_owner(argv: list[str]) -> tuple[subprocess.Popen[bytes], int]:
        return _spawn_pty_process(
            [
                sys.executable,
                "-c",
                "import time; print('ready', flush=True); time.sleep(30)",
            ]
        )

    def delayed_logging(*args: object) -> None:
        logging_entered.set()
        assert release_logging.wait(5)

    def own_session() -> None:
        try:
            with attached_run("owned-sandbox"):
                assert end_body.wait(5)
        except BaseException as error:  # noqa: BLE001 - propagate worker failures to the owner
            errors.append(error)
        finally:
            owner_done.set()

    monkeypatch.setattr(sbx_cli, "_spawn_pty_process", spawn_owner)
    monkeypatch.setattr(sbx_cli.logger, "debug", delayed_logging)
    monkeypatch.setattr(sbx_cli, "ATTACH_STOP_TIMEOUT_S", 0.01)
    owner = threading.Thread(target=own_session)
    owner.start()
    try:
        assert logging_entered.wait(5)
        end_body.set()
        assert not owner_done.wait(0.2), "PTY released while reader was active"
    finally:
        end_body.set()
        release_logging.set()
        owner.join(timeout=5)
    assert not owner.is_alive()
    assert errors == []


@markers.requires_posix_pty
def test_owned_session_drains_output_with_high_descriptor(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            r"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from tests.docker.toolkit import sbx_cli
original_spawn = sbx_cli._spawn_pty_process
marker = Path(sys.argv[1])
child = "import os,sys,time; os.write(1,b'x'*2000000); open(sys.argv[1],'w').write('drained'); time.sleep(60)"
spawned = []
def spawn(argv):
    process, fd = original_spawn([sys.executable, "-c", child, str(marker)])
    high_fd = fcntl.fcntl(fd, fcntl.F_DUPFD, 1100)
    os.close(fd)
    spawned.append(process)
    return process, high_fd
sbx_cli._spawn_pty_process = spawn
with sbx_cli.attached_run("local-peer"):
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    drained = marker.exists()
print(json.dumps({"output_drained": drained, "owner_stopped": spawned[0].poll() is not None}))
""",
            str(tmp_path / "drained"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == {"output_drained": True, "owner_stopped": True}


@markers.requires_posix_pty
def test_reader_start_failure_releases_child_and_pty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, controller = _spawn_pty_process(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    monkeypatch.setattr(
        sbx_cli, "_spawn_pty_process", lambda argv: (process, controller)
    )

    def fail_start(thread: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        with (
            pytest.raises(RuntimeError, match="can't start new thread"),
            attached_run("local-peer"),
        ):
            pytest.fail("startup failure must not yield ownership")
        assert process.poll() is not None
        with pytest.raises(OSError):
            os.fstat(controller)
    finally:
        if process.poll() is None:
            _stop_pty_process(process, controller)


@markers.requires_posix_pty
@pytest.mark.parametrize("exhausted", [False, True])
def test_selector_initialization_failure_does_not_yield_broken_session(
    tmp_path: Path,
    exhausted: bool,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            r"""
import errno
import json
import os
import resource
import sys
import time
from pathlib import Path
from tests.docker.toolkit import sbx_cli
marker = Path(sys.argv[1])
exhausted = sys.argv[2] == 'True'
process, controller = sbx_cli._spawn_pty_process([
    sys.executable, '-c',
    "import os,sys,time; os.write(1,b'x'*2000000); open(sys.argv[1],'w').write('done'); time.sleep(60)",
    str(marker),
])
sbx_cli._spawn_pty_process = lambda argv: (process, controller)
limits = resource.getrlimit(resource.RLIMIT_NOFILE)
fillers = []
yielded = False
initialization_failed = False
try:
    if exhausted:
        ceiling = 128 if limits[1] == resource.RLIM_INFINITY else min(128, limits[1])
        resource.setrlimit(resource.RLIMIT_NOFILE, (ceiling, limits[1]))
        while True:
            try:
                fillers.append(os.open('/dev/null', os.O_RDONLY))
            except OSError as error:
                assert error.errno == errno.EMFILE
                break
    try:
        with sbx_cli.attached_run('local-peer'):
            yielded = True
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
    except OSError as error:
        initialization_failed = error.errno == errno.EMFILE
finally:
    for descriptor in fillers:
        os.close(descriptor)
    resource.setrlimit(resource.RLIMIT_NOFILE, limits)
print(json.dumps({
    'owner_yielded': yielded,
    'initialization_failed': initialization_failed,
    'output_completed': marker.exists(),
    'child_stopped': process.poll() is not None,
}))
""",
            str(tmp_path / "drained"),
            str(exhausted),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == {
        "owner_yielded": not exhausted,
        "initialization_failed": exhausted,
        "output_completed": not exhausted,
        "child_stopped": True,
    }
