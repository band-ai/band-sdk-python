"""Native handle errors preserve the job's retryable ownership."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from band.integrations.codex import winspawn
from tests.integrations.codex.jobpeer import NativeJobPeer


class TestJobHandleOwnership:
    def test_failed_handle_close_can_retry_the_same_handle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handle = ctypes.c_void_p(1)
        job = winspawn.Job(handle, "test")
        attempted: list[ctypes.c_void_p] = []

        def close_handle(current: ctypes.c_void_p) -> bool:
            attempted.append(current)
            return len(attempted) > 1

        monkeypatch.setattr(
            winspawn, "_kernel32", lambda: SimpleNamespace(CloseHandle=close_handle)
        )
        monkeypatch.setattr(winspawn, "_last_error", lambda: 5)
        monkeypatch.setattr(winspawn, "_win_os_error", lambda code: OSError(code))
        with pytest.raises(OSError):
            job.close()
        job.close()
        assert attempted == [handle, handle]

    async def test_failed_termination_is_reported_without_releasing_the_job(
        self, native_job: NativeJobPeer
    ) -> None:
        handle = native_job.handle
        job = winspawn.Job(handle, "test")
        terminated: list[ctypes.c_void_p] = []

        def terminate(current: ctypes.c_void_p, _code: int) -> bool:
            terminated.append(current)
            if len(terminated) == 1:
                native_job.error = 5
                return False
            return native_job.terminate(current, _code)

        native_job.kernel.TerminateJobObject = terminate
        with pytest.raises(OSError):
            job.terminate()
        job.terminate()
        assert terminated == [handle, handle]
        native_job.exited.add(101)
        await job.wait_until_idle(0)
        job.close()

    async def test_zero_accounting_retains_unsignaled_member_until_retry(
        self, native_job: NativeJobPeer
    ) -> None:
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        assert native_job.active == 0
        with pytest.raises(winspawn.JobNotDrained):
            await job.wait_until_idle(0)
        assert not native_job.closed
        assert native_job.opened == {101}
        native_job.exited.add(101)
        job.terminate()
        await job.wait_until_idle(0)
        job.close()
        assert native_job.closed
        assert not native_job.opened

    async def test_all_members_must_exit_when_the_pid_buffer_grows(
        self, native_job: NativeJobPeer
    ) -> None:
        native_job.pids = list(range(101, 166))
        native_job.active = len(native_job.pids)
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        native_job.exited.update(native_job.pids[:-1])
        with pytest.raises(winspawn.JobNotDrained):
            await job.wait_until_idle(0)
        assert native_job.opened == {165}
        native_job.exited.add(165)
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened

    async def test_reused_pid_is_not_waited_as_a_job_member(
        self, native_job: NativeJobPeer
    ) -> None:
        native_job.reused.add(101)
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened
        assert not native_job.waited

    def test_denied_member_handle_preserves_job_custody(
        self, native_job: NativeJobPeer
    ) -> None:
        native_job.open_error = 5
        job = winspawn.Job(native_job.handle, "test")
        with pytest.raises(OSError):
            job.terminate()
        assert not native_job.terminated
        assert not native_job.closed

    async def test_native_wait_failure_retains_the_member_handle(
        self, native_job: NativeJobPeer
    ) -> None:
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        native_job.wait_error = True
        with pytest.raises(OSError):
            await job.wait_until_idle(0)
        assert native_job.opened == {101}
        assert not native_job.closed
        native_job.wait_error = False
        native_job.exited.add(101)
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened

    async def test_failed_member_handle_close_keeps_retryable_ownership(
        self, native_job: NativeJobPeer
    ) -> None:
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        native_job.exited.add(101)
        native_job.close_error = True
        with pytest.raises(OSError):
            await job.wait_until_idle(0)
        assert native_job.opened == {101}
        assert not native_job.closed
        native_job.close_error = False
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened

    async def test_job_close_cannot_discard_pending_physical_exit(
        self, native_job: NativeJobPeer
    ) -> None:
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        with pytest.raises(winspawn.JobNotDrained):
            job.close()
        assert not native_job.closed
        native_job.exited.add(101)
        await job.wait_until_idle(0)
        job.close()
        assert native_job.closed

    async def test_failed_membership_query_keeps_the_handle_for_retry(
        self, native_job: NativeJobPeer
    ) -> None:
        native_job.kernel.IsProcessInJob = lambda *_args: False
        native_job.error = 5
        job = winspawn.Job(native_job.handle, "test")
        with pytest.raises(OSError):
            job.terminate()
        assert native_job.opened == {101}
        assert not native_job.terminated
        native_job.kernel.IsProcessInJob = native_job.is_member
        job.terminate()
        native_job.exited.add(101)
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened

    async def test_vanished_member_pid_is_confirmed_against_the_job(
        self, native_job: NativeJobPeer
    ) -> None:
        def gone(_access: int, _inherit: bool, _pid: int) -> None:
            native_job.pids.clear()
            native_job.active = 0
            native_job.error = 87

        native_job.kernel.OpenProcess = gone
        job = winspawn.Job(native_job.handle, "test")
        job.terminate()
        await job.wait_until_idle(0)
        job.close()
        assert not native_job.opened
        assert not native_job.waited

    def test_invalid_member_pid_still_in_the_job_is_not_suppressed(
        self, native_job: NativeJobPeer
    ) -> None:
        native_job.open_error = 87
        job = winspawn.Job(native_job.handle, "test")
        with pytest.raises(OSError):
            job.terminate()
        assert not native_job.terminated
        assert not native_job.closed
