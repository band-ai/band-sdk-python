"""A real stdio ACP agent that answers the common Room lifecycle suite's
commands in its workspace."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from acp import RequestError, run_agent
from acp.schema import InitializeResponse

from band.runtime.tools import BandTool
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME
from tests.adapters.roompeer import (
    HOLD_POLL_S,
    STARTUP_FAILURE,
    PeerCommand,
    begin_startup,
    file_request,
    perform,
    reply_text,
    startup_held,
    startup_refused,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class WorkspacePeer(FakeACPAgent):
    """``--hold-startup`` holds ``initialize`` while its file exists and
    ``--fail-startup`` refuses it, as the suite's other peers do."""

    def __init__(self, failure: Path | None, hold: Path | None) -> None:
        super().__init__(supports_session_load=True)
        self.failure = failure
        self.hold = hold
        self.on_prompt(self.answer)

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        begin_startup()
        while startup_held(self.hold):
            await asyncio.sleep(HOLD_POLL_S)
        if startup_refused(self.failure):
            raise RequestError(-32000, STARTUP_FAILURE)
        return await super().initialize(protocol_version, client_capabilities, **kwargs)

    async def answer(self, peer: FakeACPAgent, session_id: str) -> None:
        content = peer.prompt_texts()[-1]
        if PeerCommand.EXIT_PROCESS in content:
            os._exit(0)
        if PeerCommand.LOSE_TRANSPORT in content:
            # A frame that is not JSON-RPC faults the client's reader while
            # this process stays alive.
            sys.stdout.write("[]\n")
            sys.stdout.flush()
            await asyncio.Future[None]()
        request = file_request(content)
        result = perform(request["tool_name"], request["input"])
        await peer.call_mcp_tool(
            session_id=session_id,
            server=BAND_MCP_SERVER_NAME,
            tool_name=BandTool.SEND_MESSAGE,
            arguments={
                "content": reply_text(request["tool_name"], result),
                "mentions": ["@alice"],
            },
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-startup", type=Path)
    parser.add_argument("--hold-startup", type=Path)
    # OMP passes its room's workspace as a flag instead of the launch cwd.
    parser.add_argument("--cwd")
    # Each profile adds its own launch flags and subcommand.
    options, _ = parser.parse_known_args()
    if options.cwd is not None:
        os.chdir(options.cwd)
    await run_agent(WorkspacePeer(options.fail_startup, options.hold_startup))


if __name__ == "__main__":
    asyncio.run(main())
