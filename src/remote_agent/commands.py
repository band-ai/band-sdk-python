"""Bounded JSON commands, with byte-exact output capture and no shell interpretation."""

from __future__ import annotations

import base64
import os
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping

from remote_agent.capabilities import require_linux
from remote_agent.environment import minimal_environment
from remote_agent.protocol import decode
from remote_agent.registry import register_process
from remote_agent.storage import canonical
from remote_agent.types import JsonObject


class CommandError(Exception):
    pass


def failure_capture(reason: object) -> JsonObject:
    return {
        "profile": "remote-agent/process-result/1",
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "failure": str(reason)[:2000],
        "complete": False,
        "elapsed_ms": 0,
    }


def capture(
    argv: list[str],
    request: JsonObject,
    *,
    timeout: float = 10,
    max_bytes: int = 1_048_576,
    max_input_bytes: int | None = None,
    env: Mapping[str, str] | None = None,
) -> JsonObject:
    """Use a fresh allowlisted environment unless the caller explicitly supplies one."""
    require_linux()
    if env is None:
        env = minimal_environment()
    encoded = canonical(request)
    if (
        len(encoded) > (max_bytes if max_input_bytes is None else max_input_bytes)
        or timeout <= 0
    ):
        return failure_capture("Invalid command request size or timeout")
    started = time.monotonic()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    size, failure, complete, code = 0, None, True, None
    with tempfile.TemporaryFile() as source:
        source.write(encoded)
        source.seek(0)
        try:
            process = subprocess.Popen(
                argv,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
                close_fds=True,
            )
        except OSError as error:
            return failure_capture("Callback could not start: " + str(error))
        deadline = started + timeout
        output, errors = process.stdout, process.stderr
        try:
            assert output is not None and errors is not None
            register_process(process)
            with selectors.DefaultSelector() as selector:
                selector.register(output, selectors.EVENT_READ, "stdout")
                selector.register(errors, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CommandError("Callback timeout")
                    events = selector.select(remaining)
                    if not events:
                        raise CommandError("Callback timeout")
                    for key, _ in events:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        retained = chunk[: max(0, max_bytes - size)]
                        streams[key.data].extend(retained)
                        size += len(chunk)
                        if size > max_bytes:
                            raise CommandError("Callback output capacity exceeded")
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise CommandError("Callback timeout") from error
            if code:
                failure = f"Callback exited with status {code}"
        except (CommandError, OSError) as error:
            failure, complete = str(error)[:2000], False
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            code = process.returncode
            if output is not None:
                output.close()
            if errors is not None:
                errors.close()
    return {
        "profile": "remote-agent/process-result/1",
        **{
            name: base64.b64encode(value).decode("ascii")
            for name, value in streams.items()
        },
        "exit_code": code,
        "failure": failure,
        "complete": complete,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def result_from_capture(execution: JsonObject) -> JsonObject:
    if execution["failure"] is not None:
        raise CommandError(execution["failure"])
    if (
        execution["complete"] is not True
        or type(execution["exit_code"]) is not int
        or execution["exit_code"] != 0
    ):
        raise CommandError("Callback did not produce a complete successful result")
    try:
        result = decode(base64.b64decode(execution["stdout"], validate=True))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CommandError("Callback output is not JSON") from error
    if not isinstance(result, dict):
        raise CommandError("Callback output must be an object")
    return result


def call(
    argv: list[str],
    request: JsonObject,
    *,
    timeout: float = 10,
    max_bytes: int = 1_048_576,
    env: Mapping[str, str] | None = None,
) -> JsonObject:
    return result_from_capture(
        capture(argv, request, timeout=timeout, max_bytes=max_bytes, env=env)
    )
