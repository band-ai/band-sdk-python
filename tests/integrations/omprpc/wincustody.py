"""Observe one real Windows RPC tree before its process gate is torn down."""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import platform
import sys
import time
from contextlib import ExitStack
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil
from pydantic import BaseModel, JsonValue

from band.integrations.codex import winspawn
from band.integrations.omprpc import OmpProcessError
from tests.integrations.omppeer import ProcessReceipt
from tests.integrations.omprpc.kit import (
    descendant_client,
    descendant_liveness,
    process_gate_session,
    process_is_alive,
    unrelated_gate_child,
)

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
PID_CAPACITY = 64
CLEANUP_WAIT_MS = 5000
EVIDENCE_PATH = Path("windows-custody.jsonl")


class Role(StrEnum):
    HELPER = "helper"
    TARGET = "target"
    DESCENDANT = "descendant"
    LAUNCHER = "launcher"
    UNRELATED = "unrelated"


class TargetReceipt(BaseModel):
    pid: int


class JobProcessList(ctypes.Structure):
    _fields_ = (
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.c_size_t * PID_CAPACITY),
    )


@dataclass
class WatchedProcess:
    role: Role
    process: psutil.Process
    created: float
    parent_pid: int
    handle: int
    creation_filetime: int
    receipt: ProcessReceipt | None


def filetime_value(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def kernel_api() -> Any:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = ctypes.c_void_p
    library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    library.OpenProcess.restype = handle
    library.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    library.OpenJobObjectW.restype = handle
    library.CloseHandle.argtypes = [handle]
    library.CloseHandle.restype = wintypes.BOOL
    library.GetProcessId.argtypes = [handle]
    library.GetProcessId.restype = wintypes.DWORD
    library.GetProcessTimes.argtypes = [
        handle,
        *([ctypes.POINTER(wintypes.FILETIME)] * 4),
    ]
    library.GetProcessTimes.restype = wintypes.BOOL
    library.IsProcessInJob.argtypes = [handle, handle, ctypes.POINTER(wintypes.BOOL)]
    library.IsProcessInJob.restype = wintypes.BOOL
    library.WaitForSingleObject.argtypes = [handle, wintypes.DWORD]
    library.WaitForSingleObject.restype = wintypes.DWORD
    library.GetExitCodeProcess.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
    library.GetExitCodeProcess.restype = wintypes.BOOL
    library.TerminateProcess.argtypes = [handle, wintypes.UINT]
    library.TerminateProcess.restype = wintypes.BOOL
    library.QueryInformationJobObject.argtypes = [
        handle,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    library.QueryInformationJobObject.restype = wintypes.BOOL
    return library


def windows_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]


class CustodyProbe:
    def __init__(self, stack: ExitStack) -> None:
        self.stack = stack
        self.kernel = kernel_api()
        self.processes: dict[int, WatchedProcess] = {}
        self.job: winspawn.Job | None = None
        self.query_handle: int | None = None
        self.original_create_job = winspawn.create_job
        self.sdk_job_handle_closed = False
        self.sequence = 0
        stack.enter_context(patch.object(winspawn, "create_job", self.create_job))

    def emit(self, event: str, **data: object) -> None:
        self.sequence += 1
        logger.info(
            "%s",
            json.dumps(
                {
                    "sequence": self.sequence,
                    "monotonic_ns": time.monotonic_ns(),
                    "event": event,
                    "before_fixture_teardown": True,
                    **data,
                },
                sort_keys=True,
            ),
        )

    def close_handle(self, handle: int) -> None:
        if not self.kernel.CloseHandle(handle):
            raise windows_error()

    def create_job(self) -> winspawn.Job:
        job = self.original_create_job()
        self.job = job
        self.original_terminate = job.terminate
        self.original_active_processes = job.active_processes
        self.original_close = job.close
        try:
            query = self.kernel.OpenJobObjectW(JOB_OBJECT_QUERY, False, job.name)
            if not query:
                raise windows_error()
            self.query_handle = int(query)
            self.stack.callback(self.close_query_handle)
            self.stack.enter_context(patch.object(job, "terminate", self.terminate))
            self.stack.enter_context(
                patch.object(job, "active_processes", self.active_processes)
            )
            self.stack.enter_context(patch.object(job, "close", self.close_sdk_job))
        except BaseException:
            job.close()
            raise
        self.emit(
            "job_created",
            job_name=job.name,
            observer_retains_query_handle=True,
            observer_retains_process_handles=True,
            explicit_termination_required=True,
        )
        return job

    def close_query_handle(self) -> None:
        if self.query_handle is not None:
            self.close_handle(self.query_handle)
            self.query_handle = None

    def creation_time(self, handle: int) -> int:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise windows_error()
        return filetime_value(created)

    def watch(
        self,
        role: Role,
        process: psutil.Process,
        receipt: ProcessReceipt | None = None,
    ) -> None:
        if process.pid in self.processes:
            return
        created = process.create_time()
        if receipt is not None:
            assert created == receipt.created
        rights = SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        if role != Role.UNRELATED:
            rights |= PROCESS_TERMINATE
        handle = self.kernel.OpenProcess(rights, False, process.pid)
        if not handle:
            raise windows_error()
        handle = int(handle)
        try:
            assert self.kernel.GetProcessId(handle) == process.pid
            kernel_created = self.creation_time(handle)
            assert psutil.Process(process.pid).create_time() == created
            watched = WatchedProcess(
                role=role,
                process=process,
                created=created,
                parent_pid=process.ppid(),
                handle=handle,
                creation_filetime=kernel_created,
                receipt=receipt,
            )
        except BaseException:
            self.close_handle(handle)
            raise
        self.processes[process.pid] = watched
        self.stack.callback(self.close_process_handle, watched)

    def close_process_handle(self, watched: WatchedProcess) -> None:
        if watched.handle:
            self.close_handle(watched.handle)
            watched.handle = 0

    def membership(self, process_handle: int, job_handle: int | None) -> bool:
        result = wintypes.BOOL()
        if not self.kernel.IsProcessInJob(
            process_handle, job_handle, ctypes.byref(result)
        ):
            raise windows_error()
        return bool(result.value)

    def process_snapshot(self, watched: WatchedProcess) -> dict[str, JsonValue]:
        handle = watched.handle
        errors: list[JsonValue] = []
        before = int(self.kernel.WaitForSingleObject(handle, 0))
        if before == WAIT_FAILED:
            errors.append(f"wait_before: {windows_error()}")
        in_job: bool | None = None
        in_any_job: bool | None = None
        try:
            if self.query_handle is not None:
                in_job = self.membership(handle, self.query_handle)
            in_any_job = self.membership(handle, None)
        except OSError as error:
            errors.append(f"membership: {error}")
        alive: bool | None = None
        status: str | None = None
        try:
            alive = process_is_alive(watched.process)
            status = watched.process.status()
        except psutil.NoSuchProcess:
            status = "NoSuchProcess"
        except psutil.Error as error:
            errors.append(f"psutil: {error}")
        code = wintypes.DWORD()
        exit_code: int | None = None
        if self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            exit_code = int(code.value)
        else:
            errors.append(f"exit_code: {windows_error()}")
        after = int(self.kernel.WaitForSingleObject(handle, 0))
        if after == WAIT_FAILED:
            errors.append(f"wait_after: {windows_error()}")
        return {
            "role": watched.role.value,
            "pid": watched.process.pid,
            "parent_pid": watched.parent_pid,
            "psutil_creation_time": watched.created,
            "kernel_creation_filetime": watched.creation_filetime,
            "receipt": None
            if watched.receipt is None
            else watched.receipt.model_dump(mode="json"),
            "is_in_client_job": in_job,
            "is_in_any_job": in_any_job,
            "kernel_wait_before_psutil": before,
            "kernel_wait_after_psutil": after,
            "kernel_exit_code": exit_code,
            "psutil_alive": alive,
            "psutil_status": status,
            "errors": errors,
        }

    def job_snapshot(self) -> dict[str, JsonValue]:
        if self.query_handle is None:
            return {"query_handle_closed": True}
        accounting = winspawn.BasicAccountingInformation()
        processes = JobProcessList()
        errors: list[JsonValue] = []
        active: int | None = None
        total: int | None = None
        terminated: int | None = None
        if self.kernel.QueryInformationJobObject(
            self.query_handle,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            active = int(accounting.ActiveProcesses)
            total = int(accounting.TotalProcesses)
            terminated = int(accounting.TotalTerminatedProcesses)
        else:
            errors.append(f"accounting: {windows_error()}")
        listed = bool(
            self.kernel.QueryInformationJobObject(
                self.query_handle,
                JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                ctypes.byref(processes),
                ctypes.sizeof(processes),
                None,
            )
        )
        if not listed:
            errors.append(f"pid_list: {windows_error()}")
        count = int(processes.NumberOfProcessIdsInList)
        complete = listed and count == int(processes.NumberOfAssignedProcesses)
        pids: list[JsonValue] = [
            int(processes.ProcessIdList[index])
            for index in range(min(count, PID_CAPACITY))
        ]
        return {
            "active_processes": active,
            "total_processes": total,
            "total_terminated_processes": terminated,
            "assigned_processes": int(processes.NumberOfAssignedProcesses),
            "pids": pids,
            "pid_list_complete": complete,
            "sdk_job_handle_closed": self.sdk_job_handle_closed,
            "errors": errors,
        }

    def snapshot(self, event: str) -> list[dict[str, JsonValue]]:
        before = self.job_snapshot()
        states = [
            self.process_snapshot(watched)
            for watched in self.processes.values()
            if watched.handle
        ]
        self.emit(
            event,
            job_before_process_samples=before,
            processes=states,
            job_after_process_samples=self.job_snapshot(),
        )
        return states

    def terminate(self) -> None:
        self.snapshot("before_TerminateJobObject")
        try:
            self.original_terminate()
        except BaseException as error:
            self.emit("TerminateJobObject_failed", error=repr(error))
            raise
        self.snapshot("after_TerminateJobObject")

    def active_processes(self) -> int | None:
        active = self.original_active_processes()
        self.emit("sdk_active_processes", active_processes=active)
        return active

    def close_sdk_job(self) -> None:
        self.snapshot("before_sdk_job_handle_close")
        self.original_close()
        self.sdk_job_handle_closed = True
        self.emit("sdk_job_handle_closed")

    def cleanup_owned(self) -> None:
        self.emit("emergency_cleanup_begins")
        if self.job is not None and not self.sdk_job_handle_closed:
            try:
                self.original_terminate()
            except OSError as error:
                self.emit("emergency_job_termination_failed", error=repr(error))
        for watched in self.processes.values():
            if watched.role == Role.UNRELATED or not watched.handle:
                continue
            state = int(self.kernel.WaitForSingleObject(watched.handle, 0))
            if state == WAIT_TIMEOUT and not self.kernel.TerminateProcess(
                watched.handle, 1
            ):
                self.emit(
                    "emergency_process_termination_failed",
                    pid=watched.process.pid,
                    error=str(windows_error()),
                )
            state = int(
                self.kernel.WaitForSingleObject(watched.handle, CLEANUP_WAIT_MS)
            )
            self.emit("emergency_process_wait", pid=watched.process.pid, wait=state)
        if self.job is not None and not self.sdk_job_handle_closed:
            self.original_close()
            self.sdk_job_handle_closed = True
        self.close_query_handle()
        for watched in self.processes.values():
            self.close_process_handle(watched)


async def observe_root_exit(probe: CustodyProbe) -> None:
    async with (
        process_gate_session() as gate,
        unrelated_gate_child(gate) as unrelated,
    ):
        probe.watch(Role.UNRELATED, unrelated.process, unrelated.receipt)
        client = descendant_client(gate)
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            async with asyncio.timeout(45):
                await client.start()
                root = client._proc
                assert root is not None
                probe.watch(Role.HELPER, psutil.Process(root.pid))
                target = TargetReceipt.model_validate(
                    await client.request("checkpoint")
                )
                probe.watch(Role.TARGET, psutil.Process(target.pid))
                descendants = [await gate.connection(), await gate.connection()]
                for child in descendants:
                    probe.watch(Role.DESCENDANT, child.process, child.receipt)
                for process in psutil.Process(root.pid).children(recursive=True):
                    probe.watch(Role.LAUNCHER, process)
                probe.snapshot("before_crash")
                assert await descendant_liveness(descendants) == [True, True]
                crashing = asyncio.create_task(client.request("crash"))
                async with asyncio.timeout(5):
                    await root.wait()
                probe.snapshot("after_helper_exit")
                probe.emit("helper_returncode", returncode=root.returncode)
                assert root.returncode == 3
                try:
                    await crashing
                except OmpProcessError as error:
                    probe.emit("crash_request_failed_as_expected", error=str(error))
                else:
                    raise AssertionError("Crash request unexpectedly succeeded")
                probe.snapshot("before_explicit_sdk_close")
                await client.close()
                states = probe.snapshot("after_sdk_close")
                alive_at_close = await descendant_liveness(descendants)
                probe.emit("original_liveness_after_close", alive=alive_at_close)
                # Keeping a query handle postpones kill-on-last-handle-close. Record
                # its release separately so it cannot masquerade as SDK termination.
                probe.close_query_handle()
                probe.snapshot("after_observer_job_handle_close")
                for watched in probe.processes.values():
                    state = int(probe.kernel.WaitForSingleObject(watched.handle, 0))
                    if state == WAIT_OBJECT_0 or watched.role == Role.UNRELATED:
                        probe.close_process_handle(watched)
                alive_without_exited_handles = await descendant_liveness(descendants)
                unrelated_alive = await asyncio.to_thread(
                    process_is_alive, unrelated.process
                )
                probe.emit(
                    "after_observer_exited_process_handles_close",
                    descendants_alive=alive_without_exited_handles,
                    unrelated_alive=unrelated_alive,
                    live_handles_retained=[
                        watched.process.pid
                        for watched in probe.processes.values()
                        if watched.handle
                    ],
                )
                assert unrelated_alive
                assert [
                    state["kernel_wait_after_psutil"]
                    for state in states
                    if state["role"] == Role.UNRELATED.value
                ] == [WAIT_TIMEOUT]
                assert [
                    state["kernel_wait_after_psutil"]
                    for state in states
                    if state["role"] == Role.DESCENDANT.value
                ] == [WAIT_OBJECT_0, WAIT_OBJECT_0]
                assert alive_at_close == [False, False]
                assert alive_without_exited_handles == [False, False]
        except BaseException as error:
            probe.emit("scenario_failed", error=repr(error))
            raise
        finally:
            try:
                await client.close()
            finally:
                try:
                    probe.cleanup_owned()
                finally:
                    if crashing is not None:
                        await asyncio.gather(crashing, return_exceptions=True)


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This diagnostic requires a real Windows runner")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(EVIDENCE_PATH, mode="w", encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with ExitStack() as stack:
            probe = CustodyProbe(stack)
            probe.emit(
                "runtime",
                python=sys.version,
                executable=sys.executable,
                windows=platform.platform(),
                psutil_version=psutil.__version__,
                wait_object_0=WAIT_OBJECT_0,
                wait_timeout=WAIT_TIMEOUT,
                diagnostic_only=True,
            )
            asyncio.run(observe_root_exit(probe))
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    main()
