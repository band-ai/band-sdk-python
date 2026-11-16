"""Codex adapter: which dynamic tools a thread is offered, by name."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from band.adapters.codex.dynamictools import dynamic_tools
from band.core.types import AdapterFeatures


class SendMessageInput(BaseModel):
    """A custom tool whose name collides with Band's ``sendmessage``."""

    text: str


def band_tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": f"Band {name}", "inputSchema": {}}


def extra_tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": f"extra {name}", "inputSchema": {}}


def offered(tools: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(tool["name"], tool["description"]) for tool in tools]


class TestDynamicToolList:
    """The first tool to claim a name wins it, even one ``features`` filters out."""

    def test_band_tools_win_names_over_extras_and_custom_tools(self) -> None:
        tools = dynamic_tools(
            [band_tool("sendmessage")],
            AdapterFeatures(),
            additional=[extra_tool("sendmessage"), extra_tool("lookup")],
            custom_tools=[(SendMessageInput, lambda inp: inp.text)],
        )

        assert offered(tools) == [
            ("sendmessage", "Band sendmessage"),
            ("lookup", "extra lookup"),
        ]

    def test_a_filtered_band_tool_still_claims_its_name(self) -> None:
        tools = dynamic_tools(
            [band_tool("sendmessage"), band_tool("lookup")],
            AdapterFeatures(exclude_tools=["sendmessage"]),
            additional=[extra_tool("sendmessage")],
            custom_tools=[(SendMessageInput, lambda inp: inp.text)],
        )

        assert offered(tools) == [("lookup", "Band lookup")]
