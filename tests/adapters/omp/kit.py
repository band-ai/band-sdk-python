"""Native OMP runtime doubles and subprocess turn helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.kit import process_is_alive
from tests.paths import REPO_ROOT


class PendingRuntime:
    def __init__(self, *_args, **_kwargs):
        self.yielded = asyncio.Event()
        self.finish = asyncio.Event()
        self.closed = False
        self.reply = True

    async def start(self):
        return None

    async def request(self, command, **_parameters):
        if command == "get_state":
            return {
                "sessionId": "native-session",
                "sessionFile": None,
                "isSettled": True,
            }
        return {}

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.yielded.set()
        await self.finish.wait()
        if self.reply:
            await on_tool(
                OmpHostToolCall(
                    id="host-call",
                    toolCallId="reply",
                    toolName="band_send_message",
                    arguments={"content": "Background work finished"},
                )
            )

    async def close(self):
        self.closed = True


class FailedDeliveryTools(FakeAgentTools):
    async def execute_tool_call_structured(self, tool_name, arguments):
        return ToolCallOutcome(
            value={"error": "delivery rejected"},
            ok=False,
            error_message="delivery rejected",
        )


class AutomaticThinkingRuntime(PendingRuntime):
    async def request(self, command, **parameters):
        if command == "get_available_thinking_levels":
            return {"levels": ["off", "low", "high"]}
        if command == "get_state":
            return {
                "sessionId": "native-session",
                "isSettled": True,
                "thinkingLevel": "high",
            }
        return await super().request(command, **parameters)


class DocumentationRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type="tool_execution_start",
                toolCallId="docs",
                toolName="grep",
                args={
                    "path": "xd://mcp__band_sdk_band_band_send_message",
                    "pattern": ".",
                },
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end",
                toolCallId="docs",
                toolName="grep",
                result=OmpToolResult(
                    content=[OmpContent(type="text", text="Tool documentation")]
                ),
                isError=False,
            )
        )


class RepeatedRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        request = OmpUIRequest(id="ui-wire-id", method="select", title="A question")
        for _ in range(2):
            response = await on_ui(request)
            assert response.cancelled
        await on_event(
            OmpEvent(
                type="tool_execution_start",
                toolCallId="ui-wire-id",
                toolName="read",
                args={"path": "denied.txt"},
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end",
                toolCallId="ui-wire-id",
                toolName="read",
                isError=True,
                result=OmpToolResult(
                    content=[OmpContent(type="text", text="Native tool failed")]
                ),
            )
        )


async def run_turn(adapter, tools, *, bootstrap=True):
    await adapter.run_judged_turn(
        msg=make_platform_message("Finish the background work"),
        tools=tools,
        history=OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id="room-123",
    )


async def _ready(*_args, **_kwargs) -> None:
    return None


async def _cancel(_request) -> None:
    return None


async def owned_rpc_turn(
    monkeypatch,
    tmp_path: Path,
    title: str,
    order: str,
    *,
    native_name: str = "read",
    native_arguments=None,
    native_error: bool = True,
    inject_band_tools: bool = True,
):
    observed = []
    clients = []

    class OrderingClient(OmpRpcClient):
        async def start(self):
            await super().start()
            assert self._proc is not None
            root_identity = await asyncio.to_thread(psutil.Process, self._proc.pid)
            checkpoint = await self.request("checkpoint")
            peer_pid = checkpoint["pid"]
            assert isinstance(peer_pid, int)
            peer_identity = await asyncio.to_thread(psutil.Process, peer_pid)
            await asyncio.to_thread(peer_identity.create_time)
            assert peer_identity == root_identity or root_identity in (
                await asyncio.to_thread(peer_identity.parents)
            )
            clients.append((self, peer_identity))

        async def prompt(self, message, *, on_event, on_tool, on_ui):
            async def event(frame):
                if frame.type in {"tool_execution_start", "tool_execution_end"}:
                    observed.append(frame.type)
                await on_event(frame)
                if frame.type == "tool_execution_start" and order == "native-first":
                    await self.request("permission-after-native")

            async def ui(request):
                observed.append("select")
                return await on_ui(request)

            await super().prompt(message, on_event=event, on_tool=on_tool, on_ui=ui)

    def client_factory(*_args, env=None, **_kwargs):
        client = OrderingClient(
            (
                sys.executable,
                "-u",
                str(REPO_ROOT / "tests" / "integrations" / "omppermissionpeer.py"),
                order,
            ),
            env={
                **(env or {}),
                "PYTHONPATH": str(REPO_ROOT),
                "OMP_TEST_PERMISSION_TITLE": title,
                "OMP_TEST_NATIVE_NAME": native_name,
                "OMP_TEST_NATIVE_ARGUMENTS": json.dumps(
                    {"path": "denied.txt"}
                    if native_arguments is None
                    else native_arguments
                ),
                "OMP_TEST_NATIVE_ERROR": "1" if native_error else "0",
            },
        )
        return client

    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", client_factory)
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", _ready)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=inject_band_tools),
        resolve_permission=_cancel,
    )
    tools = FakeAgentTools()
    try:
        await adapter.on_message(
            make_platform_message("Handle the permission request"),
            tools,
            OmpSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room",
        )
    finally:
        await adapter.cleanup_all()
    assert clients
    for client, peer_identity in clients:
        process = client._proc
        assert process is not None
        assert process.returncode is not None
        assert await process.wait() == process.returncode
        closing = client._close_task
        assert closing is not None and closing.done() and not closing.cancelled()
        assert closing.exception() is None
        # The retained helper can differ from the native interpreter on Windows.
        assert not await asyncio.to_thread(process_is_alive, peer_identity)
    assert adapter._rooms == {}
    return tools, observed


class Launches:
    """Record native runtime instances and the startup overlays they own."""

    def __init__(self) -> None:
        self.runtimes: list[PendingRuntime] = []
        self.servers: list[dict[str, object]] = []
        self.plugins: list[Path] = []
        self.spawned = asyncio.Event()

    def __call__(self, command: list[str], **_kwargs: object) -> PendingRuntime:
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        runtime = PendingRuntime()
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", record)
    return record
