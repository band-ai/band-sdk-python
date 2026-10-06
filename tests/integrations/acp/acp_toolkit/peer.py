"""A real stdio ACP peer with scripted process exits and stderr."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from enum import StrEnum
from typing import Any

from acp import run_agent
from acp.schema import InitializeResponse

from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class ExitStage(StrEnum):
    INITIALIZE = "initialize"
    PROMPT = "prompt"
    STDOUT_EOF = "stdout-eof"
    EOF = "eof"


class StdioPeer(FakeACPAgent):
    def __init__(self, stage: ExitStage, code: int, lines: list[str]) -> None:
        super().__init__()
        self.stage = stage
        self.code = code
        self.lines = lines
        self.on_prompt(self.exit_on_prompt)

    def exit_process(self) -> None:
        sys.stderr.write("\n".join(self.lines) + "\n")
        sys.stderr.flush()
        # Exit inside a request without letting ACP turn the failure into a reply.
        os._exit(self.code)

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        if self.stage is ExitStage.INITIALIZE:
            self.exit_process()
        return await super().initialize(protocol_version, client_capabilities, **kwargs)

    async def exit_on_prompt(self, agent: FakeACPAgent, session_id: str) -> None:
        del agent, session_id
        if self.stage is ExitStage.PROMPT:
            self.exit_process()
        if self.stage is ExitStage.STDOUT_EOF:
            os.close(sys.stdout.fileno())
            # Keep stderr alive until runtime cleanup closes the agent's stdin.
            await asyncio.Future[None]()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=ExitStage, choices=list(ExitStage))
    parser.add_argument("code", type=int)
    parser.add_argument("lines", nargs="*")
    args = parser.parse_args()
    peer = StdioPeer(args.stage, args.code, args.lines)
    await run_agent(peer)
    peer.exit_process()


if __name__ == "__main__":
    asyncio.run(main())
