from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.docker import markers
from tests.docker.sbxcli.kit import (
    _assert_stopped_and_fd_closed,
    _open_fd_count,
)
from tests.docker.toolkit import sbx_cli
from tests.docker.toolkit.sbx_cli import (
    _spawn_pty_process,
    _stop_pty_process,
    attached_run,
)
from tests.paths import REPO_ROOT


class TestAttachedSession:
    @markers.requires_posix_pty
    def test_attached_run_holds_the_child_open_then_releases_it_on_exit(
        self,
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
        self, monkeypatch: pytest.MonkeyPatch, exit_code: int
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

    @markers.requires_posix_pty
    def test_owned_session_waits_for_reader_before_releasing_its_pty(
        self,
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
    def test_owned_session_drains_output_with_high_descriptor(
        self, tmp_path: Path
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                r"""
import fcntl
import json
import os
import resource
import sys
import time
from pathlib import Path
from tests.docker.toolkit import sbx_cli
minimum_fd = 1100
soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft_limit != resource.RLIM_INFINITY and soft_limit <= minimum_fd:
    resource.setrlimit(resource.RLIMIT_NOFILE, (minimum_fd + 1, hard_limit))
original_spawn = sbx_cli._spawn_pty_process
marker = Path(sys.argv[1])
child = "import os,sys,time; os.write(1,b'x'*2000000); open(sys.argv[1],'w').write('drained'); time.sleep(60)"
spawned = []
def spawn(argv):
    process, fd = original_spawn([sys.executable, "-c", child, str(marker)])
    high_fd = fcntl.fcntl(fd, fcntl.F_DUPFD, minimum_fd)
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
        assert json.loads(result.stdout) == {
            "output_drained": True,
            "owner_stopped": True,
        }

    @markers.requires_posix_pty
    def test_reader_start_failure_releases_child_and_pty(
        self,
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
        self,
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
