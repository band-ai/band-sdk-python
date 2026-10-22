from __future__ import annotations

import os
import signal
import sys

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
)


class TestPtyProcess:
    @markers.requires_posix_pty
    def test_spawn_pty_process_returns_a_live_readable_pty(self) -> None:
        process, controller_fd = _spawn_pty_process(
            [sys.executable, "-c", "print('hi')"]
        )
        try:
            os.set_blocking(controller_fd, True)
            assert b"hi" in os.read(controller_fd, 1024)
        finally:
            _stop_pty_process(process, controller_fd)

    @markers.requires_posix_pty
    def test_spawn_pty_process_closes_both_fds_when_the_child_fails_to_start(
        self,
    ) -> None:
        # A missing executable fails Popen synchronously, before any child process
        # exists to leave behind -- the only way this leaks is the two pty fds.
        before = _open_fd_count()

        with pytest.raises(FileNotFoundError):
            _spawn_pty_process(["definitely-not-a-real-executable-xyz"])

        assert _open_fd_count() == before

    @markers.requires_posix_pty
    def test_stop_pty_process_terminates_a_cooperative_process(self) -> None:
        process, controller_fd = _spawn_pty_process(
            [sys.executable, "-c", "import time; time.sleep(100)"]
        )

        _stop_pty_process(process, controller_fd)

        _assert_stopped_and_fd_closed(process, controller_fd)

    @markers.requires_posix_pty
    def test_stop_pty_process_escalates_to_sigkill_when_sigterm_is_ignored(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sbx_cli, "ATTACH_STOP_TIMEOUT_S", 0.2)
        ignore_sigterm = (
            "import signal; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); "
            "import time; time.sleep(100)"
        )
        process, controller_fd = _spawn_pty_process(
            [sys.executable, "-c", ignore_sigterm]
        )
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
