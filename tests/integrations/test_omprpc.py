"""OMP RPC turns stay open across yields and own subprocess cleanup."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from pydantic import JsonValue

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

import psutil

from band.integrations.omprpc import (
    OmpProcessError,
    OmpPromptError,
    OmpProtocolError,
    OmpRpcClient,
    OmpRpcError,
)
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpState,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omppeer import ProcessReceipt

ClientFactory = Callable[[str], Awaitable[OmpRpcClient]]


class Observer:
    def __init__(self) -> None:
        self.events: list[OmpEvent] = []
        self.queue: asyncio.Queue[OmpEvent] = asyncio.Queue()

    async def on_event(self, event: OmpEvent) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(content=[OmpContent(type="text", text=call.toolName)])

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(cancelled=True)

    async def wait_for(self, event_type: str) -> OmpEvent:
        async with asyncio.timeout(5):
            while True:
                event = await self.queue.get()
                if event.type == event_type:
                    return event


@pytest_asyncio.fixture(loop_scope="function")
async def client_factory() -> AsyncIterator[ClientFactory]:
    clients: list[OmpRpcClient] = []

    async def build(scenario: str) -> OmpRpcClient:
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                str(Path(__file__).with_name("omppeer.py")),
                "--scenario",
                scenario,
            )
        )
        clients.append(client)
        await client.start()
        return client

    try:
        yield build
    finally:
        for client in clients:
            await client.close()


def prompt_task(
    client: OmpRpcClient, observer: Observer, message: str = "first"
) -> asyncio.Task[None]:
    return asyncio.create_task(
        client.prompt(
            message,
            on_event=observer.on_event,
            on_tool=observer.on_tool,
            on_ui=observer.on_ui,
        )
    )


async def second_prompt(client: OmpRpcClient) -> Observer:
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer, "second")
    assert any(
        event.message is not None
        and isinstance(event.message.content, list)
        and any(content.text == "complete" for content in event.message.content)
        for event in observer.events
    )
    return observer


async def checkpoint_frames(client: OmpRpcClient) -> list[dict[str, JsonValue]]:
    checkpoint = await client.request("checkpoint")
    return cast(list[dict[str, JsonValue]], checkpoint["frames"])


def process_is_alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_gate_processes(processes: list[psutil.Process]) -> None:
    for process in processes:
        with suppress(psutil.NoSuchProcess):
            process.kill()
    deadline = time.monotonic() + 5
    while alive := [process.pid for process in processes if process_is_alive(process)]:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Process gate cleanup left live processes: {alive}")
        time.sleep(0.01)


@dataclass(frozen=True)
class GatedProcess:
    receipt: ProcessReceipt
    process: psutil.Process
    reader: asyncio.StreamReader


class ProcessGate:
    def __init__(self) -> None:
        self.port = 0
        self.connections: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()
        self.writers: list[asyncio.StreamWriter] = []
        self.processes: list[psutil.Process] = []

    def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        self.connections.put_nowait((reader, writer))

    async def connection(self) -> GatedProcess:
        async with asyncio.timeout(5):
            reader, _ = await self.connections.get()
            receipt = ProcessReceipt.model_validate_json(await reader.readline())
        process = await asyncio.to_thread(psutil.Process, receipt.pid)
        self.processes.append(process)
        assert await asyncio.to_thread(process.create_time) == receipt.created
        return GatedProcess(receipt, process, reader)


@pytest_asyncio.fixture(loop_scope="function")
async def process_gate() -> AsyncIterator[ProcessGate]:
    gate = ProcessGate()
    server = await asyncio.start_server(gate.accept, "127.0.0.1", 0)
    try:
        sockets = server.sockets
        assert sockets
        gate.port = sockets[0].getsockname()[1]
        yield gate
    finally:
        server.close()
        for writer in gate.writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in gate.writers), return_exceptions=True
        )
        await server.wait_closed()
        await asyncio.to_thread(kill_gate_processes, gate.processes)


async def test_prompt_delivers_typed_narration_and_ignores_other_events(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer)
    assert [event.type for event in observer.events] == [
        "agent_start",
        "tool_execution_start",
        "tool_execution_end",
        "message_end",
        "agent_end",
    ]
    tool_start, tool_end, message_end = observer.events[1:4]
    assert (tool_start.toolCallId, tool_start.toolName, tool_start.args) == (
        "native-call",
        "read",
        {"path": "note.txt"},
    )
    assert tool_end.result is not None
    assert tool_end.result.content[0].text == "note"
    assert tool_end.result.details == {"lines": 1}
    assert tool_end.isError is False
    message = message_end.message
    assert message is not None
    assert message.role == "assistant"
    assert isinstance(message.content, list)
    assert [
        (content.type, content.text, content.thinking) for content in message.content
    ] == [
        ("thinking", None, "Considering the result"),
        ("text", "complete", None),
    ]
    assert message.usage is not None
    assert (message.usage.input, message.usage.output, message.usage.totalTokens) == (
        7,
        3,
        13,
    )
    assert message.usage.cost is not None
    assert message.usage.cost.total == 0.33
    state = OmpState.model_validate(await client.request("get_state"))
    assert (state.sessionId, state.sessionFile, state.isSettled) == (
        "session-local",
        "/local/session.jsonl",
        True,
    )
    assert state.model is not None
    assert (state.model.provider, state.model.id) == ("test-provider", "test-model")
    await second_prompt(client)


@pytest.mark.parametrize("scenario", ["yield", "stale", "false-result-settled"])
async def test_initial_yield_never_finishes_a_background_turn(
    client_factory: ClientFactory, scenario: str
) -> None:
    client = await client_factory(scenario)
    observer = Observer()
    running = prompt_task(client, observer)
    try:
        first_end = await observer.wait_for("agent_end")
        assert first_end.awaitingAsyncWork is True
        await client.request("checkpoint")
        assert not running.done()
        await client.request("release")
        async with asyncio.timeout(5):
            await running
        messages = [
            content.text
            for event in observer.events
            if event.type == "message_end"
            and event.message is not None
            and isinstance(event.message.content, list)
            for content in event.message.content
            if content.type == "text"
        ]
        assert messages == ["yielded", "background complete"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize(
    "scenario", ["resume-error", "resume-error-compacted", "resume-aborted"]
)
async def test_resumed_failures_override_a_successful_initial_prompt_result(
    client_factory: ClientFactory, scenario: str
) -> None:
    client = await client_factory(scenario)
    observer = Observer()
    running = prompt_task(client, observer)
    try:
        await observer.wait_for("agent_end")
        await client.request("release")
        expected = (
            "Resumed provider failed" if scenario != "resume-aborted" else "aborted"
        )
        async with asyncio.timeout(5):
            with pytest.raises(OmpPromptError, match=expected):
                await running
        assert sum(event.type == "agent_end" for event in observer.events) == 2
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_settlement_before_matching_result_waits_for_both(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("settlement-before-result")
    observer = Observer()
    running = prompt_task(client, observer)
    try:
        await observer.wait_for("agent_end")
        await client.request("checkpoint")
        assert not running.done()
        await client.request("release")
        async with asyncio.timeout(5):
            await running
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("scenario", ["state-settles", "settles-during-state", "local"])
async def test_native_state_snapshot_and_subscribed_settlement_do_not_race(
    client_factory: ClientFactory, scenario: str
) -> None:
    client = await client_factory(scenario)
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer)
    assert OmpState.model_validate(await client.request("get_state")).isSettled


async def test_rejected_prompt_does_not_wait_for_a_result_that_is_not_owed(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("rejected")
    async with asyncio.timeout(5):
        with pytest.raises(OmpRpcError, match="Prompt rejected") as rejected:
            await prompt_task(client, Observer())
    assert (rejected.value.command, rejected.value.code) == ("prompt", "rejected")
    await second_prompt(client)


@pytest.mark.parametrize("scenario", ["initial-error", "initial-aborted"])
async def test_failed_initial_result_is_reported(
    client_factory: ClientFactory, scenario: str
) -> None:
    client = await client_factory(scenario)
    async with asyncio.timeout(5):
        with pytest.raises(OmpPromptError):
            await prompt_task(client, Observer())


async def test_host_tools_and_ui_run_concurrently_even_before_prompt_acceptance(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("callbacks")
    observer = Observer()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[OmpHostToolCall] = []
    approvals: list[OmpUIRequest] = []

    async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
        calls.append(call)
        if call.arguments["number"] == 1:
            first_started.set()
            await release_first.wait()
        else:
            await first_started.wait()
            await client.request("checkpoint")
            release_first.set()
        return OmpToolResult(
            content=[
                OmpContent(type="text", text=call.toolCallId),
                OmpContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
            ],
            details={"number": call.arguments["number"]},
            is_error=call.arguments["number"] == 2,
        )

    async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
        approvals.append(request)
        return OmpUIResponse(value="Approve")

    async with asyncio.timeout(5):
        await client.prompt(
            "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
        )
    assert {call.id for call in calls} == {"host-1", "host-2"}
    assert [(request.method, request.options) for request in approvals] == [
        ("select", ["Approve", "Deny"])
    ]
    frames = await checkpoint_frames(client)
    result = next(frame for frame in frames if frame.get("id") == "host-2")
    assert result == {
        "type": "host_tool_result",
        "id": "host-2",
        "result": {
            "content": [
                {"type": "text", "text": "call-2"},
                {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
            ],
            "details": {"number": 2},
        },
        "isError": True,
    }
    ui_response = next(frame for frame in frames if frame.get("id") == "approval-1")
    assert ui_response == {
        "type": "extension_ui_response",
        "id": "approval-1",
        "value": "Approve",
    }
    await second_prompt(client)


@pytest.mark.parametrize("kind", ["host", "ui"])
async def test_native_cancel_targets_the_callback_and_keeps_the_next_turn_usable(
    client_factory: ClientFactory, kind: str
) -> None:
    client = await client_factory(f"{kind}-cancel")
    observer = Observer()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    forever = asyncio.Event()

    async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
        started.set()
        try:
            await forever.wait()
        finally:
            cancelled.set()
        return OmpToolResult(content=[])

    async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
        started.set()
        try:
            await forever.wait()
        finally:
            cancelled.set()
        return OmpUIResponse(value="Approve")

    running = asyncio.create_task(
        client.prompt("first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui)
    )
    try:
        async with asyncio.timeout(5):
            await started.wait()
            await client.request(f"cancel-{kind}")
            await cancelled.wait()
            await running
        frames = await checkpoint_frames(client)
        target = "host-cancelled" if kind == "host" else "ui-cancelled"
        assert not any(frame.get("id") == target for frame in frames)
        await second_prompt(client)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_ui_callback_failure_fails_closed_and_reaps_the_process(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("ui-error")
    process = client._proc
    assert process is not None

    async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
        raise ValueError("Unoffered permission choice")

    observer = Observer()
    async with asyncio.timeout(6):
        with pytest.raises(ValueError, match="Unoffered permission choice"):
            await client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=observer.on_tool,
                on_ui=on_ui,
            )
    assert process.returncode is not None


@pytest.mark.parametrize("scenario", ["yield", "no-result", "stubborn"])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_interrupted_prompt_aborts_reaps_and_can_restart(
    client_factory: ClientFactory, scenario: str, interruption: str
) -> None:
    client = await client_factory(scenario)
    process = client._proc
    assert process is not None
    observer = Observer()
    running = prompt_task(client, observer)
    try:
        await observer.wait_for("agent_end")
        if interruption == "cancel":
            running.cancel()
            async with asyncio.timeout(6):
                with pytest.raises(asyncio.CancelledError):
                    await running
        else:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await running
        assert process.returncode is not None
        async with asyncio.timeout(5):
            await client.start()
            await second_prompt(client)
        assert client._proc is not process
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_repeatedly_cancelled_close_still_waits_for_reap(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("stubborn")
    process = client._proc
    assert process is not None
    closing = asyncio.create_task(client.close())
    loop = asyncio.get_running_loop()
    cancellations = [
        loop.call_later(delay, closing.cancel) for delay in (0.01, 0.05, 0.1)
    ]
    try:
        async with asyncio.timeout(6):
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert process.returncode is not None
        await client.close()
    finally:
        for cancellation in cancellations:
            cancellation.cancel()
        await asyncio.gather(closing, return_exceptions=True)


@pytest.mark.parametrize(
    ("scenario", "error", "match"),
    [
        ("eof", OmpProcessError, "EOF"),
        ("bad-json", OmpProtocolError, "protocol"),
        ("bad-event", OmpProtocolError, "protocol"),
        ("wrong-command", OmpProtocolError, "command"),
        ("missing-state", OmpProtocolError, "protocol"),
        ("bad-acceptance", OmpProtocolError, "protocol"),
    ],
)
async def test_eof_and_protocol_failure_cannot_leave_a_prompt_waiting(
    client_factory: ClientFactory,
    scenario: str,
    error: type[RuntimeError],
    match: str,
) -> None:
    client = await client_factory(scenario)
    process = client._proc
    assert process is not None
    async with asyncio.timeout(6):
        with pytest.raises(error, match=match):
            await prompt_task(client, Observer())
    assert process.returncode is not None


async def test_command_responses_are_correlated_out_of_order_and_errors_are_recoverable(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    async with asyncio.timeout(5):
        first, second = await asyncio.gather(
            client.request("first"), client.request("second")
        )
    assert (first, second) == ({"value": "first"}, {"value": "second"})
    with pytest.raises(OmpRpcError, match="Command failed") as failed:
        await client.request("error")
    assert (failed.value.command, failed.value.code) == ("error", "bad_command")
    await second_prompt(client)


async def test_cancelled_command_drops_only_its_waiter(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    pending = asyncio.create_task(client.request("hold"))
    await client.request("checkpoint")
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert await client.request("second") == {"value": "second"}
    await second_prompt(client)


async def test_eof_fails_a_command_waiter_and_closes_the_transport(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    process = client._proc
    assert process is not None
    async with asyncio.timeout(6):
        with pytest.raises(OmpProcessError, match="EOF"):
            await client.request("crash")
        await client.close()
    assert process.returncode is not None


async def test_stderr_is_drained_without_newlines(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    async with asyncio.timeout(5):
        assert await client.request("stderr") == {"drained": True}


async def test_cancelled_startup_reaps_a_process_that_never_announces_ready() -> None:
    client = OmpRpcClient(
        (
            sys.executable,
            "-u",
            str(Path(__file__).with_name("omppeer.py")),
            "--scenario",
            "no-ready",
        )
    )
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await client.start()
        assert client._proc is not None
        assert client._proc.returncode is not None
    finally:
        await client.close()


@pytest.mark.parametrize("stdio", ["inherit", "discard"])
@pytest.mark.parametrize("interruption", ["startup", "close"])
async def test_cancelled_cleanup_reaps_descendants_but_not_unrelated_processes(
    process_gate: ProcessGate,
    stdio: str,
    interruption: str,
) -> None:
    peer = str(Path(__file__).with_name("omppeer.py"))
    port = str(process_gate.port)
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        peer,
        "--scenario",
        "gated-child",
        "--gate-port",
        port,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    client = OmpRpcClient(
        (
            sys.executable,
            "-u",
            peer,
            "--scenario",
            "no-ready-descendants" if interruption == "startup" else "descendants",
            "--gate-port",
            port,
            "--child-stdio",
            stdio,
        )
    )
    starting: asyncio.Task[None] | None = None
    closing: asyncio.Task[None] | None = None
    cancellations: list[asyncio.TimerHandle] = []
    try:
        sibling = await process_gate.connection()
        unrelated_process = await asyncio.to_thread(psutil.Process, unrelated.pid)
        # Windows virtualenv launchers may remain between the parent and Python.
        assert sibling.process == unrelated_process or unrelated_process in (
            await asyncio.to_thread(sibling.process.parents)
        )
        starting = asyncio.create_task(client.start())
        try:
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
        except TimeoutError:
            if starting.done():
                await starting
            process = client._proc
            pytest.fail(
                f"Peer did not connect its descendants: startup_done={starting.done()}, "
                f"root_pid={process.pid if process else None}, "
                f"returncode={process.returncode if process else None}"
            )
        root = client._proc
        assert root is not None
        root_process = await asyncio.to_thread(psutil.Process, root.pid)
        ancestors = [
            await asyncio.to_thread(process.process.parents) for process in descendants
        ]
        assert all(root_process in parents for parents in ancestors)
        assert (
            descendants[0].process in ancestors[1]
            or descendants[1].process in ancestors[0]
        )
        if interruption == "startup":
            closing = starting
        else:
            async with asyncio.timeout(5):
                await starting
            closing = asyncio.create_task(client.close())
            await asyncio.sleep(0)
        closing.cancel()
        loop = asyncio.get_running_loop()
        cancellations = [
            loop.call_later(delay, closing.cancel) for delay in (0.01, 0.05, 0.1)
        ]
        async with asyncio.timeout(10):
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert root.returncode is not None
        for descendant in descendants:
            async with asyncio.timeout(1):
                assert await descendant.reader.read(1) == b""
            assert not await asyncio.to_thread(process_is_alive, descendant.process)
        assert await asyncio.to_thread(process_is_alive, sibling.process)
        assert unrelated.returncode is None
        await client.close()
    finally:
        for cancellation in cancellations:
            cancellation.cancel()
        tasks = {task for task in (starting, closing) if task is not None}
        for task in tasks:
            task.cancel()
        with suppress(ProcessLookupError):
            unrelated.kill()
        async with asyncio.timeout(5):
            await unrelated.wait()
        if client._proc is not None and client._proc.returncode is None:
            with suppress(ProcessLookupError):
                client._proc.kill()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_missing_executable_is_a_process_failure() -> None:
    client = OmpRpcClient(("definitely-not-an-omp-executable-xyz",))
    try:
        with pytest.raises(OmpProcessError, match="start"):
            await client.start()
    finally:
        await client.close()


async def test_tool_callback_exception_propagates_unchanged_and_reaps_the_process(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("callbacks")
    process = client._proc
    assert process is not None
    observer = Observer()
    sentinel = ValueError("Band delivery failed")

    async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
        raise sentinel

    async with asyncio.timeout(6):
        with pytest.raises(ValueError, match="Band delivery failed") as failed:
            await client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
    assert failed.value is sentinel
    assert process.returncode is not None
    await client.start()
    await second_prompt(client)


async def test_cancelled_prompt_cancels_its_host_callback_before_reap(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("host-cancel")
    process = client._proc
    assert process is not None
    observer = Observer()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    forever = asyncio.Event()

    async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
        started.set()
        try:
            await forever.wait()
        finally:
            cancelled.set()
        return OmpToolResult(content=[])

    running = asyncio.create_task(
        client.prompt(
            "first",
            on_event=observer.on_event,
            on_tool=on_tool,
            on_ui=observer.on_ui,
        )
    )
    try:
        async with asyncio.timeout(6):
            await started.wait()
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        assert cancelled.is_set()
        assert process.returncode is not None
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_narration_failure_does_not_leave_the_turn_or_process_waiting(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("basic")
    process = client._proc
    assert process is not None
    observer = Observer()

    async def on_event(event: OmpEvent) -> None:
        raise ValueError("Narration failed")

    async with asyncio.timeout(6):
        with pytest.raises(ValueError, match="Narration failed"):
            await client.prompt(
                "first",
                on_event=on_event,
                on_tool=observer.on_tool,
                on_ui=observer.on_ui,
            )
    assert process.returncode is not None


async def test_native_v2_preserves_large_inline_image_frames(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory("large-image")
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer)
    message = next(
        event.message for event in observer.events if event.type == "message_end"
    )
    assert message is not None and isinstance(message.content, list)
    image = next(content for content in message.content if content.type == "image")
    assert image.mimeType == "image/png"
    assert image.data == "YWJj" * 300_000
    negotiations = [
        frame
        for frame in await checkpoint_frames(client)
        if frame["type"] == "negotiate_protocol"
    ]
    assert len(negotiations) == 1
    assert negotiations[0]["protocolVersion"] == 2


@pytest.mark.parametrize(
    "scenario",
    [
        "chunk-overflow",
        "chunk-order",
        "chunk-interrupted",
        "chunk-invalid-data",
        "chunk-length",
        "chunk-eof",
        "frame-overflow",
    ],
)
async def test_invalid_or_oversized_native_chunks_fail_and_reap(
    client_factory: ClientFactory, scenario: str
) -> None:
    client = await client_factory(scenario)
    process = client._proc
    assert process is not None
    async with asyncio.timeout(6):
        with pytest.raises(OmpProtocolError, match="protocol"):
            await prompt_task(client, Observer())
    assert process.returncode is not None


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("no-v2", OmpProtocolError),
        ("bad-negotiation", OmpProtocolError),
        ("negotiation-rejected", OmpRpcError),
    ],
)
async def test_start_requires_successful_native_v2_negotiation(
    client_factory: ClientFactory, scenario: str, error: type[RuntimeError]
) -> None:
    async with asyncio.timeout(6):
        with pytest.raises(error, match="protocol"):
            await client_factory(scenario)
