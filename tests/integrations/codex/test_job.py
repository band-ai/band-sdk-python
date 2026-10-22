"""Native handle errors preserve the job's retryable ownership."""

from __future__ import annotations

import asyncio
import ctypes
from types import SimpleNamespace

import pytest

from band.integrations.codex import winspawn
from tests.integrations.codex.job import (
    FIRST_MEMBER_PID,
    JOB_HANDLE,
    KILL_ON_JOB_CLOSE,
    PROCESS_HANDLES,
    SECOND_MEMBER_PID,
    UNRELATED_HANDLE,
    JobKernel,
)


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
        self, job_kernel: JobKernel
    ) -> None:
        handle = ctypes.c_void_p(JOB_HANDLE)
        job = winspawn.Job(handle, "test")
        job_kernel.termination_failures = 1
        with pytest.raises(OSError):
            job.terminate()
        assert not job_kernel.closed
        job.terminate()
        assert job_kernel.termination_attempts == [JOB_HANDLE, JOB_HANDLE]
        job_kernel.signaled.update(PROCESS_HANDLES.values())
        await job.wait_until_idle(0)
        job.close()
        assert sorted(job_kernel.closed) == [JOB_HANDLE, *PROCESS_HANDLES.values()]

    async def test_empty_accounting_does_not_release_pending_kernel_processes(
        self, job_kernel: JobKernel
    ) -> None:
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        assert job.active_processes() == 0
        with pytest.raises(winspawn.JobNotDrained):
            await job.wait_until_idle(0)
        assert not job_kernel.closed

        job_kernel.signaled.update(PROCESS_HANDLES.values())
        await job.wait_until_idle(0)
        job.close()
        assert sorted(job_kernel.closed) == [JOB_HANDLE, *PROCESS_HANDLES.values()]

    def test_admission_is_sealed_before_a_member_can_fork_during_capture(
        self, job_kernel: JobKernel
    ) -> None:
        job_kernel.fork_during_open = True
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        assert job_kernel.late_fork_rejected
        assert job_kernel.limits.BasicLimitInformation.LimitFlags & KILL_ON_JOB_CLOSE

    async def test_recycled_pid_is_not_waited_as_an_owned_process(
        self, job_kernel: JobKernel
    ) -> None:
        job_kernel.recycled_pid = FIRST_MEMBER_PID
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        job_kernel.signaled.add(PROCESS_HANDLES[SECOND_MEMBER_PID])
        await job.wait_until_idle(0)
        job.close()
        assert sorted(job_kernel.closed) == [
            JOB_HANDLE,
            PROCESS_HANDLES[SECOND_MEMBER_PID],
            UNRELATED_HANDLE,
        ]

    async def test_process_that_exits_during_handle_capture_does_not_block_cleanup(
        self, job_kernel: JobKernel
    ) -> None:
        job_kernel.missing_pid = FIRST_MEMBER_PID
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        job_kernel.signaled.add(PROCESS_HANDLES[SECOND_MEMBER_PID])
        await job.wait_until_idle(0)
        job.close()
        assert sorted(job_kernel.closed) == [
            JOB_HANDLE,
            PROCESS_HANDLES[SECOND_MEMBER_PID],
        ]

    async def test_native_wait_failure_retains_process_handles_for_retry(
        self, job_kernel: JobKernel
    ) -> None:
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        job_kernel.wait_fails = True
        with pytest.raises(OSError):
            await job.wait_until_idle(0)
        assert not job_kernel.closed
        job_kernel.wait_fails = False
        job_kernel.signaled.update(PROCESS_HANDLES.values())
        await job.wait_until_idle(0)
        job.close()
        assert sorted(job_kernel.closed) == [JOB_HANDLE, *PROCESS_HANDLES.values()]

    @pytest.mark.parametrize("failing_pid", [FIRST_MEMBER_PID, SECOND_MEMBER_PID])
    async def test_failed_member_handle_close_keeps_the_job_owned_for_retry(
        self, job_kernel: JobKernel, failing_pid: int
    ) -> None:
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        job_kernel.signaled.update(PROCESS_HANDLES.values())
        await job.wait_until_idle(0)
        job_kernel.close_failures[PROCESS_HANDLES[failing_pid]] = 1
        with pytest.raises(OSError):
            job.close()
        assert JOB_HANDLE not in job_kernel.closed
        job.close()
        assert sorted(job_kernel.closed) == [JOB_HANDLE, *PROCESS_HANDLES.values()]

    async def test_unrelated_identity_handle_close_failure_is_retained_for_retry(
        self, job_kernel: JobKernel
    ) -> None:
        job_kernel.recycled_pid = FIRST_MEMBER_PID
        job_kernel.close_failures[UNRELATED_HANDLE] = 1
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        with pytest.raises(OSError):
            job.terminate()
        assert not job_kernel.closed
        assert not job_kernel.terminated
        job.terminate()
        job_kernel.signaled.add(PROCESS_HANDLES[SECOND_MEMBER_PID])
        await job.wait_until_idle(0)
        job.close()
        job_kernel.assert_handles_released()

    async def test_partial_capture_failure_retains_the_handles_already_opened(
        self, job_kernel: JobKernel
    ) -> None:
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job_kernel.inaccessible_pid = SECOND_MEMBER_PID
        with pytest.raises(OSError):
            job.terminate()
        assert not job_kernel.closed
        assert not job_kernel.terminated
        job_kernel.inaccessible_pid = None
        job.terminate()
        job_kernel.signaled.update(PROCESS_HANDLES.values())
        await job.wait_until_idle(0)
        job.close()
        job_kernel.assert_handles_released()

    async def test_cancelled_native_exit_wait_keeps_handles_for_explicit_retry(
        self, job_kernel: JobKernel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entered = asyncio.Event()
        native_wait = job_kernel.WaitForSingleObject

        def observed_wait(handle: ctypes.c_void_p | int, timeout: int) -> int:
            entered.set()
            return native_wait(handle, timeout)

        monkeypatch.setattr(job_kernel, "WaitForSingleObject", observed_wait)
        job = winspawn.Job(ctypes.c_void_p(JOB_HANDLE), "test")
        job.terminate()
        waiting = asyncio.create_task(job.wait_until_idle(30))
        observed = asyncio.create_task(entered.wait())
        try:
            done, _pending = await asyncio.wait(
                [waiting, observed], return_when=asyncio.FIRST_COMPLETED
            )
            assert observed in done
            assert not waiting.done()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not job_kernel.closed
            job_kernel.signaled.update(PROCESS_HANDLES.values())
            job.terminate()
            await job.wait_until_idle(0)
            job.close()
            assert sorted(job_kernel.closed) == [JOB_HANDLE, *PROCESS_HANDLES.values()]
        finally:
            waiting.cancel()
            observed.cancel()
            await asyncio.gather(waiting, observed, return_exceptions=True)
