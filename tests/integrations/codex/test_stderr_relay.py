"""Tests for the Windows stderr relay (bounded buffer, two pump threads).

The relay gives a Codex child a dedicated pipe when the helper's stderr is
an inherited pipe: a reader thread drains that pipe into a bounded buffer
(never touching the destination), and a writer thread forwards buffered
bytes to the original pipe whenever it accepts them.  Overflow drops the
oldest bytes and marks the loss in the stream.  The end-to-end guarantee —
a child outruns an undrained destination — is covered by
``tests/integrations/codex/test_winspawn_relay_pipes.py`` with real pipes;
these unit tests pin the wiring and the policy with a scripted kernel.
"""

from __future__ import annotations

import ctypes
import threading
import types

import pytest

from band.integrations.codex import winspawn


def _out_params(pointer: object) -> object:
    """Expose a ``byref`` target for fake kernel writers."""
    return pointer if hasattr(pointer, "value") else pointer._obj  # type: ignore[attr-defined]


def _set_output(pointer: object, value: int) -> None:
    target = _out_params(pointer)
    target.value = value  # type: ignore[attr-defined]


def _write_output(pointer: object, data: bytes) -> None:
    target = getattr(pointer, "contents", pointer)
    ctypes.memmove(ctypes.c_void_p(ctypes.addressof(target)), data, len(data))


def _feed(chunks: list[bytes]) -> types.GeneratorType[bytes | bool, None, None]:
    """Yield scripted reads, ending with a failed read (the pipe EOF)."""
    yield from chunks
    yield True


def _values(array: object) -> list[int]:
    return [int(array[index] or 0) for index in range(3)]


class _Kernel:
    def __init__(self) -> None:
        self.startup: object = None
        self.closed: list[int] = []
        self.sinks: dict[int, list[bytes]] = {}
        self.reads: dict[int, object] = {}
        self.pipes: dict[int, int] = {}
        self.types: dict[int, int] = {}
        self.pipe_pair: tuple[int, int] | None = None
        self.sink_handle: int | None = None
        self.sink_error: int | None = None
        self.sink_release: threading.Event | None = None
        self.next_handle = 999

    def DeleteProcThreadAttributeList(self, attributes: object) -> None:
        return None

    def SetHandleInformation(
        self, handle: ctypes.c_void_p, flags: int, value: int
    ) -> bool:
        return True

    def CloseHandle(self, handle: ctypes.c_void_p) -> bool:
        self.closed.append(int(handle.value or 0))
        return True

    def CreatePipe(self, read, write, attributes, size):
        self.next_handle += 2
        _set_output(read, self.next_handle)
        _set_output(write, self.next_handle + 1)
        self.pipe_pair = (self.next_handle, self.next_handle + 1)
        self.pipes[self.next_handle] = size
        self.types[self.pipe_pair[0]] = winspawn._FILE_TYPE_PIPE
        self.types[self.pipe_pair[1]] = winspawn._FILE_TYPE_PIPE
        self.next_handle += 1
        return True

    def DuplicateHandle(
        self, source, handle, target_process, target, access, inherit, flags
    ) -> bool:
        self.next_handle += 1
        self.sink_handle = self.next_handle
        self.types[self.sink_handle] = winspawn._FILE_TYPE_PIPE
        _set_output(target, self.sink_handle)
        return True

    def GetFileType(self, handle: ctypes.c_void_p) -> int:
        return self.types.get(int(handle.value or 0), 1)

    def ReadFile(self, handle, buffer, size, read, overlapped) -> bool:
        feed = self.reads.get(int(handle.value or 0))
        assert feed is not None
        data = next(feed)
        if data is True:
            return False
        assert data
        _write_output(buffer, data)
        _set_output(read, len(data))
        return True

    def WriteFile(
        self, handle: ctypes.c_void_p, buffer, size, written, overlapped
    ) -> bool:
        if self.sink_release is not None:
            self.sink_release.wait(10)
        if self.sink_error is not None:
            _set_output(written, 0)
            return False
        data = ctypes.string_at(buffer, size)
        self.sinks.setdefault(int(handle.value or 0), []).append(data)
        _set_output(written, size)
        return True


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr_type: int,
    feed: object,
) -> types.SimpleNamespace:
    """Route the module through a fake kernel and record both pump threads."""
    kernel = _Kernel()
    kernel.types[40] = stderr_type
    kernel.reads[1001] = feed
    created: list[types.SimpleNamespace] = []
    handles: list[list[int]] = []

    def _thread(*, target, args, name, daemon):
        thread = types.SimpleNamespace(
            target=target,
            args=args,
            name=name,
            daemon=daemon,
            start=lambda: None,
        )
        created.append(thread)
        return thread

    def _attribute_list(items):
        for handle_list in items:
            if handle_list[0] == winspawn._PROC_THREAD_ATTRIBUTE_HANDLE_LIST:
                array = ctypes.cast(
                    handle_list[1], ctypes.POINTER(ctypes.c_void_p * 3)
                )[0]
                handles.append(_values(array))
                return array
        raise AssertionError("attribute list omits handles")

    def _create_process(
        image,
        command_line,
        process_attributes,
        thread_attributes,
        inherit,
        flags,
        environment,
        current_directory,
        startup,
        information,
    ):
        kernel.startup = getattr(startup, "_obj", startup)
        info = getattr(information, "_obj", information)
        info.hProcess = ctypes.c_void_p(400)
        info.hThread = ctypes.c_void_p(401)
        return True

    monkeypatch.setattr(winspawn, "_kernel32", lambda: kernel)
    monkeypatch.setattr(winspawn, "_last_error", lambda: 5)
    monkeypatch.setattr(
        winspawn,
        "_win_os_error",
        lambda code: OSError(code, "kernel32 failure"),
    )
    monkeypatch.setattr(winspawn, "_standard_handles", lambda: (37, 38, 40))
    monkeypatch.setattr(winspawn, "_attribute_list", _attribute_list)
    monkeypatch.setattr(
        winspawn,
        "threading",
        types.SimpleNamespace(Thread=_thread, Condition=threading.Condition),
    )
    monkeypatch.setattr(winspawn, "_set_inheritable", lambda handle, inherit: None)
    monkeypatch.setattr(kernel, "CreateProcessW", _create_process, raising=False)
    return types.SimpleNamespace(
        kernel=kernel,
        handles=handles,
        startup=lambda: kernel.startup.StartupInfo,
        spawn=lambda: winspawn._spawn_suspended(
            ctypes.c_void_p(40), ("codex.exe", "serve"), 41, {}
        ),
        threads=created,
    )


def test_start_with_console_stderr_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _install(monkeypatch, stderr_type=2, feed=[])
    process, thread = env.spawn()
    assert (process, thread) == (400, 401)
    assert env.handles[0] == [37, 38, 40]
    assert env.startup().hStdError == 40
    assert env.kernel.sinks == {}
    assert env.threads == []


def test_start_attaches_relay_and_registers_both_pump_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _install(
        monkeypatch,
        stderr_type=winspawn._FILE_TYPE_PIPE,
        feed=_feed([b"first", b"second"]),
    )
    process, thread = env.spawn()
    assert (process, thread) == (400, 401)
    assert len(env.threads) == 2
    reader, writer = env.threads
    assert reader.target is winspawn._drain_stderr
    assert writer.target is winspawn._forward_stderr
    assert reader.args[1] is writer.args[1]
    assert reader.daemon and writer.daemon
    # The child's stderr points at the relay's write end, in the handle
    # list and the startup block alike, and the helper closed its own copy
    # once the child held one.
    child = env.startup().hStdError
    assert child != 40
    assert env.handles[0] == [37, 38, child]
    assert child in env.kernel.closed


def test_relay_returns_none_for_non_pipe_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = _Kernel()
    kernel.types[40] = 2  # a character device passes straight through
    monkeypatch.setattr(winspawn, "_kernel32", lambda: kernel)
    assert winspawn._stderr_relay(40) is None
    assert kernel.closed == []


def test_relay_handles_are_the_returned_triple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = _Kernel()
    kernel.types[40] = winspawn._FILE_TYPE_PIPE
    monkeypatch.setattr(winspawn, "_kernel32", lambda: kernel)
    relay = winspawn._stderr_relay(40)
    assert relay is not None
    assert kernel.pipe_pair == (relay.read, relay.child)
    assert kernel.sink_handle == relay.sink
    assert kernel.pipes[relay.read] == winspawn._STDERR_RELAY_PIPE_BYTES
    assert relay.buffer._limit == winspawn._STDERR_BUFFER_BYTES


def test_spawn_failure_closes_relay_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _install(monkeypatch, stderr_type=winspawn._FILE_TYPE_PIPE, feed=[])

    def _fail(*_args: object) -> bool:
        return False

    env.kernel.CreateProcessW = _fail
    with pytest.raises(OSError):
        env.spawn()
    assert env.threads == []
    relay_read, child = env.kernel.pipe_pair
    assert env.kernel.closed == [relay_read, child, env.kernel.sink_handle]
    assert env.kernel.sinks == {}


def test_drain_then_forward_moves_every_byte_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [b"chunk-%04d" % index for index in range(10)]
    env = _install(
        monkeypatch,
        stderr_type=winspawn._FILE_TYPE_PIPE,
        feed=_feed(chunks),
    )
    env.spawn()
    reader, writer = env.threads
    reader.target(*reader.args)
    writer.target(*writer.args)
    sink = writer.args[0]
    assert b"".join(env.kernel.sinks[sink]) == b"".join(chunks)
    # Each thread closed exactly the handle it owns; the child's copy was
    # closed by the spawn path.
    assert reader.args[0] in env.kernel.closed
    assert sink in env.kernel.closed


def test_overflow_drops_oldest_and_marks_loss() -> None:
    buffer = winspawn._StderrBuffer(limit=4096)
    for index in range(10):
        buffer.push(bytes([ord("a") + index]) * 1000)
    assert buffer._dropped == 6000  # the oldest six chunks are gone
    first = buffer.pop()
    assert first.startswith(winspawn._STDERR_TRUNCATION_MARKER % 6000)
    assert first.endswith(b"g" * 1000)
    buffer.mark_eof()
    while buffer.pop():
        pass
    assert buffer.pop() == b""


def test_forward_survives_broken_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _install(
        monkeypatch,
        stderr_type=winspawn._FILE_TYPE_PIPE,
        feed=_feed([b"payload"] * 4),
    )
    env.spawn()
    reader, writer = env.threads
    env.kernel.sink_error = winspawn._ERROR_BROKEN_PIPE
    reader.target(*reader.args)
    writer.target(*writer.args)  # must return, not spin or raise
    assert writer.args[0] in env.kernel.closed
    assert env.kernel.sinks.get(writer.args[0], []) == []


def test_parked_sink_write_never_stops_the_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    chunks = [b"x" * winspawn._STDERR_RELAY_PIPE_BYTES for _ in range(40)]
    chunks[-1] = b"final"
    env = _install(
        monkeypatch,
        stderr_type=winspawn._FILE_TYPE_PIPE,
        feed=_feed(chunks),
    )
    env.spawn()
    reader, writer = env.threads
    env.kernel.sink_release = release
    forwarder = threading.Thread(target=writer.target, args=writer.args)
    forwarder.start()
    try:
        # The reader runs here, on the test's main thread. It must finish
        # even though the forwarder is parked inside the first write and
        # 2.6 MB have nowhere to go: the buffer is capped at 1 MiB and
        # the excess is dropped.
        reader.target(*reader.args)
        assert forwarder.is_alive()
        assert env.kernel.sinks.get(writer.args[0], []) == []
        assert reader.args[1]._dropped > 0
        assert reader.args[1]._size <= winspawn._STDERR_BUFFER_BYTES
    finally:
        release.set()
        forwarder.join(10)
    assert not forwarder.is_alive()
    delivered = b"".join(env.kernel.sinks[writer.args[0]])
    assert delivered.startswith(b"x" * winspawn._STDERR_RELAY_PIPE_BYTES)
    assert delivered.endswith(b"final")
    assert winspawn._STDERR_TRUNCATION_MARKER[:20] in delivered
