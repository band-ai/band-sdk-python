from __future__ import annotations

import ctypes
import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

import psutil

logger = logging.getLogger(__name__)
SHELL_TIMEOUT_SECONDS = 30
CLEANUP_TIMEOUT_SECONDS = 5
MARKER = "approval-marker"
WAIT_TIMEOUT = 258


def emit(event: str, **fields: object) -> None:
    logger.info("%s", json.dumps({"event": event, "at": time.monotonic(), **fields}))


def target_bytes(target: Path) -> str | None:
    try:
        return target.read_bytes().hex()
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"{type(error).__name__}: {error}"


class ShellProbe:
    def __init__(self, label: str, command: str, target: Path) -> None:
        self.label = label
        self.command = command
        self.target = target
        self.stopped = threading.Event()
        self.children: dict[tuple[int, float], psutil.Process] = {}
        self.monitor_error: Exception | None = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.kernel.WaitForSingleObject.restype = ctypes.c_ulong
        self.kernel.IsProcessInJob.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.kernel.IsProcessInJob.restype = ctypes.c_int
        self.kernel.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.kernel.OpenProcess.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_int

    def record_process(self, process: psutil.Process) -> dict[str, object]:
        try:
            with process.oneshot():
                cpu = process.cpu_times()
                return {
                    "pid": process.pid,
                    "created": process.create_time(),
                    "parent": process.ppid(),
                    "name": process.name(),
                    "status": process.status(),
                    "cpu_user": cpu.user,
                    "cpu_system": cpu.system,
                    "threads": [thread._asdict() for thread in process.threads()],
                    "modules": sorted(
                        {Path(item.path).name for item in process.memory_maps()}
                    ),
                }
        except psutil.NoSuchProcess:
            return {"pid": process.pid, "exited": True}
        except psutil.Error as error:
            return {"pid": process.pid, "query_error": str(error)}

    def snapshot(self, process: subprocess.Popen[bytes], phase: str) -> None:
        # Popen retains its native handle, preventing root PID reuse here.
        handle = self.kernel.OpenProcess(0x00101000, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            wait = self.kernel.WaitForSingleObject(handle, 0)
            in_job = ctypes.c_int()
            if not self.kernel.IsProcessInJob(handle, None, ctypes.byref(in_job)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        records: list[dict[str, object]] = []
        if wait == WAIT_TIMEOUT:
            try:
                root = psutil.Process(process.pid)
                for child in root.children(recursive=True):
                    self.children[(child.pid, child.create_time())] = child
                records.append(self.record_process(root))
            except psutil.NoSuchProcess:
                records.append({"pid": process.pid, "exited_during_snapshot": True})
        records.extend(self.record_process(child) for child in self.children.values())
        emit(
            "process_snapshot",
            label=self.label,
            phase=phase,
            pid=process.pid,
            kernel_wait=wait,
            in_any_job=bool(in_job.value),
            target_hex=target_bytes(self.target),
            processes=records,
        )

    def monitor(self, process: subprocess.Popen[bytes]) -> None:
        try:
            while not self.stopped.is_set():
                self.snapshot(process, "running")
                if self.stopped.wait(0.25):
                    return
        except Exception as error:
            self.monitor_error = error
            logger.exception("Process monitor failed for %s", self.label)
            self.stopped.set()

    def retire(self, process: subprocess.Popen[bytes]) -> None:
        children = list(self.children.values())
        for child in children:
            try:
                # psutil verifies creation identity before terminating a reused PID.
                child.kill()
            except psutil.NoSuchProcess:
                pass
        if process.poll() is None:
            process.kill()
        output, errors = process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
        _, alive = psutil.wait_procs(children, timeout=CLEANUP_TIMEOUT_SECONDS)
        emit(
            "cleanup_complete",
            label=self.label,
            root_exit=process.returncode,
            alive_owned_children=[child.pid for child in alive],
            stdout_hex=output.hex(),
            stderr_hex=errors.hex(),
        )
        if alive:
            raise RuntimeError("Tracked diagnostic descendants did not exit")

    def run(self) -> bool:
        arguments = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            self.command,
        ]
        emit(
            "launch",
            label=self.label,
            resolved_executable=shutil.which(arguments[0]),
            arguments=arguments,
            serialized_command_line=subprocess.list2cmdline(arguments),
            cwd=str(self.target.parent),
            stdin="DEVNULL",
            stdout="PIPE",
            stderr="PIPE",
            timeout=SHELL_TIMEOUT_SECONDS,
            target_hex=target_bytes(self.target),
        )
        process = subprocess.Popen(
            arguments,
            cwd=self.target.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitor = threading.Thread(target=self.monitor, args=(process,), daemon=True)
        monitor.start()
        succeeded = False
        try:
            output, errors = process.communicate(timeout=SHELL_TIMEOUT_SECONDS)
            emit(
                "communicate_complete",
                label=self.label,
                pid=process.pid,
                returncode=process.returncode,
                stdout_hex=output.hex(),
                stderr_hex=errors.hex(),
                target_hex=target_bytes(self.target),
            )
            succeeded = process.returncode == 0
        except subprocess.TimeoutExpired as error:
            emit(
                "communicate_timeout",
                label=self.label,
                pid=process.pid,
                stdout_hex=(error.output or b"").hex(),
                stderr_hex=(error.stderr or b"").hex(),
                target_hex=target_bytes(self.target),
            )
        finally:
            self.stopped.set()
            monitor.join(timeout=CLEANUP_TIMEOUT_SECONDS)
            try:
                if monitor.is_alive():
                    raise RuntimeError("Diagnostic monitor did not stop")
                self.snapshot(process, "before_cleanup")
            finally:
                self.retire(process)
        if self.monitor_error is not None:
            raise RuntimeError("Diagnostic monitor failed") from self.monitor_error
        return succeeded


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("This component diagnostic requires Windows")
    with Path("windows-shell-evidence.jsonl").open("w", encoding="utf-8") as stream:
        configure_logging(stream)
        emit(
            "runtime",
            python=sys.version,
            platform=platform.platform(),
            psutil=psutil.__version__,
        )
        with tempfile.TemporaryDirectory(prefix="shell-component-") as directory:
            workspace = Path(directory)
            target = workspace / "approval with spaces.txt"
            target.write_bytes(b"old")
            original = ShellProbe(
                "original_payload", f'echo {MARKER} > "{target.name}"', target
            ).run()
            written = target.read_bytes()
            encoding = "utf-16" if written.startswith(b"\xff\xfe") else "utf-8-sig"
            marker_written = written.decode(encoding).splitlines() == [MARKER]
            milestone = workspace / "engine-entry.txt"
            engine = ShellProbe(
                "direct_engine_milestone",
                '[IO.File]::WriteAllText("engine-entry.txt", "engine-entered"); '
                '[Console]::Error.WriteLine("engine-entered")',
                milestone,
            ).run()
            engine_entered = (
                milestone.exists() and milestone.read_text() == "engine-entered"
            )
            emit(
                "result",
                original_completed=original,
                marker_written=marker_written,
                engine_completed=engine,
                engine_entered=engine_entered,
            )
            return 0 if original and marker_written and engine and engine_entered else 1


def configure_logging(stream: IO[str]) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(), logging.StreamHandler(stream)],
    )


if __name__ == "__main__":
    raise SystemExit(main())
