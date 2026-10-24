"""Native permission and tool projections redact private file content."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from band.adapters.omp import OMP_PERMISSION_TOOL, OMP_TOOL_APPROVAL_HEADER
from tests.adapters.omp.kit import (
    owned_rpc_turn,
    tool_call_records,
    tool_result_records,
)
from tests.integrations.omppermissionpeer import NATIVE_EVENT_ID

FILE_CONTENT = "private-file-bytes-4e612b"
FILE_ARGUMENTS = {
    "filename": "private.txt",
    "content": FILE_CONTENT,
    "mentions": ["user"],
}
DEVICE_ARGUMENTS = {
    "path": "xd://mcp__band_send_room_file",
    "content": json.dumps(FILE_ARGUMENTS),
}
UNPARSEABLE_SECRET = "unparseable-file-bytes-72847e"
WITHHELD_SECRET = "withheld-file-bytes"
WITHHELD_PAYLOAD = {
    "filename": "private.txt",
    "content": WITHHELD_SECRET,
    "mentions": ["user"],
}
OVERSIZED_DEVICE_CONTENT = (
    '{"content":' + json.dumps(WITHHELD_SECRET) + ',"count":' + "9" * 5000 + "}"
)


class TestPermissionProjection:
    @pytest.mark.parametrize(
        ("tool_name", "expected_name", "wire_arguments"),
        [
            ("band_send_room_file", "band_send_room_file", FILE_ARGUMENTS),
            ("mcp__band_send_room_file", "band_send_room_file", FILE_ARGUMENTS),
            ("write", "band_send_room_file", DEVICE_ARGUMENTS),
            ("send_room_file", "send_room_file", FILE_ARGUMENTS),
        ],
    )
    async def test_owned_permission_projection_redacts_structured_file_content(
        self,
        monkeypatch,
        tmp_path: Path,
        tool_name: str,
        expected_name: str,
        wire_arguments: dict[str, object],
    ) -> None:
        title = f"{OMP_TOOL_APPROVAL_HEADER}{tool_name}\nArguments: " + json.dumps(
            wire_arguments
        )
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
        permission = next(
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert FILE_CONTENT not in json.dumps(permission)
        projected = permission["args"]
        assert projected["message"] == f"{OMP_TOOL_APPROVAL_HEADER}{tool_name}"
        assert projected["tool_name"] == expected_name
        assert projected["arguments"]["filename"] == FILE_ARGUMENTS["filename"]
        assert projected["arguments"]["mentions"] == FILE_ARGUMENTS["mentions"]
        assert projected["arguments"]["content"] != FILE_CONTENT

    @pytest.mark.parametrize(
        "title",
        [
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_room_file\nArguments: "
                + '{"content":"'
                + UNPARSEABLE_SECRET
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_room_file\nArguments: "
                + json.dumps([UNPARSEABLE_SECRET])
            ),
            "A question\nArguments: " + json.dumps({"content": UNPARSEABLE_SECRET}),
            "A question Arguments: " + UNPARSEABLE_SECRET,
        ],
    )
    async def test_owned_permission_projection_drops_unparseable_arguments(
        self, monkeypatch, tmp_path: Path, title: str
    ) -> None:
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
        permission = next(
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert UNPARSEABLE_SECRET not in json.dumps(permission)
        assert set(permission["args"]) == {"message"}
        assert permission["args"]["message"] != title

    async def test_owned_permission_projection_keeps_title_without_arguments_marker(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        title = "Approve tool?"
        tools, _ = await owned_rpc_turn(
            monkeypatch, tmp_path, title, "permission-first"
        )
        permission = next(
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert permission["args"]["message"] == title

    @pytest.mark.parametrize(
        ("native_name", "arguments", "expected_filename", "settles_turn"),
        [
            ("mcp__band_create_task", {"subject": "New work"}, None, False),
            ("mcp__band_send_room_file", WITHHELD_PAYLOAD, "private.txt", False),
            (
                "write",
                {
                    "path": "xd://mcp__band_send_room_file",
                    "content": OVERSIZED_DEVICE_CONTENT,
                    "append": False,
                },
                None,
                False,
            ),
        ],
    )
    async def test_default_capabilities_preserve_native_name_and_turn_effect(
        self,
        monkeypatch,
        tmp_path: Path,
        native_name: str,
        arguments: dict[str, object],
        expected_filename: str | None,
        settles_turn: bool,
    ) -> None:
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
            call
            for call in tool_call_records(tools)
            if call["name"] != OMP_PERMISSION_TOOL
        ]
        results = [
            result
            for result in tool_result_records(tools)
            if result["name"] != OMP_PERMISSION_TOOL
        ]
        assert [call["name"] for call in calls] == [native_name]
        assert [result["name"] for result in results] == [native_name]
        assert calls[0]["tool_call_id"] == results[0]["tool_call_id"] == NATIVE_EVENT_ID
        projected = calls[0]["args"]
        assert set(projected) == set(arguments)
        assert projected.get("filename") == expected_filename
        assert WITHHELD_SECRET not in json.dumps(calls)
        assert tools.turn.complete is settles_turn

    async def test_device_write_keeps_nested_filename(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        arguments = {
            "path": "xd://mcp__band_send_room_file",
            "content": json.dumps(WITHHELD_PAYLOAD),
            "append": False,
        }
        tools, _ = await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            "A question",
            "permission-first",
            native_name="write",
            native_arguments=arguments,
            native_error=False,
            inject_band_tools=False,
        )
        calls = [
            call
            for call in tool_call_records(tools)
            if call["name"] != OMP_PERMISSION_TOOL
        ]
        projected = calls[0]["args"]
        assert json.loads(projected["content"])["filename"] == "private.txt"
        assert WITHHELD_SECRET not in json.dumps(calls)

    async def test_oversized_permission_integer_uses_placeholder(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        secret = "oversized-decoder-file-bytes"
        title = (
            f'{OMP_TOOL_APPROVAL_HEADER}band_send_room_file\nArguments: {{"content":'
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
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert set(permission["args"]) == {"message"}
        assert secret not in json.dumps(permission)
        assert "9" * 5000 not in json.dumps(permission)
        assert len(tool_result_records(tools)) == 2
