"""Exercise the installed MCP CLI and SDK engine without platform credentials."""

from __future__ import annotations

import asyncio
import logging
import sys
from importlib.metadata import version

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from band.integrations.mcp.engine import dispatch_tool
from band.runtime.tools import Surface, iter_tool_definitions
from band.testing.fake_tools import FakeAgentTools

logger = logging.getLogger(__name__)


async def main() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "band_mcp.server", "--agent-key", "band_a_smoke"],
        env={"BAND_MCP_SCOPE": "agent"},
    )
    async with asyncio.timeout(30):
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            by_name = {tool.name: tool for tool in tools.tools}
            assert "band_send_message" in by_name
            assert by_name["band_send_message"].inputSchema["properties"]["content"]
            assert "band_no_reply" in by_name
    definition = next(
        tool
        for tool in iter_tool_definitions(surface=Surface.AGENT)
        if tool.name == "band_no_reply"
    )
    result = await dispatch_tool(FakeAgentTools(), definition, {})
    assert result == {"status": "no_reply"}
    logger.info(
        "Installed MCP %s with SDK %s: initialize, schemas and dispatch passed",
        version("band-mcp"),
        version("band-sdk"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
