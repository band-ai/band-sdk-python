"""Native OMP records preserve identity across unencodable payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from band.adapters.omp import OMP_PERMISSION_TOOL, OMP_TOOL_APPROVAL_HEADER
from tests.adapters.omp.permissions import owned_rpc_turn
from tests.adapters.omp.records import tool_call_records, tool_result_records
from tests.integrations.omppermissionpeer import NATIVE_EVENT_ID, PermissionOrder

SECRET = "adversarial-private-content"
ENABLED_PATH = "xd://mcp__band_send_message"
WITHHELD_PATH = "xd://mcp__band_send_room_file"
SURROGATE = '{"filename":"private.txt","content":"\\ud800"}'
INTEGER_LIMIT = '{"content":' + json.dumps(SECRET) + ',"count":' + "9" * 5000 + "}"
DEEP = "[" * 20000 + json.dumps(SECRET) + "]" * 20000
MALFORMED = '{"content":' + json.dumps(SECRET)
NON_OBJECT = json.dumps([SECRET])


def _device(path: str, encoded: str) -> dict[str, object]:
    return {"path": path, "content": encoded, "append": False, "mode": "owned"}


async def _turn(
    monkeypatch,
    tmp_path: Path,
    title: str,
    native_name: str,
    native_arguments,
):
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        return await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            title,
            PermissionOrder.NATIVE_FIRST,
            native_name=native_name,
            native_arguments=native_arguments,
            inject_band_tools=False,
        )
    finally:
        sys.set_int_max_str_digits(limit)


class TestRecordEncoding:
    async def test_enabled_native_surrogate_keeps_decoded_object(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        tools, _ = await _turn(
            monkeypatch,
            tmp_path,
            "A question",
            "write",
            _device(ENABLED_PATH, SURROGATE),
        )
        native = next(
            call
            for call in tool_call_records(tools)
            if call["name"] != OMP_PERMISSION_TOOL
        )
        assert native["name"] == "band_send_message"
        assert native["args"] == {"filename": "private.txt", "content": "\ud800"}

    @pytest.mark.parametrize(
        ("encoded", "device_path", "expected_name", "keeps_raw_content"),
        [
            (INTEGER_LIMIT, WITHHELD_PATH, "write", False),
            (DEEP, ENABLED_PATH, "band_send_message", True),
            (DEEP, WITHHELD_PATH, "write", False),
            (MALFORMED, ENABLED_PATH, "band_send_message", True),
            (MALFORMED, WITHHELD_PATH, "write", False),
            (NON_OBJECT, ENABLED_PATH, "band_send_message", True),
            (NON_OBJECT, WITHHELD_PATH, "write", False),
        ],
        ids=[
            "int-limit-withheld",
            "deep-enabled",
            "deep-withheld",
            "malformed-enabled",
            "malformed-withheld",
            "non-object-enabled",
            "non-object-withheld",
        ],
    )
    async def test_native_device_payload_preserves_identity(
        self,
        monkeypatch,
        tmp_path: Path,
        encoded: str,
        device_path: str,
        expected_name: str,
        keeps_raw_content: bool,
    ) -> None:
        device = _device(device_path, encoded)
        tools, _ = await _turn(monkeypatch, tmp_path, "A question", "write", device)
        calls = tool_call_records(tools)
        results = tool_result_records(tools)
        permission = next(call for call in calls if call["name"] == OMP_PERMISSION_TOOL)
        native = next(call for call in calls if call["name"] != OMP_PERMISSION_TOOL)
        assert native["name"] == expected_name
        assert native["tool_call_id"] == NATIVE_EVENT_ID
        assert permission["tool_call_id"] != NATIVE_EVENT_ID
        assert {(result["name"], result["tool_call_id"]) for result in results} == {
            (expected_name, NATIVE_EVENT_ID),
            (OMP_PERMISSION_TOOL, permission["tool_call_id"]),
        }
        projected = native["args"]
        assert set(projected) == set(device)
        assert projected["path"] == device["path"]
        assert projected["append"] is False
        assert projected["mode"] == "owned"
        assert (SECRET in projected["content"]) is keeps_raw_content
        assert all(result["is_error"] for result in results)

    @pytest.mark.parametrize(
        ("encoded", "permission_tool_name", "secret_leaks"),
        [
            (DEEP, "band_send_room_file", False),
            (MALFORMED, "band_send_room_file", False),
            (NON_OBJECT, "band_send_room_file", False),
        ],
        ids=["deep", "malformed", "non-object"],
    )
    async def test_permission_device_payload_preserves_identity(
        self,
        monkeypatch,
        tmp_path: Path,
        encoded: str,
        permission_tool_name: str,
        secret_leaks: bool,
    ) -> None:
        device = _device(WITHHELD_PATH, encoded)
        title = f"{OMP_TOOL_APPROVAL_HEADER}write\nArguments: " + json.dumps(device)
        tools, _ = await _turn(monkeypatch, tmp_path, title, "read", None)
        calls = tool_call_records(tools)
        results = tool_result_records(tools)
        permission = next(call for call in calls if call["name"] == OMP_PERMISSION_TOOL)
        native = next(call for call in calls if call["name"] != OMP_PERMISSION_TOOL)
        assert native["name"] == "read"
        assert native["tool_call_id"] == NATIVE_EVENT_ID
        assert permission["tool_call_id"] != NATIVE_EVENT_ID
        assert permission["args"].get("tool_name") == permission_tool_name
        assert {(result["name"], result["tool_call_id"]) for result in results} == {
            ("read", NATIVE_EVENT_ID),
            (OMP_PERMISSION_TOOL, permission["tool_call_id"]),
        }
        projected = permission["args"]["arguments"]
        assert set(projected) == set(device)
        assert projected["path"] == device["path"]
        assert projected["append"] is False
        assert projected["mode"] == "owned"
        assert (SECRET in projected["content"]) is secret_leaks
        assert all(result["is_error"] for result in results)
