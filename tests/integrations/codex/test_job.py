"""Native handle errors preserve the job's retryable ownership."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from band.integrations.codex import winspawn


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

    def test_failed_termination_is_reported_without_releasing_the_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handle = ctypes.c_void_p(1)
        job = winspawn.Job(handle, "test")
        terminated: list[ctypes.c_void_p] = []

        def terminate(current: ctypes.c_void_p, _code: int) -> bool:
            terminated.append(current)
            return len(terminated) > 1

        monkeypatch.setattr(
            winspawn,
            "_kernel32",
            lambda: SimpleNamespace(TerminateJobObject=terminate),
        )
        monkeypatch.setattr(winspawn, "_last_error", lambda: 5)
        monkeypatch.setattr(winspawn, "_win_os_error", lambda code: OSError(code))
        with pytest.raises(OSError):
            job.terminate()
        job.terminate()
        assert terminated == [handle, handle]
