"""Native OMP records preserve identity and effects across encoding failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from band.testing.fake_tools import events_of_type
from tests.adapters.omp.kit import owned_rpc_turn


class TestRecordEncoding:
    @pytest.mark.parametrize(
        ("shape", "path"),
        [
            ("surrogate", "enabled-native"),
            ("integer-limit", "withheld-native"),
            ("deep", "enabled-native"),
            ("deep", "withheld-native"),
            ("deep", "permission"),
            ("malformed", "enabled-native"),
            ("malformed", "withheld-native"),
            ("malformed", "permission"),
            ("non-object", "enabled-native"),
            ("non-object", "withheld-native"),
            ("non-object", "permission"),
        ],
    )
    async def test_adversarial_projection_preserves_record_identity(
        self, monkeypatch, tmp_path: Path, path: str, shape: str
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
            json.loads(event["content"])
            for event in events_of_type(tools, "tool_result")
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
        self, monkeypatch, tmp_path: Path, path: str, order: str
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
            json.loads(event["content"])
            for event in events_of_type(tools, "tool_result")
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
        self, monkeypatch, tmp_path: Path, path: str
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
                json.loads(event["content"])
                for event in events_of_type(tools, "tool_call")
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
