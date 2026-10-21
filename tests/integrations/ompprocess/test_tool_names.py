"""Tool names OMP mints under the SDK plugin namespace map back to Band tools."""

from __future__ import annotations

import pytest

from band.integrations.ompprocess import (
    normalize_native_mcp_device_call,
    normalize_native_mcp_tool_name,
)

OWN = frozenset({"band_send_message"})


class TestPluginNamespacedToolNames:
    @pytest.mark.parametrize(
        "path",
        [
            "xd://mcp__band_send_message",
            "xd://mcp__band_sdk_band_band_send_message",
        ],
    )
    def test_executed_device_write_maps_to_the_band_tool(self, path: str) -> None:
        arguments = {"path": path, "content": '{"chat_id":"room-1","content":"hi"}'}
        assert normalize_native_mcp_device_call(
            "write", arguments, OWN, kind="execute"
        ) == ("band_send_message", {"chat_id": "room-1", "content": "hi"})

    @pytest.mark.parametrize(
        "title", ["mcp__band_send_message", "mcp__band_sdk_band_band_send_message"]
    )
    def test_mcp_title_maps_to_the_band_tool(self, title: str) -> None:
        assert normalize_native_mcp_tool_name(title, OWN) == "band_send_message"
        assert normalize_native_mcp_device_call(title, {"chat_id": "r1"}, OWN) == (
            "band_send_message",
            {"chat_id": "r1"},
        )

    def test_a_namespaced_read_is_never_the_band_tool(self) -> None:
        arguments = {
            "path": "xd://mcp__band_sdk_band_band_send_message",
            "content": '{"content":"hi"}',
        }
        assert normalize_native_mcp_device_call(
            "read", arguments, OWN, kind="read"
        ) == ("read", arguments)

    def test_a_foreign_namespace_stays_unmapped(self) -> None:
        assert (
            normalize_native_mcp_tool_name("mcp__other_sdk_band_send_message", OWN)
            is None
        )
