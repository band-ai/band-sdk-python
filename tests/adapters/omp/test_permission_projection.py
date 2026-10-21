"""Native permission and tool projections redact private file content."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from band.testing.fake_tools import events_of_type
from tests.adapters.omp.kit import owned_rpc_turn


class TestPermissionProjection:
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
        self, monkeypatch, tmp_path: Path, spelling: str, expected_name: str
    ) -> None:
        content = "private-file-bytes-4e612b"
        arguments = {
            "filename": "private.txt",
            "content": content,
            "mentions": ["user"],
        }
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
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
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

    @pytest.mark.parametrize(
        "shape", ["invalid-json", "array", "wrong-header", "inline"]
    )
    async def test_owned_permission_projection_drops_unparseable_arguments(
        self, monkeypatch, tmp_path: Path, shape: str
    ) -> None:
        secret = "unparseable-file-bytes-72847e"
        match shape:
            case "invalid-json":
                title = (
                    'Allow tool: band_send_room_file\nArguments: {"content":"' + secret
                )
            case "array":
                title = "Allow tool: band_send_room_file\nArguments: " + json.dumps(
                    [secret]
                )
            case "wrong-header":
                title = "A question\nArguments: " + json.dumps({"content": secret})
            case _:
                title = "A question Arguments: " + secret
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
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
        self, monkeypatch, tmp_path: Path, native_name: str, device: str | None
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
        self, monkeypatch, tmp_path: Path
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
