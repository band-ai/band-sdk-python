"""Tool names OMP mints under the SDK plugin namespace map back to Band tools."""

from __future__ import annotations

import pytest

from band.integrations.omp import XD_MCP_PREFIX, XD_URL_PREFIX
from band.integrations.ompprocess import (
    OMP_PLUGIN_TOOL_NAMESPACE,
    normalize_native_mcp_device_call,
    normalize_native_mcp_tool_name,
)

OWN_TOOL = "band_send_message"
OWN = frozenset({OWN_TOOL})
BARE_TITLE = f"{XD_MCP_PREFIX}{OWN_TOOL}"
NAMESPACED_TITLE = f"{XD_MCP_PREFIX}{OMP_PLUGIN_TOOL_NAMESPACE}band_{OWN_TOOL}"
BARE_PATH = f"{XD_URL_PREFIX}{BARE_TITLE}"
NAMESPACED_PATH = f"{XD_URL_PREFIX}{NAMESPACED_TITLE}"


class TestPluginNamespacedToolNames:
    @pytest.mark.parametrize(
        "path",
        [
            BARE_PATH,
            NAMESPACED_PATH,
        ],
    )
    def test_executed_device_write_maps_to_the_band_tool(self, path: str) -> None:
        arguments = {"path": path, "content": '{"chat_id":"room-1","content":"hi"}'}
        assert normalize_native_mcp_device_call(
            "write", arguments, OWN, kind="execute"
        ) == (OWN_TOOL, {"chat_id": "room-1", "content": "hi"})

    @pytest.mark.parametrize("title", [BARE_TITLE, NAMESPACED_TITLE])
    def test_mcp_title_maps_to_the_band_tool(self, title: str) -> None:
        assert normalize_native_mcp_tool_name(title, OWN) == OWN_TOOL
        assert normalize_native_mcp_device_call(title, {"chat_id": "r1"}, OWN) == (
            OWN_TOOL,
            {"chat_id": "r1"},
        )

    def test_a_namespaced_read_is_never_the_band_tool(self) -> None:
        arguments = {
            "path": NAMESPACED_PATH,
            "content": '{"content":"hi"}',
        }
        assert normalize_native_mcp_device_call(
            "read", arguments, OWN, kind="read"
        ) == ("read", arguments)

    def test_a_foreign_namespace_stays_unmapped(self) -> None:
        assert (
            normalize_native_mcp_tool_name(
                f"{XD_MCP_PREFIX}other_sdk_band_send_message", OWN
            )
            is None
        )
