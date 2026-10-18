"""Run a command inside a Windows job and tear that job down.

The parent retains the job handle. The isolated helper creates the real
command in that job before it can run, then waits. Close terminates the
job so descendants die even after their launcher exits.
"""

from __future__ import annotations

import asyncio
import ctypes
import functools
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from ctypes import wintypes
from pathlib import Path
from typing import Any, NoReturn

logger = logging.getLogger(__name__)

# CreateProcess failed because the executable path does not exist.
MISSING_EXECUTABLE = frozenset({2, 3})

_READY = "ok"
_STATUS_ERROR = "error"
_SPAWN_ACK_S = 30.0
_JOB_POLL_S = 0.05
_ERROR_ALREADY_EXISTS = 183
_ERROR_BROKEN_PIPE = 109
_ERROR_INVALID_PARAMETER = 87
_ERROR_MORE_DATA = 234
_STATUS_READ_SIZE = 256
_PATH_BUFFER_SIZE = 260

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_JOB_OBJECT_ASSIGN_PROCESS = 0x0001

_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF


class SpawnFailure(Exception):
    """Windows startup did not establish readiness."""

    def __init__(self, winerror: int | None) -> None:
        self.winerror = winerror
        detail = "no status" if winerror is None else f"winerror {winerror}"
        super().__init__(detail)


class JobNotDrained(Exception):
    """Termination has not established that every process exited."""

    def __init__(self, active: int | None) -> None:
        self.active = active
        super().__init__(active)


class BasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class IoCounters(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    )


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", BasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class BasicAccountingInformation(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    )


class BasicProcessIdList(ctypes.Structure):
    _fields_ = (
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.c_size_t * 1),
    )


class StartupInfo(ctypes.Structure):
    _fields_ = (
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    )


class StartupInfoEx(ctypes.Structure):
    _fields_ = (
        ("StartupInfo", StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    )


class ProcessInformation(ctypes.Structure):
    _fields_ = (
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    )


def _last_error() -> int:
    # These ctypes and subprocess attributes exist only on Windows.
    return int(ctypes.get_last_error())  # type: ignore[attr-defined]


def _clear_last_error() -> None:
    ctypes.set_last_error(0)  # type: ignore[attr-defined]


def _win_os_error(code: int) -> OSError:
    return ctypes.WinError(code)  # type: ignore[attr-defined]


def _msvcrt() -> Any:
    import msvcrt  # noqa: PLC0415 -- Windows-only stdlib; this module imports on every platform

    return msvcrt


@functools.cache
def _kernel32() -> Any:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = ctypes.c_void_p
    library.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    library.CreateJobObjectW.restype = handle
    library.SetInformationJobObject.argtypes = [
        handle,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    library.SetInformationJobObject.restype = wintypes.BOOL
    library.QueryInformationJobObject.argtypes = [
        handle,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    library.QueryInformationJobObject.restype = wintypes.BOOL
    library.TerminateJobObject.argtypes = [handle, wintypes.UINT]
    library.TerminateJobObject.restype = wintypes.BOOL
    library.CloseHandle.argtypes = [handle]
    library.CloseHandle.restype = wintypes.BOOL
    library.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    library.OpenJobObjectW.restype = handle
    library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    library.OpenProcess.restype = handle
    library.IsProcessInJob.argtypes = [handle, handle, ctypes.POINTER(wintypes.BOOL)]
    library.IsProcessInJob.restype = wintypes.BOOL
    library.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_wchar),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(StartupInfoEx),
        ctypes.POINTER(ProcessInformation),
    ]
    library.CreateProcessW.restype = wintypes.BOOL
    library.ResumeThread.argtypes = [handle]
    library.ResumeThread.restype = wintypes.DWORD
    library.TerminateProcess.argtypes = [handle, wintypes.UINT]
    library.TerminateProcess.restype = wintypes.BOOL
    library.WaitForSingleObject.argtypes = [handle, wintypes.DWORD]
    library.WaitForSingleObject.restype = wintypes.DWORD
    library.GetExitCodeProcess.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
    library.GetExitCodeProcess.restype = wintypes.BOOL
    library.SetHandleInformation.argtypes = [handle, wintypes.DWORD, wintypes.DWORD]
    library.SetHandleInformation.restype = wintypes.BOOL
    library.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    library.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    library.UpdateProcThreadAttribute.restype = wintypes.BOOL
    library.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    library.DeleteProcThreadAttributeList.restype = None
    library.WriteFile.argtypes = [
        handle,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    library.WriteFile.restype = wintypes.BOOL
    library.PeekNamedPipe.argtypes = [
        handle,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    library.PeekNamedPipe.restype = wintypes.BOOL
    library.GetModuleFileNameW.argtypes = [handle, wintypes.LPWSTR, wintypes.DWORD]
    library.GetModuleFileNameW.restype = wintypes.DWORD
    library.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    library.GetSystemDirectoryW.restype = wintypes.UINT
    library.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    library.GetWindowsDirectoryW.restype = wintypes.UINT
    library.GetEnvironmentVariableW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    library.GetEnvironmentVariableW.restype = wintypes.DWORD
    library.NeedCurrentDirectoryForExePathW.argtypes = [wintypes.LPCWSTR]
    library.NeedCurrentDirectoryForExePathW.restype = wintypes.BOOL
    library.SearchPathW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.c_void_p,
    ]
    library.SearchPathW.restype = wintypes.DWORD
    return library


def _native_path(function: Callable[..., int], *prefix: object) -> Path:
    capacity = _PATH_BUFFER_SIZE
    while True:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = function(*prefix, buffer, capacity)
        if not length:
            raise _win_os_error(_last_error())
        if length < capacity:
            return Path(buffer.value)
        capacity = max(capacity * 2, length + 1)


def _parent_path() -> str:
    capacity = _PATH_BUFFER_SIZE
    while True:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = _kernel32().GetEnvironmentVariableW("PATH", buffer, capacity)
        if length < capacity:
            return buffer.value
        capacity = length + 1


def process_image() -> Path:
    """Bypass Windows venv launchers so the retained process is the helper."""
    if os.name != "nt":
        return Path(sys.executable)
    return _native_path(_kernel32().GetModuleFileNameW, None)


def find_executable(name: str) -> str | None:
    """Resolve a bare name using the parent's CreateProcess search order."""
    library = _kernel32()
    windows = _native_path(library.GetWindowsDirectoryW)
    directories = [process_image().parent]
    if library.NeedCurrentDirectoryForExePathW(name):
        directories.append(Path.cwd())
    directories.extend(
        [
            _native_path(library.GetSystemDirectoryW),
            windows / "System",
            windows,
        ]
    )
    search_path = os.pathsep.join(str(directory) for directory in directories)
    if parent_path := _parent_path():
        search_path += os.pathsep + parent_path
    # An explicit path avoids SearchPath's registry-dependent default order.
    capacity = _PATH_BUFFER_SIZE
    while True:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = library.SearchPathW(search_path, name, ".exe", capacity, buffer, None)
        if not length:
            return None
        if length < capacity:
            return buffer.value
        capacity = length + 1


def _winerror(exc: OSError) -> int:
    code = exc.winerror  # type: ignore[attr-defined]
    if isinstance(code, int) and code:
        return code
    return _last_error()


def _fail(api: str, code: int | None = None) -> NoReturn:
    winerror = _last_error() if code is None else code
    logger.warning("%s failed: winerror %s", api, winerror)
    raise SpawnFailure(winerror)


class Job:
    """A job handle owned by the parent process."""

    def __init__(self, handle: ctypes.c_void_p, name: str) -> None:
        self._handle: ctypes.c_void_p | None = handle
        self.name = name
        self._processes: dict[int, ctypes.c_void_p] = {}
        self._members_captured = False

    def close(self) -> None:
        if self._processes:
            raise JobNotDrained(None)
        handle = self._handle
        if handle is not None and not _kernel32().CloseHandle(handle):
            raise _win_os_error(_last_error())
        self._handle = None

    def _open_handle(self) -> ctypes.c_void_p:
        handle = self._handle
        if handle is None:
            raise SpawnFailure(None)
        return handle

    def active_processes(self) -> int | None:
        if self._handle is None:
            return 0
        info = BasicAccountingInformation()
        if not _kernel32().QueryInformationJobObject(
            self._open_handle(),
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            logger.warning(
                "QueryInformationJobObject failed: winerror %s",
                _last_error(),
            )
            return None
        return int(info.ActiveProcesses)

    def _seal_membership(self) -> None:
        # A zero limit denies late children without terminating existing members.
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        limits.BasicLimitInformation.ActiveProcessLimit = 0
        if not _kernel32().SetInformationJobObject(
            self._open_handle(),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _win_os_error(_last_error())

    def _member_pids(self) -> tuple[int, ...]:
        capacity = 1
        while True:
            buffer = ctypes.create_string_buffer(
                ctypes.sizeof(BasicProcessIdList)
                + (capacity - 1) * ctypes.sizeof(ctypes.c_size_t)
            )
            info = ctypes.cast(buffer, ctypes.POINTER(BasicProcessIdList)).contents
            complete = _kernel32().QueryInformationJobObject(
                self._open_handle(),
                _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                buffer,
                ctypes.sizeof(buffer),
                None,
            )
            if not complete and (code := _last_error()) != _ERROR_MORE_DATA:
                raise _win_os_error(code)
            assigned = int(info.NumberOfAssignedProcesses)
            count = int(info.NumberOfProcessIdsInList)
            if not complete or count < assigned:
                capacity = max(capacity * 2, assigned)
                continue
            values = ctypes.cast(
                ctypes.byref(buffer, BasicProcessIdList.ProcessIdList.offset),
                ctypes.POINTER(ctypes.c_size_t),
            )
            return tuple(int(values[index]) for index in range(count))

    def _capture_members(self) -> None:
        for pid in self._member_pids():
            if pid in self._processes:
                continue
            process = _kernel32().OpenProcess(
                _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not process:
                code = _last_error()
                if code == _ERROR_INVALID_PARAMETER and pid not in self._member_pids():
                    continue
                raise _win_os_error(code)
            self._processes[pid] = process
        # Retained handles keep object identity stable across PID reuse. Failed
        # membership checks keep the handles owned, but cannot authorize termination.
        for pid, process in tuple(self._processes.items()):
            member = wintypes.BOOL()
            if not _kernel32().IsProcessInJob(
                process, self._open_handle(), ctypes.byref(member)
            ):
                raise _win_os_error(_last_error())
            if not member.value:
                self._close_process_handle(pid)

    def _close_process_handle(self, pid: int) -> None:
        if not _kernel32().CloseHandle(self._processes[pid]):
            raise _win_os_error(_last_error())
        del self._processes[pid]

    def _reap_members(self) -> None:
        for pid, process in tuple(self._processes.items()):
            state = _kernel32().WaitForSingleObject(process, 0)
            if state == _WAIT_TIMEOUT:
                continue
            if state != _WAIT_OBJECT_0:
                raise _win_os_error(_last_error())
            self._close_process_handle(pid)

    def terminate(self) -> None:
        """Retain physical-exit evidence before marking members for termination.

        Members blocked in a synchronous pipe read stay inside that call until
        the other end closes. Wait only after that end is closed.
        """
        if not self._members_captured:
            self._seal_membership()
            self._capture_members()
            self._members_captured = True
        if not _kernel32().TerminateJobObject(self._open_handle(), 1):
            raise _win_os_error(_last_error())

    async def wait_until_idle(self, timeout: float) -> None:
        """Observe physical exit and job accounting without releasing the job."""
        if self._processes and not self._members_captured:
            raise JobNotDrained(None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self._reap_members()
            active = self.active_processes()
            if active == 0 and not self._processes:
                return
            remaining = deadline - loop.time()
            if active is None or remaining <= 0:
                logger.warning(
                    "Codex job still has %s active processes and %s pending exits",
                    active,
                    len(self._processes),
                )
                raise JobNotDrained(active)
            await asyncio.sleep(min(_JOB_POLL_S, remaining))


def create_job() -> Job:
    name = f"Local\\band-codex-{uuid.uuid4().hex}"
    _clear_last_error()
    handle = _kernel32().CreateJobObjectW(None, name)
    if not handle:
        _fail("CreateJobObject")
    if _last_error() == _ERROR_ALREADY_EXISTS:
        _kernel32().CloseHandle(handle)
        _fail("CreateJobObject", _ERROR_ALREADY_EXISTS)
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32().SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        code = _last_error()
        _kernel32().CloseHandle(handle)
        _fail("SetInformationJobObject", code)
    return Job(handle, name)


def _status_pipe() -> tuple[int, int]:
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(write_fd, True)
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        raise
    return read_fd, write_fd


def _os_handle(fd: int) -> int:
    return int(_msvcrt().get_osfhandle(fd))


def _error_code(line: str) -> int | None:
    prefix, _, code = line.partition(" ")
    if prefix == _STATUS_ERROR and code.isdigit():
        return int(code)
    return None


def _read_available(read_fd: int) -> bytes:
    size = _STATUS_READ_SIZE
    if os.name == "nt":
        available = wintypes.DWORD()
        if not _kernel32().PeekNamedPipe(
            ctypes.c_void_p(_os_handle(read_fd)),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            code = _last_error()
            if code == _ERROR_BROKEN_PIPE:
                return b""
            raise _win_os_error(code)
        if not available.value:
            raise BlockingIOError
        size = min(size, available.value)
    return os.read(read_fd, size)


async def _read_status(read_fd: int, timeout: float) -> str:
    # Windows 3.11 lacks os.set_blocking; the sole reader peeks before reading.
    if os.name != "nt":
        os.set_blocking(read_fd, False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning("Codex spawn helper did not report readiness")
            raise SpawnFailure(None)
        try:
            chunk = _read_available(read_fd)
        except BlockingIOError:
            await asyncio.sleep(min(_JOB_POLL_S, remaining))
            continue
        if not chunk:
            break
        data += chunk
    line = data.split(b"\n", 1)[0].decode("ascii", "replace").strip()
    if not line:
        logger.warning("Codex spawn helper did not report readiness")
        raise SpawnFailure(None)
    return line


async def await_returncode(process: asyncio.subprocess.Process, timeout: float) -> bool:
    """True once ``process.returncode`` is set.

    ``Process.wait`` stays pending while a descendant holds a stdio pipe, and
    cancelling that wait on Windows never finishes. The transport publishes
    ``returncode`` when the direct child exits.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while process.returncode is None:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_JOB_POLL_S, remaining))
    return True


async def close_stdin(process: asyncio.subprocess.Process) -> None:
    """Drop the write end so a synchronous read in the child can finish.

    ``wait_closed`` does not return while that read is still in progress, and
    cancelling the wait on Windows never finishes. Closing the transport is
    enough: the loop drops the OS handle on its next turn.
    """
    stdin = process.stdin
    if stdin is None or stdin.is_closing():
        return
    stdin.close()
    await asyncio.sleep(0)


async def _launch_helper(
    command: Sequence[str],
    status_handle: int,
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    limit: int,
) -> asyncio.subprocess.Process:
    startup = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startup.lpAttributeList = {"handle_list": [status_handle]}
    return await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        startupinfo=startup,
        limit=limit,
    )


class WindowsProcess:
    """One client-owned helper and job, including incomplete startup."""

    def __init__(self) -> None:
        self.job: Job | None = create_job()
        self.process: asyncio.subprocess.Process | None = None
        self.closing = False
        self._creation: asyncio.Task[None] | None = None
        self._status: asyncio.Task[str] | None = None
        self._read_fd: int | None = None

    async def start(
        self,
        command_for: Callable[[str, int], Sequence[str]],
        *,
        cwd: str | None,
        env: Mapping[str, str] | None,
        limit: int,
    ) -> asyncio.subprocess.Process:
        if self.closing or self._creation is not None:
            raise SpawnFailure(None)
        self._creation = asyncio.create_task(
            self._create(command_for, cwd=cwd, env=env, limit=limit)
        )
        # Cancelling connect must not discard a helper created by the transport.
        await asyncio.shield(self._creation)
        if self.closing or self._read_fd is None:
            raise SpawnFailure(None)
        self._status = asyncio.create_task(self._ready())
        line = await asyncio.shield(self._status)
        if self.closing:
            raise SpawnFailure(None)
        if line != _READY:
            code = _error_code(line)
            logger.warning("Codex process spawn failed: winerror %s", code)
            raise SpawnFailure(code)
        assert self.process is not None
        return self.process

    async def _create(
        self,
        command_for: Callable[[str, int], Sequence[str]],
        *,
        cwd: str | None,
        env: Mapping[str, str] | None,
        limit: int,
    ) -> None:
        assert self.job is not None
        self._read_fd, write_fd = _status_pipe()
        try:
            status_handle = _os_handle(write_fd)
            self.process = await _launch_helper(
                command_for(self.job.name, status_handle),
                status_handle,
                cwd=cwd,
                env=env,
                limit=limit,
            )
        finally:
            # Only the helper may keep the writer; its exit must produce EOF.
            os.close(write_fd)

    async def _ready(self) -> str:
        assert self._read_fd is not None
        try:
            return await _read_status(self._read_fd, _SPAWN_ACK_S)
        finally:
            self._close_status_pipe()

    def _close_status_pipe(self) -> None:
        read_fd, self._read_fd = self._read_fd, None
        if read_fd is not None:
            os.close(read_fd)

    async def close(self, timeout: float) -> None:
        self.closing = True
        if self._creation is not None:
            done, _pending = await asyncio.wait([self._creation], timeout=timeout)
            if not done:
                raise TimeoutError("Codex spawn helper creation is still pending")
            # A failed launch still owns its empty job.
            self._creation.exception()
        if self._status is not None:
            self._status.cancel()
            await asyncio.gather(self._status, return_exceptions=True)
        self._close_status_pipe()
        if (process := self.process) is not None:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            if not await await_returncode(process, timeout):
                raise TimeoutError("Codex spawn helper did not exit")
        if (job := self.job) is not None:
            job.terminate()
            if process is not None:
                await close_stdin(process)
            await job.wait_until_idle(timeout)
            job.close()
            self.job = None


def _set_inheritable(handle: int, inherit: bool) -> None:
    flag = _HANDLE_FLAG_INHERIT if inherit else 0
    if not _kernel32().SetHandleInformation(
        ctypes.c_void_p(handle), _HANDLE_FLAG_INHERIT, flag
    ):
        raise _win_os_error(_last_error())


def _attribute_list(
    entries: Sequence[tuple[int, ctypes.c_void_p, int]],
) -> ctypes.Array[ctypes.c_char]:
    count = len(entries)
    size = ctypes.c_size_t(0)
    _kernel32().InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
    if not size.value:
        raise _win_os_error(_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not _kernel32().InitializeProcThreadAttributeList(
        buffer, count, 0, ctypes.byref(size)
    ):
        raise _win_os_error(_last_error())
    for attribute, pointer, nbytes in entries:
        if not _kernel32().UpdateProcThreadAttribute(
            buffer,
            0,
            attribute,
            pointer,
            nbytes,
            None,
            None,
        ):
            code = _last_error()
            _kernel32().DeleteProcThreadAttributeList(buffer)
            raise _win_os_error(code)
    return buffer


def _standard_handles() -> tuple[int, int, int]:
    msvcrt = _msvcrt()
    return (
        int(msvcrt.get_osfhandle(0)),
        int(msvcrt.get_osfhandle(1)),
        int(msvcrt.get_osfhandle(2)),
    )


def _spawn_suspended(
    job: ctypes.c_void_p, command: Sequence[str], status_handle: int
) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
    """Create the command suspended and assign it before it can run."""
    _set_inheritable(status_handle, False)
    standard = _standard_handles()
    for handle in standard:
        _set_inheritable(handle, True)
    inherited = (ctypes.c_void_p * 3)(*(ctypes.c_void_p(handle) for handle in standard))
    jobs = (ctypes.c_void_p * 1)(job)
    # Job membership is fixed at creation. Assigning afterwards leaves a
    # window where killing this helper orphans a suspended process outside
    # the job, and that process keeps the room directory open.
    attributes = _attribute_list(
        (
            (
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(inherited, ctypes.c_void_p),
                ctypes.sizeof(inherited),
            ),
            (
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.cast(jobs, ctypes.c_void_p),
                ctypes.sizeof(jobs),
            ),
        )
    )
    process: ctypes.c_void_p | None = None
    thread: ctypes.c_void_p | None = None
    try:
        startup = StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(StartupInfoEx)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = standard[0]
        startup.StartupInfo.hStdOutput = standard[1]
        startup.StartupInfo.hStdError = standard[2]
        startup.lpAttributeList = ctypes.addressof(attributes)
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        info = ProcessInformation()
        if not _kernel32().CreateProcessW(
            None,
            cmdline,
            None,
            None,
            True,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED,
            None,
            None,
            ctypes.byref(startup),
            ctypes.byref(info),
        ):
            raise _win_os_error(_last_error())
        process = info.hProcess
        thread = info.hThread
        child_thread = thread
        thread = None
        if process is None or child_thread is None:
            raise _win_os_error(_last_error())
        return process, child_thread
    except OSError:
        if process is not None:
            _kernel32().TerminateProcess(process, 1)
            _kernel32().CloseHandle(process)
        raise
    finally:
        _kernel32().DeleteProcThreadAttributeList(attributes)
        if thread is not None:
            _kernel32().CloseHandle(thread)


def _close_stdio() -> None:
    for fd in (0, 1, 2):
        try:
            os.close(fd)
        except OSError:
            pass


def _write_status(handle: int, winerror: int | None) -> None:
    text = _READY if winerror is None else f"{_STATUS_ERROR} {winerror}"
    payload = f"{text}\n".encode("ascii")
    written = wintypes.DWORD()
    buffer = ctypes.create_string_buffer(payload)
    _kernel32().WriteFile(
        ctypes.c_void_p(handle),
        ctypes.cast(buffer, ctypes.c_void_p),
        len(payload),
        ctypes.byref(written),
        None,
    )
    _kernel32().CloseHandle(ctypes.c_void_p(handle))


def _wait_process(process: ctypes.c_void_p) -> int:
    try:
        if _kernel32().WaitForSingleObject(process, _INFINITE) == _WAIT_FAILED:
            return 1
        code = wintypes.DWORD()
        if not _kernel32().GetExitCodeProcess(process, ctypes.byref(code)):
            return 1
        return int(code.value)
    finally:
        _kernel32().CloseHandle(process)


def _open_job(name: str) -> ctypes.c_void_p:
    handle = _kernel32().OpenJobObjectW(_JOB_OBJECT_ASSIGN_PROCESS, False, name)
    if not handle:
        raise _win_os_error(_last_error())
    return handle


def _resume(thread: ctypes.c_void_p) -> None:
    if _kernel32().ResumeThread(thread) == _WAIT_FAILED:
        raise _win_os_error(_last_error())


@contextmanager
def _borrow_job(name: str) -> Iterator[ctypes.c_void_p]:
    job = _open_job(name)
    try:
        yield job
    finally:
        if not _kernel32().CloseHandle(job):
            raise _win_os_error(_last_error())


def _start_and_wait(job_name: str, status_handle: int, command: Sequence[str]) -> int:
    thread: ctypes.c_void_p | None = None
    try:
        try:
            with _borrow_job(job_name) as job:
                process, thread = _spawn_suspended(job, command, status_handle)
        except OSError as exc:
            _write_status(status_handle, _winerror(exc))
            return 1
        # The child already holds its stdio handles and is still suspended.
        # Drop ours before it runs so a later close cannot overlap a read.
        _close_stdio()
        try:
            if thread is None:
                raise _win_os_error(_last_error())
            _resume(thread)
        except OSError as exc:
            _kernel32().TerminateProcess(process, 1)
            _kernel32().CloseHandle(process)
            _write_status(status_handle, _winerror(exc))
            return 1
        _write_status(status_handle, None)
        return _wait_process(process)
    finally:
        if thread is not None:
            _kernel32().CloseHandle(thread)


def _parse(argv: Sequence[str]) -> tuple[str, int, tuple[str, ...]]:
    try:
        separator = argv.index("--")
        status_handle = int(argv[1])
    except (ValueError, IndexError):
        raise SystemExit(2) from None
    if separator != 2 or not argv[0] or separator + 1 >= len(argv):
        raise SystemExit(2)
    return argv[0], status_handle, tuple(argv[separator + 1 :])


def main() -> int:
    if os.name != "nt":
        return 2
    job_name, status_handle, command = _parse(sys.argv[1:])
    return _start_and_wait(job_name, status_handle, command)


if __name__ == "__main__":
    raise SystemExit(main())
