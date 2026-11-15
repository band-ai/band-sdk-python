"""Control Job accounting independently of native process-exit signals."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from band.integrations.codex.winspawn import (
    BasicAccountingInformation,
    ExtendedLimitInformation,
    ProcessIdList,
)

JOB_HANDLE = 1
FIRST_MEMBER_PID = 21
SECOND_MEMBER_PID = 22
PROCESS_HANDLES = {FIRST_MEMBER_PID: 121, SECOND_MEMBER_PID: 122}
KILL_ON_JOB_CLOSE = 0x2000
UNRELATED_HANDLE = 999


def handle_value(handle: ctypes.c_void_p | int) -> int:
    if isinstance(handle, int):
        return handle
    assert handle.value is not None
    return handle.value


class JobKernel:
    def __init__(self) -> None:
        self.pids = list(PROCESS_HANDLES)
        self.signaled: set[int] = set()
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.terminated: list[int] = []
        self.termination_attempts: list[int] = []
        self.close_failures: dict[int, int] = {}
        self.termination_failures = 0
        self.wait_fails = False
        self.last_error = 0
        self.admission_sealed = False
        self.fork_during_open = False
        self.late_fork_rejected = False
        self.recycled_pid: int | None = None
        self.missing_pid: int | None = None
        self.inaccessible_pid: int | None = None
        self.limits = ExtendedLimitInformation()
        self.limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE

    def QueryInformationJobObject(
        self,
        job: ctypes.c_void_p | int,
        information_class: int,
        information: Any,
        size: int,
        returned: object,
    ) -> bool:
        assert handle_value(job) == JOB_HANDLE
        match information_class:
            case 1:
                accounting = ctypes.cast(
                    information, ctypes.POINTER(BasicAccountingInformation)
                ).contents
                accounting.ActiveProcesses = 0 if self.terminated else len(self.pids)
                return True
            case 3:
                header = ctypes.cast(
                    information, ctypes.POINTER(ProcessIdList)
                ).contents
                pids = [] if self.terminated else self.pids
                capacity = (size - ProcessIdList.ProcessIdList.offset) // ctypes.sizeof(
                    ctypes.c_size_t
                )
                header.NumberOfAssignedProcesses = len(pids)
                header.NumberOfProcessIdsInList = min(len(pids), capacity)
                address = ctypes.addressof(header) + ProcessIdList.ProcessIdList.offset
                buffer = (ctypes.c_size_t * capacity).from_address(address)
                for index, pid in enumerate(pids[:capacity]):
                    buffer[index] = pid
                if len(pids) > capacity:
                    self.last_error = 234
                    return False
                return True
            case 9:
                assert size == ctypes.sizeof(self.limits)
                ctypes.memmove(information, ctypes.byref(self.limits), size)
                return True
            case _:
                raise AssertionError(f"Unexpected Job query class: {information_class}")

    def SetInformationJobObject(
        self,
        job: ctypes.c_void_p | int,
        information_class: int,
        information: Any,
        size: int,
    ) -> bool:
        assert handle_value(job) == JOB_HANDLE
        assert information_class == 9
        assert size == ctypes.sizeof(self.limits)
        ctypes.memmove(ctypes.byref(self.limits), information, size)
        basic = self.limits.BasicLimitInformation
        self.admission_sealed = (
            bool(basic.LimitFlags & 8) and basic.ActiveProcessLimit == 0
        )
        return True

    def OpenProcess(self, rights: int, inherit: bool, pid: int) -> int | None:
        assert rights & 0x00100000, "process handles must support waiting"
        assert rights & 0x1000, "process handles must support Job membership queries"
        assert not inherit
        if self.fork_during_open:
            self.fork_during_open = False
            self.late_fork_rejected = self.admission_sealed
            if not self.admission_sealed:
                self.pids.append(23)
        if pid == self.missing_pid:
            self.last_error = 87
            return None
        if pid == self.inaccessible_pid:
            self.last_error = 5
            return None
        handle = UNRELATED_HANDLE if pid == self.recycled_pid else PROCESS_HANDLES[pid]
        self.opened.append(handle)
        return handle

    def IsProcessInJob(
        self, process: ctypes.c_void_p | int, job: ctypes.c_void_p | int, result: Any
    ) -> bool:
        assert handle_value(job) == JOB_HANDLE
        ctypes.cast(result, ctypes.POINTER(wintypes.BOOL)).contents.value = (
            handle_value(process) != UNRELATED_HANDLE
        )
        return True

    def TerminateJobObject(self, job: ctypes.c_void_p | int, code: int) -> bool:
        assert code == 1
        self.termination_attempts.append(handle_value(job))
        if self.termination_failures:
            self.termination_failures -= 1
            self.last_error = 5
            return False
        self.terminated.append(handle_value(job))
        return True

    def WaitForSingleObject(self, process: ctypes.c_void_p | int, timeout: int) -> int:
        assert timeout == 0, "native probes must not block the event loop"
        if self.wait_fails:
            self.last_error = 6
            return 0xFFFFFFFF
        return 0 if handle_value(process) in self.signaled else 258

    def CloseHandle(self, handle: ctypes.c_void_p | int) -> bool:
        value = handle_value(handle)
        remaining = self.close_failures.get(value, 0)
        if remaining:
            self.close_failures[value] = remaining - 1
            self.last_error = 5
            return False
        self.closed.append(value)
        return True

    def assert_handles_released(self) -> None:
        """Retirement releases every handle acquired across failed attempts."""
        assert sorted(self.closed) == sorted([JOB_HANDLE, *self.opened])
