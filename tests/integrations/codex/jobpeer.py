"""Native job boundary where accounting can finish before physical process exit."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

from band.integrations.codex.winspawn import (
    BasicAccountingInformation,
    ExtendedLimitInformation,
)


class NativeJobPeer:
    def __init__(self) -> None:
        self.handle = ctypes.c_void_p(1)
        self.pids = [101]
        self.exited: set[int] = set()
        self.reused: set[int] = set()
        self.opened: set[int] = set()
        self.waited: list[int] = []
        self.active = 1
        self.error = 0
        self.open_error = 0
        self.wait_error = False
        self.close_error = False
        self.query_error = False
        self.terminated = False
        self.closed = False
        self.sealed = False
        self.kernel = SimpleNamespace(
            SetInformationJobObject=self.set_limits,
            QueryInformationJobObject=self.query,
            TerminateJobObject=self.terminate,
            OpenProcess=self.open_process,
            IsProcessInJob=self.is_member,
            WaitForSingleObject=self.wait,
            CloseHandle=self.close,
        )

    def set_limits(
        self, handle: ctypes.c_void_p, kind: int, data: object, size: int
    ) -> bool:
        assert handle is self.handle
        assert kind == 9
        assert size == ctypes.sizeof(ExtendedLimitInformation)
        limits = ctypes.cast(data, ctypes.POINTER(ExtendedLimitInformation)).contents
        basic = limits.BasicLimitInformation
        assert basic.LimitFlags & 0x2000, "sealing must preserve kill-on-close"
        self.sealed = bool(basic.LimitFlags & 8 and basic.ActiveProcessLimit == 0)
        return True

    def query(
        self,
        handle: ctypes.c_void_p,
        kind: int,
        data: object,
        size: int,
        returned: object,
    ) -> bool:
        assert handle is self.handle
        if self.query_error:
            self.error = 5
            return False
        if kind == 1:
            info = ctypes.cast(
                data, ctypes.POINTER(BasicAccountingInformation)
            ).contents
            info.ActiveProcesses = self.active
            return True
        assert kind == 3
        assert self.sealed, "new descendants must be excluded before the snapshot"
        assert not self.terminated, "termination can remove unsignaled members"
        header_size = 2 * ctypes.sizeof(wintypes.DWORD)
        header = ctypes.cast(data, ctypes.POINTER(wintypes.DWORD))
        capacity = (size - header_size) // ctypes.sizeof(ctypes.c_size_t)
        header[0] = len(self.pids)
        header[1] = min(capacity, len(self.pids))
        address = ctypes.cast(data, ctypes.c_void_p).value
        assert address is not None
        entries = (ctypes.c_size_t * header[1]).from_address(address + header_size)
        entries[:] = self.pids[: header[1]]
        if capacity < len(self.pids):
            self.error = 234
            return False
        return True

    def terminate(self, handle: ctypes.c_void_p, code: int) -> bool:
        assert handle is self.handle
        assert code == 1
        self.terminated = True
        self.active = 0
        return True

    def open_process(
        self, access: int, inherit: bool, pid: int
    ) -> ctypes.c_void_p | None:
        assert access == 0x101000
        assert not inherit
        if self.open_error:
            self.error = self.open_error
            return None
        self.opened.add(pid)
        return ctypes.c_void_p(pid)

    def is_member(
        self, process: ctypes.c_void_p, job: ctypes.c_void_p, result: object
    ) -> bool:
        assert job is self.handle
        ctypes.cast(result, ctypes.POINTER(wintypes.BOOL))[0] = (
            process.value not in self.reused
        )
        return True

    def wait(self, handle: ctypes.c_void_p, timeout: int) -> int:
        assert timeout == 0, "process waits must not block the event loop"
        assert handle.value is not None
        self.waited.append(handle.value)
        if self.wait_error:
            self.error = 6
            return 0xFFFFFFFF
        return 0 if handle.value in self.exited else 258

    def close(self, handle: ctypes.c_void_p) -> bool:
        if self.close_error:
            self.error = 6
            return False
        if handle is self.handle:
            self.closed = True
        else:
            assert handle.value is not None
            self.opened.remove(handle.value)
        return True
