"""Run a command inside a Windows job and tear that job down.

The parent creates the job and keeps a handle. This module, started as
``python -m band.integrations.codex.winspawn``, assigns the real command to
that job before the command runs, then waits. Close terminates the job, so
descendants die even after the launcher process itself has exited.
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
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from ctypes import wintypes
from typing import Any, NoReturn

logger = logging.getLogger(__name__)

MODULE_NAME = "band.integrations.codex.winspawn"

# CreateProcess failed because the executable path does not exist.
MISSING_EXECUTABLE = frozenset({2, 3})

_READY = "ok"
_STATUS_ERROR = "error"
_SPAWN_ACK_S = 30.0
_JOB_POLL_S = 0.05
_ERROR_BROKEN_PIPE = 109
_ERROR_ALREADY_EXISTS = 183

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_ASSIGN_PROCESS = 0x0001

_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_INFINITE = 0xFFFFFFFF
_WAIT_FAILED = 0xFFFFFFFF


class SpawnFailure(Exception):
    """The command never became a member of its job."""

    def __init__(self, winerror: int | None) -> None:
        self.winerror = winerror
        detail = "no status" if winerror is None else f"winerror {winerror}"
        super().__init__(detail)


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
    library.AssignProcessToJobObject.argtypes = [handle, handle]
    library.AssignProcessToJobObject.restype = wintypes.BOOL
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
    return library


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

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None and not _kernel32().CloseHandle(handle):
            logger.warning("CloseHandle failed: winerror %s", _last_error())

    def _open_handle(self) -> ctypes.c_void_p:
        handle = self._handle
        if handle is None:
            raise SpawnFailure(None)
        return handle

    def active_processes(self) -> int | None:
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

    def terminate(self) -> None:
        """Mark every member for termination.

        Members blocked in a synchronous pipe read stay inside that call until
        the other end closes. Wait only after that end is closed.
        """
        if not _kernel32().TerminateJobObject(self._open_handle(), 1):
            logger.warning(
                "TerminateJobObject failed: winerror %s",
                _last_error(),
            )

    async def wait_until_idle(self, timeout: float) -> None:
        """Wait until no members remain, then close the job handle."""
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                active = self.active_processes()
                if active == 0 or active is None:
                    return
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning("Codex job still has %s active processes", active)
                    return
                await asyncio.sleep(min(_JOB_POLL_S, remaining))
        finally:
            self.close()


_JOBS: dict[int, Job] = {}


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


async def _read_status(read_fd: int, timeout: float) -> str:
    handle = _os_handle(read_fd)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning("Codex spawn helper did not report readiness")
            raise SpawnFailure(None)
        available = wintypes.DWORD()
        if not _kernel32().PeekNamedPipe(
            ctypes.c_void_p(handle),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            err = _last_error()
            if err == _ERROR_BROKEN_PIPE and data:
                break
            if err != _ERROR_BROKEN_PIPE:
                logger.warning("PeekNamedPipe failed: winerror %s", err)
            raise SpawnFailure(None if err == _ERROR_BROKEN_PIPE else err)
        if available.value:
            data += os.read(read_fd, int(available.value))
        else:
            await asyncio.sleep(min(_JOB_POLL_S, remaining))
    return data.split(b"\n", 1)[0].decode("ascii", "replace").strip()


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


async def _stop_failed_spawn(
    process: asyncio.subprocess.Process, job: Job, timeout: float
) -> None:
    job.terminate()
    stdin = process.stdin
    if stdin is not None and not stdin.is_closing():
        stdin.close()
    await job.wait_until_idle(timeout)
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    if not await await_returncode(process, timeout):
        logger.warning(
            "Codex spawn helper %s still alive after job termination", process.pid
        )


async def spawn(
    command_for: Callable[[str, int], Sequence[str]],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    limit: int,
    observe_timeout: float,
) -> asyncio.subprocess.Process:
    """Start the wrapper and return it once the real command is in the job."""
    job = create_job()
    read_fd = -1
    write_fd = -1
    started: asyncio.subprocess.Process | None = None
    registered = False
    try:
        read_fd, write_fd = _status_pipe()
        status_handle = _os_handle(write_fd)
        startup = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        startup.lpAttributeList = {"handle_list": [status_handle]}
        started = await asyncio.create_subprocess_exec(
            *command_for(job.name, status_handle),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            startupinfo=startup,
            limit=limit,
        )
        line = await _read_status(read_fd, _SPAWN_ACK_S)
        if line != _READY:
            code = _error_code(line)
            logger.warning("Codex process spawn failed: winerror %s", code)
            raise SpawnFailure(code)
        _JOBS[started.pid] = job
        registered = True
        return started
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
        if not registered:
            if started is not None:
                await _stop_failed_spawn(started, job, observe_timeout)
            else:
                job.close()


def request_job_termination(pid: int) -> Job | None:
    """Mark ``pid``'s job for termination and return it, still open.

    The caller closes the child's stdin before ``Job.wait_until_idle``. A
    synchronous read stays inside the member until that write end closes.
    """
    job = _JOBS.pop(pid, None)
    if job is None:
        logger.warning("Codex process %s has no job to terminate", pid)
        return None
    job.terminate()
    return job


def _set_inheritable(handle: int, inherit: bool) -> None:
    flag = _HANDLE_FLAG_INHERIT if inherit else 0
    if not _kernel32().SetHandleInformation(
        ctypes.c_void_p(handle), _HANDLE_FLAG_INHERIT, flag
    ):
        raise _win_os_error(_last_error())


def _attribute_list(
    handles: ctypes.Array[ctypes.c_void_p],
) -> ctypes.Array[ctypes.c_char]:
    size = ctypes.c_size_t(0)
    _kernel32().InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise _win_os_error(_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not _kernel32().InitializeProcThreadAttributeList(
        buffer, 1, 0, ctypes.byref(size)
    ):
        raise _win_os_error(_last_error())
    if not _kernel32().UpdateProcThreadAttribute(
        buffer,
        0,
        _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        ctypes.cast(handles, ctypes.c_void_p),
        ctypes.sizeof(handles),
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
    attributes = _attribute_list(inherited)
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
        if not _kernel32().AssignProcessToJobObject(job, process):
            raise _win_os_error(_last_error())
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


def _start_and_wait(job_name: str, status_handle: int, command: Sequence[str]) -> int:
    job = _open_job(job_name)
    thread: ctypes.c_void_p | None = None
    try:
        try:
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
        _kernel32().CloseHandle(job)


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
