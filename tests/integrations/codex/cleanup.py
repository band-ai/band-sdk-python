"""Content-free snapshots of subprocess cleanup stalled at a test deadline."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from band.integrations.codex import CodexStdioClient


def process_snapshot(process: asyncio.subprocess.Process) -> dict[str, object]:
    native = getattr(process._transport, "_proc", None)
    try:
        native_exit: object = native.poll() if native is not None else "unavailable"
    except (AttributeError, OSError) as error:
        native_exit = type(error).__name__
    return {
        "pid": process.pid,
        "asyncio_exit": process.returncode,
        "native_exit": native_exit,
    }


def await_snapshot(task: asyncio.Task[Any]) -> dict[str, object]:
    chain: list[str] = []
    helpers: list[dict[str, object]] = []
    waiting: object = task.get_coro()
    while inspect.iscoroutine(waiting):
        frame = waiting.cr_frame
        chain.append(
            f"{waiting.cr_code.co_name}:{frame.f_lineno}"
            if frame
            else waiting.cr_code.co_name
        )
        if frame is not None:
            helper = frame.f_locals.get("taskkill")
            if isinstance(helper, asyncio.subprocess.Process):
                helpers.append(process_snapshot(helper))
        waiting = waiting.cr_await
    failure = task.exception() if task.done() and not task.cancelled() else None
    return {
        "done": task.done(),
        "cancelled": task.cancelled(),
        "failure": type(failure).__name__ if failure is not None else None,
        "await_chain": chain,
        "kill_helpers": helpers,
    }


def cleanup_snapshot(
    client: CodexStdioClient, process: asyncio.subprocess.Process
) -> dict[str, object]:
    closing = client._close_task
    return {
        "child": process_snapshot(process),
        "cleanup": await_snapshot(closing) if closing is not None else "not-started",
    }
