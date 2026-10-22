"""Bounded JSON-lines execution for trusted, independently supplied conformance adapters."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from pathlib import Path


def run(
    argv: list[str],
    wire: bytes,
    *,
    cwd: Path,
    timeout: float = 30,
    max_bytes: int = 16777216,
) -> bytes:
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(v, str) and v for v in argv)
    ):
        raise ValueError(
            "adapter command must be a nonempty JSON array of argument strings"
        )
    if len(wire) > max_bytes:
        raise ValueError("conformance input capacity exceeded")

    process = subprocess.Popen(
        argv,
        cwd=Path(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    output, diagnostic = bytearray(), bytearray()
    deadline, offset = time.monotonic() + timeout, 0
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        streams = {
            stream.fileno(): stream
            for stream in (process.stdin, process.stdout, process.stderr)
        }
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            if wire:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("conformance adapter deadline exceeded")
                for key, _ in selector.select(remaining):
                    stream = streams[key.fd]
                    if stream is process.stdin:
                        try:
                            offset += os.write(
                                stream.fileno(), wire[offset : offset + 65536]
                            )
                        except BrokenPipeError:
                            offset = len(wire)
                        if offset == len(wire):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        data = os.read(stream.fileno(), 65536)
                        if not data:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        target = output if stream is process.stdout else diagnostic
                        if len(output) + len(diagnostic) + len(data) > max_bytes:
                            raise RuntimeError(
                                "conformance adapter output capacity exceeded"
                            )
                        target.extend(data)
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("conformance adapter deadline exceeded") from error
        if process.returncode:
            message = diagnostic[-2000:].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"adapter failed with exit {process.returncode}: {message}"
            )
        return bytes(output)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
