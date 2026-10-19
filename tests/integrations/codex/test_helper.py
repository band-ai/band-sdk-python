"""The spawn helper borrows the job only while creating its child."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from band.integrations.codex import winspawn


class TestHelperJobLifetime:
    @pytest.mark.parametrize("resume_fails", [False, True])
    def test_job_is_released_before_readiness_or_child_wait(
        self, monkeypatch: pytest.MonkeyPatch, resume_fails: bool
    ) -> None:
        job = ctypes.c_void_p(1)
        process = ctypes.c_void_p(2)
        thread = ctypes.c_void_p(3)
        released: list[ctypes.c_void_p] = []

        def close_handle(handle: ctypes.c_void_p) -> bool:
            released.append(handle)
            return True

        def resume(handle: ctypes.c_void_p) -> None:
            assert job in released, "helper still owns job while child runs"
            if resume_fails:
                raise OSError("resume failed")

        def report(_handle: int, _code: int | None) -> None:
            assert job in released, "status promises ownership has transferred"

        def wait(_process: ctypes.c_void_p) -> int:
            assert job in released, "child wait must not retain job ownership"
            return 0

        monkeypatch.setattr(winspawn, "_open_job", lambda _name: job)
        monkeypatch.setattr(
            winspawn, "_spawn_suspended", lambda *_args: (process, thread)
        )
        monkeypatch.setattr(winspawn, "_close_stdio", lambda: None)
        monkeypatch.setattr(winspawn, "_resume", resume)
        monkeypatch.setattr(winspawn, "_write_status", report)
        monkeypatch.setattr(winspawn, "_wait_process", wait)
        monkeypatch.setattr(winspawn, "_winerror", lambda _exc: 5)
        monkeypatch.setattr(
            winspawn,
            "_kernel32",
            lambda: SimpleNamespace(
                CloseHandle=close_handle, TerminateProcess=lambda *_args: True
            ),
        )

        assert winspawn._start_and_wait("job", 4, ("command",)) == int(resume_fails)
        assert released.count(job) == 1
