"""Native OMP runtime doubles and subprocess turn helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest
from pydantic import BaseModel

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.core.delivery import DeliveryFailedError
from band.core.model_catalog import ModelSelection
from band.core.types import MessageType
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.omprpc import OmpRpcClient, OmpRpcError
from band.integrations.ompwire import (
    OmpCommandName,
    OmpContent,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpMessage,
    OmpToolResult,
    OmpUIMethod,
    OmpUIRequest,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.kit import process_is_alive
from tests.paths import REPO_ROOT


class EchoInput(BaseModel):
    """Echo the supplied text."""

    text: str


class PendingRuntime:
    def __init__(self, *_args, **_kwargs):
        self.yielded = asyncio.Event()
        self.finish = asyncio.Event()
        self.closed = False
        self.reply = True

    async def start(self):
        return None

    async def request(self, command, **_parameters):
        if command == OmpCommandName.GET_STATE:
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


class HungStartRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.started = asyncio.Event()

    async def start(self):
        self.started.set()
        await asyncio.Event().wait()


class RecordingRuntime(PendingRuntime):
    """Record native commands, the live model, and the prompt text."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.prompt_text = ""
        self.switch_error: OmpRpcError | None = None
        self.switch_cancelled = False
        self.new_session_cancelled = False
        self.model: tuple[str, str] | None = None
        self.thinking: str | None = None
        self.levels: tuple[str, ...] = ("off", "low", "high")
        self.session_file: str | None = None

    async def request(self, command, **parameters):
        self.calls.append((command, parameters))
        if command == OmpCommandName.SWITCH_SESSION:
            if self.switch_error is not None:
                raise self.switch_error
            self.session_file = parameters["sessionPath"]
            return {"cancelled": self.switch_cancelled}
        if command == OmpCommandName.NEW_SESSION:
            self.session_file = None
            return {"cancelled": self.new_session_cancelled}
        if command == OmpCommandName.SET_MODEL:
            self.model = (parameters["provider"], parameters["modelId"])
            return {}
        if command == OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
            return {"levels": list(self.levels)}
        if command == OmpCommandName.SET_THINKING_LEVEL:
            self.thinking = parameters["level"]
            return {}
        if command == OmpCommandName.GET_STATE:
            state = {
                "sessionId": "native-session",
                "sessionFile": self.session_file,
                "isSettled": True,
            }
            if self.thinking is not None:
                state["thinkingLevel"] = self.thinking
            if self.model is not None:
                provider, model_id = self.model
                state["model"] = {"id": model_id, "provider": provider}
            return state
        return await super().request(command, **parameters)

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.prompt_text = _message
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


class AutomaticThinkingRuntime(PendingRuntime):
    async def request(self, command, **parameters):
        if command == OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
            return {"levels": ["off", "low", "high"]}
        if command == OmpCommandName.GET_STATE:
            return {
                "sessionId": "native-session",
                "isSettled": True,
                "thinkingLevel": "high",
            }
        return await super().request(command, **parameters)


class BoomCloseRuntime(PendingRuntime):
    async def close(self):
        raise OSError("process still alive")


class HangAfterPromptRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.prompted = False
        self.hung = asyncio.Event()

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_tool(
            OmpHostToolCall(
                id="host-call",
                toolCallId="reply",
                toolName="band_send_message",
                arguments={"content": "Already replied"},
            )
        )
        self.prompted = True

    async def request(self, command, **_parameters):
        if command == OmpCommandName.GET_STATE and self.prompted:
            self.hung.set()
            await asyncio.Event().wait()
        return await super().request(command, **_parameters)


class RestoreFailRuntime(RecordingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.fail_restore_model = False

    async def request(self, command, **parameters):
        if (
            command == OmpCommandName.SET_MODEL
            and self.fail_restore_model
            and parameters.get("provider") == "openai"
            and parameters.get("modelId") == "original"
        ):
            raise RuntimeError("restore set_model failed")
        return await super().request(command, **parameters)


class AutoEffectiveRuntime(RecordingRuntime):
    async def request(self, command, **parameters):
        result = await super().request(command, **parameters)
        if command == OmpCommandName.GET_STATE and self.thinking == "auto":
            result = {**result, "thinkingLevel": "high"}
        return result


class StringMessageRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.MESSAGE_END,
                message=OmpMessage(role="assistant", content="Visible assistant text"),
            )
        )


class MismatchThinkingRuntime(RecordingRuntime):
    async def request(self, command, **parameters):
        result = await super().request(command, **parameters)
        if command == OmpCommandName.GET_STATE and self.thinking not in (None, "auto"):
            result = {**result, "thinkingLevel": "off"}
        return result


class CrashPromptRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise RuntimeError("provider crashed")


class DeliveryCrashRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise DeliveryFailedError(RuntimeError("delivery rejected"))


class LiveTitleRuntime(PendingRuntime):
    def __init__(self, title: str, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.title = title

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_ui(
            OmpUIRequest(
                id="ui-wire-id",
                method=OmpUIMethod.SELECT,
                title=self.title,
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            )
        )


class PermissionThenReplyRuntime(RecordingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_ui(
            OmpUIRequest(
                id="ui",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            )
        )
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


class SelectingResolver:
    def __init__(self) -> None:
        self.adapter: OmpAdapter | None = None
        self.called = asyncio.Event()

    async def __call__(self, _request) -> None:
        assert self.adapter is not None
        self.called.set()
        await self.adapter.apply_model_selection(
            ModelSelection(model="openai/other", reasoning_effort="low"),
            room_id="room-123",
        )


class DocumentationRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_START,
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
                type=OmpEventType.TOOL_END,
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
        request = OmpUIRequest(
            id="ui-wire-id", method=OmpUIMethod.SELECT, title="A question"
        )
        for _ in range(2):
            response = await on_ui(request)
            assert response.cancelled
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_START,
                toolCallId="ui-wire-id",
                toolName="read",
                args={"path": "denied.txt"},
            )
        )
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_END,
                toolCallId="ui-wire-id",
                toolName="read",
                isError=True,
                result=OmpToolResult(
                    content=[OmpContent(type="text", text="Native tool failed")]
                ),
            )
        )


async def run_turn(
    adapter,
    tools,
    *,
    bootstrap=True,
    history: OmpSessionState | None = None,
    room_id: str = "room-123",
):
    await adapter.run_judged_turn(
        msg=make_platform_message("Finish the background work"),
        tools=tools,
        history=history or OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=room_id,
    )


def native_session_file(workspace: Path) -> Path:
    directory = workspace / ".band-omp-sessions"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "saved.jsonl"
    path.write_text("{}", encoding="utf-8")
    return path


def requests_named(
    runtime: RecordingRuntime, command: str | OmpCommandName
) -> list[dict[str, object]]:
    return [parameters for name, parameters in runtime.calls if name == command]


def room_runtime(adapter: OmpAdapter, room_id: str) -> object | None:
    room = adapter._rooms.get(room_id)
    return None if room is None else room.runtime


def occupied_room_ids(adapter: OmpAdapter) -> set[str]:
    return set(adapter._rooms)


def retained_selection(adapter: OmpAdapter, room_id: str) -> ModelSelection | None:
    return adapter._room_selections.get(room_id)


async def released_rpc_clients(clients: list) -> None:
    for client, peer_identity in clients:
        process = client._proc
        assert process is not None
        assert process.returncode is not None
        assert await process.wait() == process.returncode
        closing = client._close_task
        assert closing is not None and closing.done() and not closing.cancelled()
        assert closing.exception() is None
        assert not await asyncio.to_thread(process_is_alive, peer_identity)


def decoded_events(tools: FakeAgentTools, message_type: str) -> list[dict[str, object]]:
    return [
        json.loads(event["content"]) for event in events_of_type(tools, message_type)
    ]


def tool_call_records(tools: FakeAgentTools) -> list[dict[str, object]]:
    return decoded_events(tools, MessageType.TOOL_CALL)


def tool_result_records(tools: FakeAgentTools) -> list[dict[str, object]]:
    return decoded_events(tools, MessageType.TOOL_RESULT)


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
                if frame.type in {OmpEventType.TOOL_START, OmpEventType.TOOL_END}:
                    observed.append(frame.type)
                await on_event(frame)
                if frame.type == OmpEventType.TOOL_START and order == "native-first":
                    await self.request("permission-after-native")

            async def ui(request):
                observed.append(request.method)
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
    await released_rpc_clients(clients)
    assert occupied_room_ids(adapter) == set()
    return tools, observed


class Launches:
    """Record native runtime instances and the startup overlays they own."""

    def __init__(self) -> None:
        self.runtimes: list[RecordingRuntime] = []
        self.servers: list[dict[str, object]] = []
        self.plugins: list[Path] = []
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.spawned = asyncio.Event()

    def __call__(
        self, command: list[str], env=None, **_kwargs: object
    ) -> RecordingRuntime:
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        self.commands.append(list(command))
        self.envs.append(None if env is None else dict(env))
        runtime = RecordingRuntime()
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", record)
    return record
