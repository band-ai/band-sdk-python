"""Check a populated Windows Job's zero-process admission limit on the kernel."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import platform
import sys
from contextlib import ExitStack
from pathlib import Path

import psutil
from pydantic import BaseModel

from band.integrations.codex import winspawn
from tests.integrations.omprpc.kit import (
    descendant_client,
    descendant_liveness,
    process_gate_session,
    unrelated_gate_child,
)
from tests.integrations.omprpc.wincustody import (
    WAIT_TIMEOUT,
    CustodyProbe,
    Role,
    TargetReceipt,
    logger,
    windows_error,
)

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 8
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
ERROR_NOT_ENOUGH_QUOTA = 1816
EVIDENCE_PATH = Path("windows-seal.jsonl")


class SpawnAttempt(BaseModel):
    started: bool
    pid: int | None = None
    winerror: int | None = None


def query_limits(job: winspawn.Job) -> winspawn.ExtendedLimitInformation:
    limits = winspawn.ExtendedLimitInformation()
    if not winspawn._kernel32().QueryInformationJobObject(
        job._open_handle(),
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
        None,
    ):
        raise windows_error()
    return limits


def limit_values(limits: winspawn.ExtendedLimitInformation) -> dict[str, int]:
    values = {
        name: int(getattr(limits.BasicLimitInformation, name))
        for name, _native_type in winspawn.BasicLimitInformation._fields_
    }
    values["ProcessMemoryLimit"] = int(limits.ProcessMemoryLimit)
    values["JobMemoryLimit"] = int(limits.JobMemoryLimit)
    return values


async def observe_admission_seal(probe: CustodyProbe) -> None:
    async with (
        process_gate_session() as gate,
        unrelated_gate_child(gate) as unrelated,
    ):
        probe.watch(Role.UNRELATED, unrelated.process, unrelated.receipt)
        client = descendant_client(gate)
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
                control = SpawnAttempt.model_validate(
                    await client.request("probe-spawn-child", count=gate.port)
                )
                probe.emit("control_spawn", result=control.model_dump(mode="json"))
                assert control.started and control.pid is not None
                control_child = await gate.connection()
                descendants.append(control_child)
                probe.watch(
                    Role.DESCENDANT, control_child.process, control_child.receipt
                )
                for process in psutil.Process(root.pid).children(recursive=True):
                    probe.watch(Role.LAUNCHER, process)
                probe.snapshot("populated_before_admission_seal")
                job = probe.job
                assert job is not None
                limits = query_limits(job)
                before = limit_values(limits)
                limits.BasicLimitInformation.LimitFlags |= (
                    JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                )
                limits.BasicLimitInformation.ActiveProcessLimit = 0
                success = bool(
                    winspawn._kernel32().SetInformationJobObject(
                        job._open_handle(),
                        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                        ctypes.byref(limits),
                        ctypes.sizeof(limits),
                    )
                )
                error = None if success else windows_error()
                probe.emit(
                    "set_zero_process_limit",
                    succeeded=success,
                    error=None if error is None else str(error),
                    before_limits=before,
                )
                if error is not None:
                    raise error
                after = limit_values(query_limits(job))
                states = probe.snapshot("populated_after_admission_seal")
                alive = await descendant_liveness(descendants)
                probe.emit(
                    "seal_limits_read_back", limits=after, descendants_alive=alive
                )
                expected = {
                    **before,
                    "LimitFlags": before["LimitFlags"]
                    | JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
                    "ActiveProcessLimit": 0,
                }
                assert after == expected
                assert alive == [True, True, True]
                assert all(
                    state["kernel_wait_after_psutil"] == WAIT_TIMEOUT
                    for state in states
                )
                attempted = SpawnAttempt.model_validate(
                    await client.request("probe-spawn-child", count=gate.port)
                )
                probe.emit(
                    "late_spawn_after_seal", result=attempted.model_dump(mode="json")
                )
                states = probe.snapshot("after_rejected_late_spawn")
                assert not attempted.started
                assert attempted.winerror == ERROR_NOT_ENOUGH_QUOTA
                assert await descendant_liveness(descendants) == [True, True, True]
                assert (await client.request("get_state"))["isSettled"] is True
                unrelated_state = next(
                    state for state in states if state["role"] == Role.UNRELATED.value
                )
                assert unrelated_state["kernel_wait_after_psutil"] == WAIT_TIMEOUT
                assert unrelated_state["psutil_alive"] is True
                probe.emit(
                    "admission_seal_contract_observed",
                    unrelated_kernel_wait=unrelated_state["kernel_wait_after_psutil"],
                    unrelated_alive=unrelated_state["psutil_alive"],
                )
        except BaseException as error:
            probe.emit("admission_seal_scenario_failed", error=repr(error))
            raise
        finally:
            try:
                await client.close()
                states = probe.snapshot("after_sealed_job_sdk_close")
                assert [
                    state["kernel_wait_after_psutil"]
                    for state in states
                    if state["role"] == Role.UNRELATED.value
                ] == [WAIT_TIMEOUT]
            finally:
                probe.cleanup_owned()


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
                windows=platform.platform(),
                psutil_version=psutil.__version__,
                diagnostic_only=True,
                purpose="verify zero-process admission limit on a populated Job",
            )
            asyncio.run(observe_admission_seal(probe))
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    main()
