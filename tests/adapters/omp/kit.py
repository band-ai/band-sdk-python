"""Native OMP runtime doubles and subprocess turn helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from contextvars import copy_context
from functools import partial
from pathlib import Path

import psutil
import pytest
from pydantic import BaseModel, JsonValue

from band.adapters.omp import (
    OMP_SESSION_DIRECTORY,
    OmpAdapter,
    OmpAdapterConfig,
)
from band.converters.omp import OmpSessionState
from band.core.delivery import DeliveryFailedError
from band.core.model_catalog import ModelSelection
from band.core.types import MessageType
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompprocess import OmpMcpServer, OmpMcpTransport
from band.integrations.omprpc import OmpProcessError, OmpRpcClient, OmpRpcError
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpCommandName,
    OmpContent,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpMessage,
    OmpToolResult,
    OmpUIMethod,
    OmpUIRequest,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools, reported_failures
from band.testing.fake_tools import events_of_type
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omppermissionpeer import (
    DENIED_PATH,
    NATIVE_EVENT_ID,
    NATIVE_FAILURE_OUTPUT,
)
from tests.integrations.omprpc.kit import process_is_alive
from tests.paths import REPO_ROOT


class EchoInput(BaseModel):
    """Echo the supplied text."""

    text: str


class EchoDetail(BaseModel):
    text: str
    payload: bytes


class EchoResult(BaseModel):
    detail: EchoDetail


class StartupGate:
    """Hold native startup readiness until the test releases it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *_args: object, **_kwargs: object) -> None:
        self.entered.set()
        await self.release.wait()


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


class CancelDuringFailureTools(FakeAgentTools):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_failure(self, failure):
        self.entered.set()
        await self.release.wait()
        return await super().send_failure(failure)


class FetchBoomTools(FakeAgentTools):
    async def fetch_room_context(self, **_kwargs):
        raise RuntimeError("context unavailable")


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


class SelectionGateRuntime(RecordingRuntime):
    """Hold one native selection command at its request boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.held_command: OmpCommandName | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        if command == self.held_command:
            self.entered.set()
            await self.release.wait()
        return await super().request(command, **parameters)


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


class FirstCloseFailsRuntime(RecordingRuntime):
    """Fail the first close so custody stays with this process, then succeed."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self._close_failed = False
        self.reject_requests = False

    async def request(self, command, **parameters):
        if self.reject_requests:
            raise OmpProcessError("client is closing")
        return await super().request(command, **parameters)

    async def close(self):
        if not self._close_failed:
            self._close_failed = True
            raise OSError("process still alive")
        await super().close()


class HostToolCallRuntime(RecordingRuntime):
    def __init__(self, call: OmpHostToolCall, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.call = call
        self.tool_result: OmpToolResult | None = None

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.tool_result = await on_tool(self.call)
        self.prompt_text = _message


class UiRequestRuntime(RecordingRuntime):
    def __init__(self, ui_request: OmpUIRequest, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.ui_request = ui_request
        self.response = None

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.response = await on_ui(self.ui_request)
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


class WhitespaceMessageRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.MESSAGE_END,
                message=OmpMessage(role=OMP_ASSISTANT_ROLE, content="   \n"),
            )
        )


class NonAssistantMessageRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.MESSAGE_END,
                message=OmpMessage(role="user", content="Visible assistant text"),
            )
        )


class ClosingThoughtAfterReplyRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_tool(
            OmpHostToolCall(
                id="host-call",
                toolCallId="reply",
                toolName="band_send_message",
                arguments={"content": "Already replied"},
            )
        )
        await on_event(
            OmpEvent(
                type=OmpEventType.MESSAGE_END,
                message=OmpMessage(
                    role=OMP_ASSISTANT_ROLE, content="Closing thought after reply"
                ),
            )
        )


class SameTaskCleanupRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.adapter: OmpAdapter | None = None
        self.cleaned_from_turn = False

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        assert self.adapter is not None
        await self.adapter.on_cleanup("room-123")
        self.cleaned_from_turn = True


class BoomCloseDeliveryRuntime(BoomCloseRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise DeliveryFailedError(RuntimeError("delivery rejected"))


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


class BoomGetStateAfterReplyRuntime(HangAfterPromptRuntime):
    async def request(self, command, **_parameters):
        if command == OmpCommandName.GET_STATE and self.prompted:
            raise RuntimeError("state failed")
        return await super().request(command, **_parameters)


class LeftoverHostToolRuntime(BoomGetStateAfterReplyRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.leftover: asyncio.Task | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)

        async def leftover_call() -> None:
            self.started.set()
            await self.release.wait()
            await on_tool(
                OmpHostToolCall(
                    id="late-call",
                    toolCallId="late",
                    toolName="band_send_message",
                    arguments={"content": "Late reply"},
                )
            )

        self.leftover = asyncio.create_task(leftover_call())


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
                message=OmpMessage(
                    role=OMP_ASSISTANT_ROLE, content="Visible assistant text"
                ),
            )
        )


class ThinkingBlockRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.MESSAGE_END,
                message=OmpMessage(
                    role="assistant",
                    content=[
                        OmpContent(
                            type=OmpContentKind.THINKING, thinking="Internal reasoning"
                        ),
                        OmpContent(type=OmpContentKind.TEXT, text="Visible answer"),
                    ],
                ),
            )
        )


class UnencodableCallRuntime(PendingRuntime):
    def __init__(self, arguments: dict[str, JsonValue], *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.arguments = arguments

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_START,
                toolCallId=NATIVE_EVENT_ID,
                toolName="read",
                args=self.arguments,
            )
        )
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_END,
                toolCallId=NATIVE_EVENT_ID,
                toolName="read",
                isError=True,
                result=OmpToolResult(
                    content=[
                        OmpContent(type=OmpContentKind.TEXT, text=NATIVE_FAILURE_OUTPUT)
                    ]
                ),
            )
        )


class CustomHostToolRuntime(RecordingRuntime):
    def __init__(
        self,
        *_args: object,
        arguments: dict[str, JsonValue] | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(*_args, **_kwargs)
        self.arguments = {"text": "ping"} if arguments is None else arguments
        self.tool_result: OmpToolResult | None = None

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.tool_result = await on_tool(
            OmpHostToolCall(
                id="call",
                toolCallId="echo",
                toolName="echo",
                arguments=self.arguments,
            )
        )
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


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
                id=NATIVE_EVENT_ID,
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


class OtherTaskUiRuntime(PermissionThenReplyRuntime):
    """Dispatch UI on another task, matching OmpRpcClient._start_callback."""

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        request = OmpUIRequest(
            id="ui",
            method=OmpUIMethod.SELECT,
            title="Approve tool?",
            options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
        )
        ui_task = asyncio.create_task(on_ui(request))
        try:
            await ui_task
        except asyncio.CancelledError:
            ui_task.cancel()
            await asyncio.gather(ui_task, return_exceptions=True)
            raise
        await PendingRuntime.prompt(
            self, _message, on_event=on_event, on_tool=on_tool, on_ui=on_ui
        )


def _complete_future_from_task(pending: asyncio.Future, finished: asyncio.Task) -> None:
    if finished.cancelled():
        pending.cancel()
        return
    error = finished.exception()
    if error is not None:
        pending.set_exception(error)
        return
    pending.set_result(finished.result())


class LaterTurnReaderUiRuntime(RecordingRuntime):
    """Dispatch later-turn UI from the start()-era reader using the prompt context."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self.prompts = 0

    async def start(self):
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        while True:
            job = await self._jobs.get()
            if job is None:
                return
            callback, request, done, context = job
            task = asyncio.create_task(callback(request), context=context)
            task.add_done_callback(partial(_complete_future_from_task, done))
            await done

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.prompts += 1
        if self.prompts >= 2:
            done = asyncio.get_running_loop().create_future()
            request = OmpUIRequest(
                id="ui-later",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            )
            await self._jobs.put((on_ui, request, done, copy_context()))
            await done
        await PendingRuntime.prompt(
            self, _message, on_event=on_event, on_tool=on_tool, on_ui=on_ui
        )

    async def close(self):
        if self._reader is not None:
            await self._jobs.put(None)
            await self._reader
            self._reader = None
        await super().close()


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
                    content=[
                        OmpContent(type=OmpContentKind.TEXT, text="Tool documentation")
                    ]
                ),
                isError=False,
            )
        )


class RepeatedRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        request = OmpUIRequest(
            id=NATIVE_EVENT_ID, method=OmpUIMethod.SELECT, title="A question"
        )
        for _ in range(2):
            response = await on_ui(request)
            assert response.cancelled
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_START,
                toolCallId=NATIVE_EVENT_ID,
                toolName="read",
                args={"path": DENIED_PATH},
            )
        )
        await on_event(
            OmpEvent(
                type=OmpEventType.TOOL_END,
                toolCallId=NATIVE_EVENT_ID,
                toolName="read",
                isError=True,
                result=OmpToolResult(
                    content=[
                        OmpContent(type=OmpContentKind.TEXT, text=NATIVE_FAILURE_OUTPUT)
                    ]
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
    directory = workspace / OMP_SESSION_DIRECTORY
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


def claimed_workspace_rooms(adapter: OmpAdapter) -> tuple[str, ...]:
    return adapter._workspaces.rooms


def retained_selection(adapter: OmpAdapter, room_id: str) -> ModelSelection | None:
    return adapter._room_selections.get(room_id)


async def released_rpc_clients(clients: list) -> None:
    for client, peer_identity in clients:
        process = client._proc
        assert process is not None
        assert process.returncode is not None
        assert await process.wait() == process.returncode
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
    tools: FakeAgentTools | None = None,
    judged: bool = False,
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
                    {"path": DENIED_PATH}
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
    tools = tools if tools is not None else FakeAgentTools()
    turn_input = {
        "msg": make_platform_message("Handle the permission request"),
        "tools": tools,
        "history": OmpSessionState(),
        "participants_msg": None,
        "contacts_msg": None,
        "is_session_bootstrap": True,
        "room_id": "room",
    }
    try:
        if judged:
            await adapter.run_judged_turn(**turn_input)
        else:
            await adapter.on_message(**turn_input)
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
        self.fail_first_close = False

    def __call__(
        self, command: list[str], env=None, **_kwargs: object
    ) -> RecordingRuntime:
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        self.commands.append(list(command))
        self.envs.append(None if env is None else dict(env))
        runtime = (
            FirstCloseFailsRuntime() if self.fail_first_close else RecordingRuntime()
        )
        self.fail_first_close = False
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", record)
    return record


def remote(token: str) -> OmpMcpServer:
    return OmpMcpServer(
        type=OmpMcpTransport.HTTP,
        url="http://127.0.0.1:9/mcp",
        headers={"Authorization": f"Bearer {token}"},
    )


class BandMcpReplyRuntime(PendingRuntime):
    """Replies through the Band MCP server mounted in the overlay, not a host tool."""

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        name = "mcp__band_sdk_band_send_message"
        args = {"content": "done"}
        await on_event(
            OmpEvent(
                type="tool_execution_start", toolCallId="c1", toolName=name, args=args
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end", toolCallId="c1", toolName=name, isError=False
            )
        )


class SignallingLock(asyncio.Lock):
    """A room lock that reports when a second turn starts waiting for it."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.contended.set()
        return await super().acquire()


class SettlingResolver:
    """Blocks the first resolution; when cancelled it settles durable work before
    re-raising, and counts every cancellation it receives along the way."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.settle = asyncio.Event()
        self.settled = False
        self.cancellations = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1  # settlement interrupted by a second cancel
                raise
            self.settled = True
            raise
        return {}


def settling_adapter(tmp_path: Path, resolver, **config):
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), **config),
        resolve_mcp_servers=resolver,
        emit=(),
    )
    releases: list[bool] = []  # whether settlement had finished at each release
    real_release = adapter._workspaces.release

    def release(room_id, workspace=None):
        releases.append(resolver.settled)
        return real_release(room_id, workspace)

    adapter._workspaces.release = release  # type: ignore[method-assign]
    return adapter, releases


async def start_blocked_turn(adapter, resolver: SettlingResolver):
    first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(resolver.entered.wait(), 1)
    return first


async def checkpoint() -> None:
    resumed = asyncio.Event()
    asyncio.get_running_loop().call_soon(resumed.set)
    await resumed.wait()


async def start_cleanup(adapter: OmpAdapter, *, shutdown: bool = False):
    entered = asyncio.Event()
    original = adapter.on_cleanup

    async def cleanup(room_id):
        entered.set()
        await original(room_id)

    adapter.on_cleanup = cleanup  # type: ignore[method-assign]
    try:
        operation = (
            adapter.cleanup_all() if shutdown else adapter.on_cleanup("room-123")
        )
        task = asyncio.create_task(operation)
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        adapter.on_cleanup = original  # type: ignore[method-assign]
    return task


async def cross_deadline(clock, seconds: float) -> None:
    crossed = asyncio.Event()
    handle = asyncio.get_running_loop().call_later(seconds, crossed.set)
    try:
        clock.advance(seconds)
        await asyncio.wait_for(crossed.wait(), 1)
    finally:
        handle.cancel()


class SwallowingResolver:
    """Catches a cancellation at its first await and returns a server anyway."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        try:
            await checkpoint()
        except asyncio.CancelledError:
            pass
        return {"linear": remote("late")}


class SelfRetiringResolver(SettlingResolver):
    """On its first cancellation it asks for its own room's cleanup, then holds
    its settlement until released."""

    adapter: OmpAdapter

    async def __call__(self, workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            await self.adapter.on_cleanup("room-123")  # returns at once for self
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            raise
        return {}


class UncancellingResolver(SettlingResolver):
    """Consumes the deadline's cancellation with the public ``uncancel()``, then
    settles; ``returns`` decides whether it re-raises or returns a server."""

    def __init__(self, *, returns: bool) -> None:
        super().__init__()
        self.returns = returns

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()  # the request count is now consumed
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            if not self.returns:
                raise
        return {"linear": remote("late")}


def timeout_reports(tools) -> list[tuple[str, str | None]]:
    return [(f["provider"], f.get("code")) for f in reported_failures(tools)]


def failing_runtime(closing: asyncio.Event, finish_close: asyncio.Event):
    class FailingRuntime(PendingRuntime):
        async def prompt(self, *_args, **_kwargs):
            raise RuntimeError("native turn failed")

        async def close(self):
            closing.set()
            await finish_close.wait()
            self.closed = True

    return FailingRuntime()
