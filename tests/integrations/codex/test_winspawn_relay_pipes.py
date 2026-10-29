"""Real-pipe coverage for the Windows stderr relay (Windows only).

Launches the genuine ``winmain`` helper through the genuine job and
``_spawn_suspended`` path with an *undrained* stderr destination and a
command writing far past both the relay pipe and the bounded buffer.  The
child must still complete and the helper must still exit with it: the
reader thread keeps the child's pipe draining no matter what the
destination does.  No kernel32 fakes here — that is the point.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from band.integrations.codex import winspawn

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="the helper relaunch path is Windows-only"
)

# 512 * 64 KiB: a multiple of both the relay pipe and the 1 MiB buffer.
_PAYLOAD_CHUNKS = 512


def test_undrained_sink_does_not_stall_the_child() -> None:
    script = (
        "import sys\n"
        f"for _ in range({_PAYLOAD_CHUNKS}):\n"
        "    sys.stderr.buffer.write(b'x' * 65536)\n"
        "sys.stderr.buffer.flush()\n"
    )
    job = winspawn.create_job()
    try:
        read_fd, write_fd = winspawn._status_pipe()
        status_handle = winspawn._os_handle(write_fd)
        argv = winspawn.command_in_job(
            [sys.executable, "-c", script],
            job.name,
            status_handle,
        )
        startup = subprocess.STARTUPINFO()
        # Same mechanism the production launcher uses: the attribute list,
        # not the inheritable flag, crosses the Popen boundary here.
        startup.lpAttributeList = {"handle_list": [status_handle]}
        helper = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=startup,
        )
        os.close(write_fd)
        try:
            helper.stdin.write(winspawn.encode_environment({}))
            helper.stdin.close()
            deadline = time.monotonic() + 60
            status = ""
            while time.monotonic() < deadline:
                chunk = os.read(read_fd, 32)
                if not chunk:
                    break
                status += chunk.decode()
                if "\n" in status:
                    break
            assert status.split() == [winspawn._READY]
            # Nobody ever reads helper.stderr. If the relay propagated the
            # destination's backpressure to the child, this wait expires.
            assert helper.wait(timeout=60) == 0
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=30)
            for stream in (helper.stdin, helper.stderr):
                if stream is not None:
                    stream.close()
            os.close(read_fd)
    finally:
        job.close()
