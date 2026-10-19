"""A real stdio ACP peer with scripted process exits and stderr.

It imports only ``acp`` and the standard library at startup, outside the
``acp_toolkit`` package, so tests that bound its start stay clear of CI load.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

from acp import RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PromptResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SetSessionConfigOptionResponse,
)

PEER_MODULE = "tests.integrations.acp.stdiopeer"
INITIALIZE_PENDING_LINE = "initialization pending"
PEER_EXIT_CODE = 3
CRASH_MARKER_ARG = "--crash-marker"
HOLDER_RELEASE_ARG = "--holder-release"
# A backstop for a test run that dies before releasing its holder.
HOLDER_LIFETIME_S = 120
HOLDER_POLL_S = 0.05
HOLDER_SCRIPT = f"""\
import os, sys, time
deadline = time.monotonic() + {HOLDER_LIFETIME_S}
while not os.path.exists(sys.argv[1]) and time.monotonic() < deadline:
    time.sleep({HOLDER_POLL_S})
"""
RECOVERED_REPLY = "Recovered after the connection failed."


class ExitStage(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZE_WAIT = "initialize-wait"
    PROMPT = "prompt"
    PROTOCOL_ERROR = "protocol-error"
    NEW_SESSION_PROTOCOL_ERROR = "new-session-protocol-error"
    LOAD_SESSION_PROTOCOL_ERROR = "load-session-protocol-error"
    CONFIG_PROTOCOL_ERROR = "config-protocol-error"
    STDOUT_EOF = "stdout-eof"
    EOF = "eof"


def spawn_pipe_holder(release: Path) -> None:
    """Start a descendant that inherits this peer's stdout and stderr, as a
    background job does, and holds them until ``release`` exists."""
    if release.exists():
        return
    subprocess.Popen(
        # Stdlib only, so no venv redirector between the peer and its holder.
        [sys._base_executable, "-c", HOLDER_SCRIPT, str(release)],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


class StdioPeer:
    """The ``acp.Agent`` methods an ACP client drives, each able to exit at
    its scripted stage."""

    def __init__(
        self, stage: ExitStage, code: int, lines: list[str], crash_marker: Path | None
    ) -> None:
        self.stage = stage
        self.code = code
        self.lines = lines
        self.crash_marker = crash_marker
        self.mcp_servers: dict[str, list[Any]] = {}

    def exit_process(self) -> None:
        sys.stderr.write("\n".join(self.lines) + "\n")
        sys.stderr.flush()
        # Exit inside a request without letting ACP turn the failure into a reply.
        os._exit(self.code)

    async def fail_protocol(self) -> None:
        sys.stdout.write("[]\n")
        sys.stdout.flush()
        await asyncio.Future[None]()

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        del client_capabilities, kwargs
        match self.stage:
            case ExitStage.INITIALIZE:
                self.exit_process()
            case ExitStage.INITIALIZE_WAIT:
                sys.stderr.write(INITIALIZE_PENDING_LINE + "\n")
                sys.stderr.flush()
                await asyncio.Future[None]()
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=McpCapabilities(http=True, sse=False),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities()
                ),
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        del method_id, kwargs

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        del cwd, kwargs
        if self.stage == ExitStage.NEW_SESSION_PROTOCOL_ERROR:
            await self.fail_protocol()
        session_id = f"stdio-session-{len(self.mcp_servers) + 1}"
        self.mcp_servers[session_id] = list(mcp_servers or [])
        return NewSessionResponse(session_id=session_id, config_options=[])

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
    ) -> LoadSessionResponse:
        del cwd, session_id, mcp_servers, kwargs
        if self.stage == ExitStage.LOAD_SESSION_PROTOCOL_ERROR:
            await self.fail_protocol()
        # Each process starts empty: no session survives its predecessor.
        raise RequestError.resource_not_found()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        del config_id, session_id, value, kwargs
        if self.stage == ExitStage.CONFIG_PROTOCOL_ERROR:
            await self.fail_protocol()
        return SetSessionConfigOptionResponse(config_options=[])

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del session_id, kwargs

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        del session_id, kwargs

    async def prompt(
        self, prompt: Any, session_id: str, message_id: str | None = None, **kwargs: Any
    ) -> PromptResponse:
        del prompt, message_id, kwargs
        await self.exit_on_prompt(session_id)
        return PromptResponse(stop_reason="end_turn")

    async def exit_on_prompt(self, session_id: str) -> None:
        if self.crash_marker is not None:
            if self.crash_marker.exists():
                await self.send_recovered_reply(session_id)
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

    async def send_recovered_reply(self, session_id: str) -> None:
        from band.integrations.mcp import BandMCPTransport  # noqa: PLC0415 -- kept off the startup path
        from band.runtime.tools import BandTool  # noqa: PLC0415 -- kept off the startup path
        from band.runtime.tools.registry import BAND_MCP_SERVER_NAME  # noqa: PLC0415 -- kept off the startup path
        from tests.mcpclient import mcp_session  # noqa: PLC0415 -- kept off the startup path

        server = next(
            server
            for server in self.mcp_servers[session_id]
            if getattr(server, "name", None) == BAND_MCP_SERVER_NAME
        )
        async with mcp_session(server.url, BandMCPTransport(server.type)) as client:
            result = await client.call_tool(
                BandTool.SEND_MESSAGE,
                {"content": RECOVERED_REPLY, "mentions": ["@user-456"]},
            )
        if result.isError:
            raise RuntimeError(f"Band tool failed: {result.content}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=ExitStage, choices=list(ExitStage))
    parser.add_argument("code", type=int)
    parser.add_argument("lines", nargs="*")
    parser.add_argument(CRASH_MARKER_ARG, type=Path)
    parser.add_argument(HOLDER_RELEASE_ARG, type=Path)
    args = parser.parse_args()
    if args.holder_release is not None:
        spawn_pipe_holder(args.holder_release)
    peer = StdioPeer(args.stage, args.code, args.lines, args.crash_marker)
    await run_agent(peer)
    peer.exit_process()


if __name__ == "__main__":
    asyncio.run(main())
