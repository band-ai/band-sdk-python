"""A real stdio ACP peer with scripted process exits and stderr."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

from acp import run_agent
from acp.schema import InitializeResponse

from band.integrations.acp.client_adapter import ACPClientAdapterConfig
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent

INITIALIZE_PENDING_LINE = "initialization pending"
PEER_EXIT_CODE = 3
CRASH_MARKER_ARG = "--crash-marker"
RECOVERED_REPLY = "Recovered after the connection failed."


class ExitStage(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZE_WAIT = "initialize-wait"
    PROMPT = "prompt"
    PROTOCOL_ERROR = "protocol-error"
    STDOUT_EOF = "stdout-eof"
    EOF = "eof"


def stdio_peer_config(
    stage: ExitStage, lines: list[str], *, crash_marker: Path | None = None
) -> ACPClientAdapterConfig:
    # Windows' venv redirector keeps stdout open until the peer exits.
    command = [
        sys._base_executable,
        "-m",
        "tests.integrations.acp.acp_toolkit.peer",
        stage,
        str(PEER_EXIT_CODE),
        *lines,
    ]
    if crash_marker is not None:
        command.extend((CRASH_MARKER_ARG, str(crash_marker)))
    return ACPClientAdapterConfig(
        command=command,
        env={"__PYVENV_LAUNCHER__": sys.executable},
        inject_band_tools=False,
    )


class StdioPeer(FakeACPAgent):
    def __init__(
        self, stage: ExitStage, code: int, lines: list[str], crash_marker: Path | None
    ) -> None:
        super().__init__()
        self.stage = stage
        self.code = code
        self.lines = lines
        self.crash_marker = crash_marker
        self.on_prompt(self.exit_on_prompt)

    def exit_process(self) -> None:
        sys.stderr.write("\n".join(self.lines) + "\n")
        sys.stderr.flush()
        # Exit inside a request without letting ACP turn the failure into a reply.
        os._exit(self.code)

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        match self.stage:
            case ExitStage.INITIALIZE:
                self.exit_process()
            case ExitStage.INITIALIZE_WAIT:
                sys.stderr.write(INITIALIZE_PENDING_LINE + "\n")
                sys.stderr.flush()
                await asyncio.Future[None]()
        return await super().initialize(protocol_version, client_capabilities, **kwargs)

    async def exit_on_prompt(self, agent: FakeACPAgent, session_id: str) -> None:
        del agent
        if self.crash_marker is not None:
            if self.crash_marker.exists():
                await self.say(session_id, RECOVERED_REPLY)
                return
            self.crash_marker.touch()
        match self.stage:
            case ExitStage.PROMPT:
                self.exit_process()
            case ExitStage.PROTOCOL_ERROR:
                sys.stdout.write("[]\n")
                sys.stdout.flush()
            case ExitStage.STDOUT_EOF:
                os.close(sys.stdout.fileno())
            case _:
                return
        # Keep stderr alive until runtime cleanup closes the agent's stdin.
        await asyncio.Future[None]()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=ExitStage, choices=list(ExitStage))
    parser.add_argument("code", type=int)
    parser.add_argument("lines", nargs="*")
    parser.add_argument(CRASH_MARKER_ARG, type=Path)
    args = parser.parse_args()
    peer = StdioPeer(args.stage, args.code, args.lines, args.crash_marker)
    await run_agent(peer)
    peer.exit_process()


if __name__ == "__main__":
    asyncio.run(main())
