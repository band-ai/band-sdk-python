"""Independent permission records preserve native events and redact file bytes."""

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
from band.integrations.ompwire import OmpContent, OmpEvent, OmpToolResult, OmpUIRequest
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.test_omp_adapter import PendingRuntime
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.test_omprpc import process_is_alive


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
            assert self._process_identity is not None
            checkpoint = await self.request("checkpoint")
            peer_pid = checkpoint["pid"]
            assert isinstance(peer_pid, int)
            peer_identity = await asyncio.to_thread(psutil.Process, peer_pid)
            await asyncio.to_thread(peer_identity.create_time)
            assert (
                peer_identity == self._process_identity
                or self._process_identity
                in (await asyncio.to_thread(peer_identity.parents))
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
                str(
                    Path(__file__).parent.parent
                    / "integrations"
                    / "omppermissionpeer.py"
                ),
                order,
            ),
            env={
                **(env or {}),
                "PYTHONPATH": str(Path(__file__).parent.parent.parent),
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
        assert client._process_identity is not None
        assert (
            peer_identity == client._process_identity
            or peer_identity in client._descendants
        )
        # Windows venv launchers can expose the interpreter as a descendant.
        # Cleanup guarantees settlement, not a zero status after forced termination.
        for identity in {
            client._process_identity,
            peer_identity,
            *client._descendants,
        }:
            assert not await asyncio.to_thread(process_is_alive, identity)
    assert adapter._rooms == {}
    return tools, observed


@pytest.mark.parametrize("order", ["native-first", "permission-first"])
async def test_permission_and_native_records_are_independent_in_both_orders(
    monkeypatch, tmp_path: Path, order: str
) -> None:
    tools, observed = await owned_rpc_turn(
        monkeypatch, tmp_path, "Allow tool: read", order
    )
    assert observed == (
        ["tool_execution_start", "select", "tool_execution_end"]
        if order == "native-first"
        else ["select", "tool_execution_start", "tool_execution_end"]
    )
    calls = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_call")
    ]
    results = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_result")
    ]
    permission_calls = [call for call in calls if call["name"] == "omp_permission"]
    permission_results = [
        result for result in results if result["name"] == "omp_permission"
    ]
    assert len(permission_calls) == len(permission_results) == 1
    permission_id = permission_calls[0]["tool_call_id"]
    assert permission_id != "ui-wire-id"
    assert permission_results[0]["tool_call_id"] == permission_id
    assert permission_results[0]["is_error"]
    assert [call for call in calls if call["name"] != "omp_permission"] == [
        {"name": "read", "args": {"path": "denied.txt"}, "tool_call_id": "ui-wire-id"}
    ]
    assert [result for result in results if result["name"] != "omp_permission"] == [
        {
            "name": "read",
            "output": "Native tool failed",
            "tool_call_id": "ui-wire-id",
            "is_error": True,
        }
    ]
    assert len(calls) == len(results) == 2
    assert not tools.turn.complete
    assert tools.chat == []


async def test_repeated_owned_permission_requests_get_distinct_event_ids(
    monkeypatch, tmp_path: Path
) -> None:
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

    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", RepeatedRuntime)
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", _ready)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_permission=_cancel
    )
    tools = FakeAgentTools()
    try:
        await adapter.on_message(
            make_platform_message("Repeat the question"),
            tools,
            OmpSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room",
        )
    finally:
        await adapter.cleanup_all()
    calls = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_call")
    ]
    permission_ids = [
        call["tool_call_id"] for call in calls if call["name"] == "omp_permission"
    ]
    assert len(permission_ids) == len(set(permission_ids)) == 2
    assert "ui-wire-id" not in permission_ids
    assert [call["tool_call_id"] for call in calls if call["name"] == "read"] == [
        "ui-wire-id"
    ]
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.parametrize(
    ("spelling", "expected_name"),
    [
        ("band_send_room_file", "band_send_room_file"),
        ("mcp__band_send_room_file", "band_send_room_file"),
        ("device-write", "band_send_room_file"),
        ("send_room_file", "send_room_file"),
    ],
)
async def test_owned_permission_projection_redacts_structured_file_content(
    monkeypatch, tmp_path: Path, spelling: str, expected_name: str
) -> None:
    content = "private-file-bytes-4e612b"
    arguments = {"filename": "private.txt", "content": content, "mentions": ["user"]}
    if spelling == "device-write":
        tool_name = "write"
        wire_arguments = {
            "path": "xd://mcp__band_send_room_file",
            "content": json.dumps(arguments),
        }
    else:
        tool_name = spelling
        wire_arguments = arguments
    title = f"Allow tool: {tool_name}\nArguments: " + json.dumps(wire_arguments)
    tools, _ = await owned_rpc_turn(monkeypatch, tmp_path, title, "permission-first")
    permission = next(
        json.loads(event["content"])
        for event in events_of_type(tools, "tool_call")
        if json.loads(event["content"])["name"] == "omp_permission"
    )
    assert content not in json.dumps(permission)
    projected = permission["args"]
    assert projected["message"] == f"Allow tool: {tool_name}"
    assert projected["tool_name"] == expected_name
    assert projected["arguments"]["filename"] == arguments["filename"]
    assert projected["arguments"]["mentions"] == arguments["mentions"]
    assert projected["arguments"]["content"] != content
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.parametrize("shape", ["invalid-json", "array", "wrong-header", "inline"])
async def test_owned_permission_projection_drops_unparseable_arguments(
    monkeypatch, tmp_path: Path, shape: str
) -> None:
    secret = "unparseable-file-bytes-72847e"
    match shape:
        case "invalid-json":
            title = 'Allow tool: band_send_room_file\nArguments: {"content":"' + secret
        case "array":
            title = "Allow tool: band_send_room_file\nArguments: " + json.dumps(
                [secret]
            )
        case "wrong-header":
            title = "A question\nArguments: " + json.dumps({"content": secret})
        case _:
            title = "A question Arguments: " + secret
    tools, _ = await owned_rpc_turn(monkeypatch, tmp_path, title, "permission-first")
    permission = next(
        json.loads(event["content"])
        for event in events_of_type(tools, "tool_call")
        if json.loads(event["content"])["name"] == "omp_permission"
    )
    assert secret not in json.dumps(permission)
    assert set(permission["args"]) == {"message"}
    assert permission["args"]["message"] != title
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.parametrize(
    ("native_name", "device"),
    [
        ("mcp__band_create_task", None),
        ("mcp__band_send_room_file", None),
        ("write", "object"),
        ("write", "oversized"),
    ],
)
async def test_default_capabilities_preserve_native_name_and_turn_effect(
    monkeypatch, tmp_path: Path, native_name: str, device: str | None
) -> None:
    secret = "withheld-file-bytes"
    payload = {"filename": "private.txt", "content": secret, "mentions": ["user"]}
    match native_name:
        case "write":
            encoded = (
                '{"content":' + json.dumps(secret) + ',"count":' + "9" * 5000 + "}"
                if device == "oversized"
                else json.dumps(payload)
            )
            arguments = {
                "path": "xd://mcp__band_send_room_file",
                "content": encoded,
                "append": False,
            }
        case "mcp__band_send_room_file":
            arguments = payload
        case _:
            arguments = {"subject": "New work"}
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        tools, _ = await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            "A question",
            "permission-first",
            native_name=native_name,
            native_arguments=arguments,
            native_error=False,
            inject_band_tools=False,
        )
    finally:
        sys.set_int_max_str_digits(limit)
    calls = [
        json.loads(event["content"])
        for event in events_of_type(tools, "tool_call")
        if json.loads(event["content"])["name"] != "omp_permission"
    ]
    results = [
        json.loads(event["content"])
        for event in events_of_type(tools, "tool_result")
        if json.loads(event["content"])["name"] != "omp_permission"
    ]
    assert [call["name"] for call in calls] == [native_name]
    assert [result["name"] for result in results] == [native_name]
    assert calls[0]["tool_call_id"] == results[0]["tool_call_id"] == "ui-wire-id"
    projected = calls[0]["args"]
    assert set(projected) == set(arguments)
    if device:
        assert projected["path"] == arguments["path"]
        assert projected["append"] is False
        assert isinstance(projected["content"], str)
        if device == "object":
            assert json.loads(projected["content"])["filename"] == "private.txt"
            assert json.loads(projected["content"])["mentions"] == ["user"]
    elif native_name == "mcp__band_send_room_file":
        assert projected["filename"] == "private.txt"
        assert projected["mentions"] == ["user"]
    else:
        assert projected == arguments
    assert secret not in json.dumps(calls)
    assert not tools.turn.complete
    assert tools.chat == []


async def test_oversized_permission_integer_uses_placeholder(
    monkeypatch, tmp_path: Path
) -> None:
    secret = "oversized-decoder-file-bytes"
    title = (
        'Allow tool: band_send_room_file\nArguments: {"content":'
        + json.dumps(secret)
        + ',"count":'
        + "9" * 5000
        + "}"
    )
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
    finally:
        sys.set_int_max_str_digits(limit)
    permission = next(
        json.loads(event["content"])
        for event in events_of_type(tools, "tool_call")
        if json.loads(event["content"])["name"] == "omp_permission"
    )
    assert set(permission["args"]) == {"message"}
    assert secret not in json.dumps(permission)
    assert "9" * 5000 not in json.dumps(permission)
    assert len(events_of_type(tools, "tool_result")) == 2
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.parametrize("path", ["enabled-native", "withheld-native", "permission"])
@pytest.mark.parametrize(
    "shape", ["surrogate", "integer-limit", "deep", "malformed", "non-object"]
)
async def test_adversarial_projection_preserves_record_identity(
    monkeypatch, tmp_path: Path, path: str, shape: str
) -> None:
    secret = "adversarial-private-content"
    match shape:
        case "surrogate":
            encoded = '{"filename":"private.txt","content":"\\ud800"}'
        case "integer-limit":
            encoded = (
                '{"content":' + json.dumps(secret) + ',"count":' + "9" * 5000 + "}"
            )
        case "deep":
            encoded = "[" * 20000 + json.dumps(secret) + "]" * 20000
        case "malformed":
            encoded = '{"content":' + json.dumps(secret)
        case _:
            encoded = json.dumps([secret])
    device = {
        "path": (
            "xd://mcp__band_send_message"
            if path == "enabled-native"
            else "xd://mcp__band_send_room_file"
        ),
        "content": encoded,
        "append": False,
        "mode": "owned",
    }
    title = (
        "Allow tool: write\nArguments: " + json.dumps(device)
        if path == "permission"
        else "A question"
    )
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        tools, _ = await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            title,
            "native-first",
            native_name="read" if path == "permission" else "write",
            native_arguments=None if path == "permission" else device,
            inject_band_tools=False,
        )
    finally:
        sys.set_int_max_str_digits(limit)
    calls = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_call")
    ]
    results = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_result")
    ]
    permission = next(call for call in calls if call["name"] == "omp_permission")
    native = next(call for call in calls if call["name"] != "omp_permission")
    expected_name = (
        "read"
        if path == "permission"
        else (
            "band_send_message"
            if path == "enabled-native" and shape != "deep"
            else "write"
        )
    )
    assert native["name"] == expected_name
    assert native["tool_call_id"] == "ui-wire-id"
    assert permission["tool_call_id"] != "ui-wire-id"
    assert {(result["name"], result["tool_call_id"]) for result in results} == {
        (expected_name, "ui-wire-id"),
        ("omp_permission", permission["tool_call_id"]),
    }
    if path == "permission":
        assert permission["args"]["tool_name"] == (
            "write" if shape == "deep" else "band_send_room_file"
        )
        projected = permission["args"]["arguments"]
    else:
        projected = native["args"]
    if path == "enabled-native" and shape == "surrogate":
        assert projected == {"filename": "private.txt", "content": "\ud800"}
    elif path == "permission" and shape == "surrogate":
        assert projected == {
            "filename": "private.txt",
            "content": "<3 byte file content>",
        }
    else:
        assert set(projected) == set(device)
        assert projected["path"] == device["path"]
        assert projected["append"] is False
        assert projected["mode"] == "owned"
        if path != "enabled-native" or shape == "deep":
            assert secret not in projected["content"]
        else:
            assert projected["content"] == encoded
        if path == "withheld-native" and shape == "surrogate":
            assert json.loads(projected["content"]) == {
                "filename": "private.txt",
                "content": "<3 byte file content>",
            }
    assert all(result["is_error"] for result in results)
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.parametrize("path", ["native", "permission"])
@pytest.mark.parametrize("order", ["native-first", "permission-first"])
async def test_final_encode_failure_preserves_record_and_native_effect(
    monkeypatch, tmp_path: Path, path: str, order: str
) -> None:
    arguments = {
        "path": "visible.txt",
        "file_path": False,
        "target": None,
        "nested": {"secret": "private-envelope-bytes"},
    }
    encoded = json.dumps(arguments)
    target_name = "band_no_reply" if path == "native" else "omp_permission"
    dumps = json.dumps

    def fail_private_envelope(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("name") == target_name:
            projected = value.get("args", {})
            if path == "permission":
                projected = projected.get("arguments", {})
            if isinstance(projected, dict) and "nested" in projected:
                raise RecursionError("final envelope encoding failed")
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", fail_private_envelope)
    tools, _ = await owned_rpc_turn(
        monkeypatch,
        tmp_path,
        "Allow tool: read\nArguments: " + encoded
        if path == "permission"
        else "A question",
        order,
        native_name="write" if path == "native" else "read",
        native_arguments={
            "path": "xd://mcp__band_no_reply",
            "content": encoded,
        }
        if path == "native"
        else None,
        native_error=False,
        inject_band_tools=False,
    )
    calls = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_call")
    ]
    results = [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_result")
    ]
    target = next(call for call in calls if call["name"] == target_name)
    assert target["args"] == {
        "path": "visible.txt",
        "file_path": False,
        "target": None,
        "arguments": "[private payload unavailable]",
    }
    assert "private-envelope-bytes" not in dumps(calls)
    assert {(result["name"], result["tool_call_id"]) for result in results} == {
        (call["name"], call["tool_call_id"]) for call in calls
    }
    native = next(call for call in calls if call["name"] != "omp_permission")
    assert native["tool_call_id"] == "ui-wire-id"
    assert tools.turn.complete is (path == "native")
    assert tools.chat == []


@pytest.mark.parametrize("path", ["native", "permission"])
async def test_decoded_depth_fails_final_encode_without_losing_record(
    monkeypatch, tmp_path: Path, path: str
) -> None:
    target_name = "band_no_reply" if path == "native" else "omp_permission"
    encoded = ""
    decoder_limits = []
    calibrating = True
    decoded = 0
    encode_failed = False
    loads, dumps = json.loads, json.dumps

    def payload(depth: int) -> str:
        return (
            '{"path":"visible.txt","nested":'
            + "[" * depth
            + '"private-depth-bytes"'
            + "]" * depth
            + "}"
        )

    def observe_loads(value, *args, **kwargs):
        nonlocal decoded
        if isinstance(value, str) and value.strip() == encoded:
            if calibrating:
                # Probe on the real decoder stack, not a version-specific depth.
                lower, upper = 0, 2 * sys.getrecursionlimit()
                while True:
                    try:
                        loads(payload(upper), *args, **kwargs)
                    except RecursionError:
                        break
                    lower, upper = upper, upper * 2
                while lower + 1 < upper:
                    middle = (lower + upper) // 2
                    try:
                        loads(payload(middle), *args, **kwargs)
                    except RecursionError:
                        upper = middle
                    else:
                        lower = middle
                decoder_limits.append(lower)
            result = loads(value, *args, **kwargs)
            decoded += 1
            return result
        return loads(value, *args, **kwargs)

    def observe_dumps(value, *args, **kwargs):
        nonlocal encode_failed
        try:
            return dumps(value, *args, **kwargs)
        except RecursionError:
            if isinstance(value, dict) and value.get("name") == target_name:
                encode_failed = True
            raise

    monkeypatch.setattr(json, "loads", observe_loads)
    monkeypatch.setattr(json, "dumps", observe_dumps)

    async def run_depth(depth: int):
        nonlocal encoded
        encoded = payload(depth)
        tools, _ = await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            "Allow tool: read\nArguments: " + encoded
            if path == "permission"
            else "A question",
            "native-first",
            native_name="write" if path == "native" else "read",
            native_arguments={
                "path": "xd://mcp__band_no_reply",
                "content": encoded,
            }
            if path == "native"
            else None,
            native_error=False,
            inject_band_tools=False,
        )
        calls = [
            json.loads(event["content"]) for event in events_of_type(tools, "tool_call")
        ]
        results = [
            json.loads(event["content"])
            for event in events_of_type(tools, "tool_result")
        ]
        target = next((call for call in calls if call["name"] == target_name), None)
        assert target is not None, calls
        assert len(calls) == len(results) == 2
        assert {(result["name"], result["tool_call_id"]) for result in results} == {
            (call["name"], call["tool_call_id"]) for call in calls
        }
        native = next(call for call in calls if call["name"] != "omp_permission")
        assert native["tool_call_id"] == "ui-wire-id"
        assert tools.turn.complete is (path == "native")
        assert tools.chat == []
        return calls, target

    await run_depth(0)
    assert decoder_limits
    depth = min(decoder_limits)
    calibrating = False
    decoded = 0
    encode_failed = False
    calls, target = await run_depth(depth)
    assert decoded
    if not encode_failed:
        arguments = (
            target["args"]["arguments"] if path == "permission" else target["args"]
        )
        assert arguments["path"] == "visible.txt"
        nested = arguments["nested"]
        for _ in range(depth):
            assert isinstance(nested, list) and len(nested) == 1
            nested = nested[0]
        assert nested == "private-depth-bytes"
        pytest.skip(
            f"Python {sys.version.split()[0]} encodes the maximum decoded depth "
            f"({depth}); no natural decode-success/encode-failure window"
        )
    assert target["args"] == {
        "path": "visible.txt",
        "arguments": "[private payload unavailable]",
    }
    assert "private-depth-bytes" not in dumps(calls)
