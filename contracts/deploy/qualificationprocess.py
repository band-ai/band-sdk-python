"""Bound qualification timeouts without bypassing inner cleanup."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import FrameType
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts.deploy.environment import BuildSettings

PROCESS_LOG = "CONTRACT_QUALIFICATION_PROCESS_LOG"


class QualificationSettings(BaseSettings):
    """Select the owner registry without inheriting service credentials."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    contract_qualification_process_log: Path | None = None


def qualification_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Keep the owner registry in sanitized service envs, without ambient secrets."""
    environment = dict(environment)
    registry = QualificationSettings().contract_qualification_process_log
    if registry is not None:
        environment[PROCESS_LOG] = str(registry)
    return environment


def install_termination_handler() -> None:
    def terminate(signum: int, _frame: FrameType | None) -> None:
        # A second signal must not interrupt the finally blocks already unwinding.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)


def _identity(pid: int) -> tuple[str, int, int] | None:
    try:
        fields = Path("/proc", str(pid), "stat").read_text().rsplit(") ", 1)[1].split()
        return fields[0], int(fields[2]), int(fields[19])
    except FileNotFoundError:
        return None


def register_process(process: subprocess.Popen[Any]) -> None:
    """Record a new-session child so the outer owner can clean it after escalation."""
    identity = _identity(process.pid)
    registry = QualificationSettings().contract_qualification_process_log
    if registry is not None:
        if identity is not None and identity[1] != process.pid:
            raise ValueError("Qualification child must own its process group")
        with registry.open("a") as log:
            log.write(
                json.dumps([process.pid, identity[2] if identity else None]) + "\n"
            )


def _signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _live_groups(records: Sequence[tuple[int, int | None]]) -> set[int]:
    owned = set()
    for pid, started in records:
        identity = _identity(pid)
        # An absent leader can still have live descendants in its group. A reused
        # leader PID with a new start time is never ours to signal.
        if identity is None or identity[2] == started:
            owned.add(pid)
    live = set()
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            identity = _identity(int(path.name))
            if identity and identity[0] != "Z" and identity[1] in owned:
                live.add(identity[1])
    return live


def _cleanup_groups(
    records: list[tuple[int, int | None]], log: Path | None = None
) -> None:
    def current_groups() -> set[int]:
        def read() -> list[tuple[int, int | None]]:
            if log is None:
                return records
            # A writer may be appending its final line; retry partial lines next sweep.
            lines = log.read_text().splitlines(keepends=True)
            return records + [
                tuple(json.loads(line)) for line in lines if line.endswith("\n")
            ]

        groups = _live_groups(read())
        # A registered parent can append a child then exit between read and /proc scan.
        return groups or _live_groups(read())

    for signum in (signal.SIGTERM, signal.SIGKILL):
        signaled = set()
        until = time.monotonic() + 2
        while True:
            groups = current_groups()
            for pid in groups - signaled:
                _signal_group(pid, signum)
                signaled.add(pid)
            if not groups or time.monotonic() >= until:
                break
            time.sleep(0.03)
    if current_groups():
        raise RuntimeError("Qualification process groups survived cleanup")


def stop_process_group(process: subprocess.Popen[Any], timeout: float = 10) -> None:
    _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_group(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_qualification(
    command: Sequence[str],
    *,
    timeout: float,
    cleanup_timeout: float = 45,
    check: bool = False,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """TERM and wait on timeout; retain failure and sweep recorded child groups."""
    environment = dict(
        BuildSettings().environment([], mix_env="test") if env is None else env
    )
    with tempfile.TemporaryDirectory(
        prefix="contract-qualification-owner-"
    ) as directory:
        log = Path(directory) / "processes.jsonl"
        log.touch(mode=0o600)
        environment[PROCESS_LOG] = str(log)
        process = subprocess.Popen(
            command, env=environment, start_new_session=True, **kwargs
        )
        identity = _identity(process.pid)
        records: list[tuple[int, int | None]] = (
            [(process.pid, identity[2])] if identity else []
        )
        try:
            try:
                code = process.wait(timeout=timeout)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=cleanup_timeout)
                except subprocess.TimeoutExpired:
                    _signal_group(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                raise
            if check and code:
                raise subprocess.CalledProcessError(code, command)
            return subprocess.CompletedProcess(command, code)
        finally:
            _cleanup_groups(records, log)
