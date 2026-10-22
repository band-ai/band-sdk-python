"""Scratch-only progress/task evidence for the existing Windows lifecycle model."""
from __future__ import annotations

import asyncio
import functools
import json
import os
import threading
import time
import traceback

from tests.integrations.omprpc import lifecycle
from tests.integrations.omprpc import test_lifecycle_model as model

START = time.monotonic()
CASE = 0
ACTIVE_LOOP: asyncio.AbstractEventLoop | None = None


def record(kind: str, **fields: object) -> None:
    payload = {"elapsed": round(time.monotonic() - START, 3), "kind": kind, **fields}
    line = json.dumps(payload, default=str)
    print(line, flush=True)
    with open("windows-lifecycle.jsonl", "a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def dump_tasks(reason: str) -> None:
    record("task_dump", reason=reason, case=CASE)
    loop = ACTIVE_LOOP
    if loop is not None:
        for task in asyncio.all_tasks(loop):
            print(repr(task), flush=True)
            task.print_stack()
    traceback.print_stack()


def safety_exit() -> None:
    try:
        dump_tasks("whole diagnostic safety bound")
    finally:
        os._exit(2)

def observe_method(name: str, method: object) -> object:
    @functools.wraps(method)
    async def observed(self: lifecycle.RpcLifecycle, *args: object, **kwargs: object) -> object:
        began = time.monotonic()
        record("operation_begin", case=CASE, operation=name, arguments=args)
        try:
            return await method(self, *args, **kwargs)
        finally:
            record("operation_end", case=CASE, operation=name, seconds=round(time.monotonic() - began, 3))
    return observed


ORIGINAL_PLAY = model.play


async def observed_play(steps: list[lifecycle.Step], *, detached: bool) -> None:
    global CASE, ACTIVE_LOOP
    CASE += 1
    ACTIVE_LOOP = asyncio.get_running_loop()
    began = time.monotonic()
    record("case_begin", case=CASE, detached=detached, steps=steps)
    watchdog = ACTIVE_LOOP.call_later(25, dump_tasks, "case still running after 25s")
    try:
        await ORIGINAL_PLAY(steps, detached=detached)
    finally:
        watchdog.cancel()
        record("case_end", case=CASE, seconds=round(time.monotonic() - began, 3))
        ACTIVE_LOOP = None


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This diagnostic must run on Windows")
    timer = threading.Timer(720, safety_exit)
    timer.daemon = True
    timer.start()
    for name in ("take", "open", "close", "overlap_spawn_and_restart", "finish", "cleanup"):
        setattr(lifecycle.RpcLifecycle, name, observe_method(name, getattr(lifecycle.RpcLifecycle, name)))
    model.play = observed_play
    try:
        for detached in (False, True):
            began = time.monotonic()
            record("model_begin", detached=detached)
            model.TestRpcLifecycleModel().test_generated_operations_preserve_custody_and_settlement(detached=detached)
            record("model_end", detached=detached, seconds=round(time.monotonic() - began, 3))
    finally:
        timer.cancel()


if __name__ == "__main__":
    main()
