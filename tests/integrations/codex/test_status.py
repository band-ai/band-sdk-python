"""Status polling never blocks or relies on Windows os.set_blocking."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from band.integrations.codex import winspawn


class TestWindowsStatusPipe:
    @pytest.mark.parametrize("available", [0, 3, winspawn._STATUS_READ_SIZE + 1])
    def test_reads_only_available_bytes(
        self, monkeypatch: pytest.MonkeyPatch, available: int
    ) -> None:
        def peek(
            _pipe: object,
            _buffer: object,
            _size: int,
            _read: object,
            count: object,
            _remaining: object,
        ) -> bool:
            ctypes.cast(
                count, ctypes.POINTER(wintypes.DWORD)
            ).contents.value = available
            return True

        def read(_fd: int, size: int) -> bytes:
            assert size <= available, "read would block waiting for more bytes"
            return b"x" * size

        monkeypatch.setattr(winspawn, "os", SimpleNamespace(name="nt", read=read))
        monkeypatch.setattr(winspawn, "_os_handle", lambda fd: fd)
        monkeypatch.setattr(
            winspawn, "_kernel32", lambda: SimpleNamespace(PeekNamedPipe=peek)
        )
        if available:
            assert winspawn._read_available(1) == b"x" * min(
                available, winspawn._STATUS_READ_SIZE
            )
        else:
            with pytest.raises(BlockingIOError):
                winspawn._read_available(1)

    @pytest.mark.parametrize("code", [winspawn._ERROR_BROKEN_PIPE, 5])
    def test_distinguishes_writer_exit_from_native_failure(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        monkeypatch.setattr(winspawn, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(winspawn, "_os_handle", lambda fd: fd)
        monkeypatch.setattr(winspawn, "_last_error", lambda: code)
        monkeypatch.setattr(winspawn, "_win_os_error", lambda error: OSError(error))
        monkeypatch.setattr(
            winspawn,
            "_kernel32",
            lambda: SimpleNamespace(PeekNamedPipe=lambda *_args: False),
        )
        if code == winspawn._ERROR_BROKEN_PIPE:
            assert winspawn._read_available(1) == b""
        else:
            with pytest.raises(OSError):
                winspawn._read_available(1)
