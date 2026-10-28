"""Drive the real ACP wire with an Antigravity-shaped FakeACPAgent."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from acp import RequestError, run_agent
from acp.schema import (
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    RequestPermissionResponse,
    SetSessionConfigOptionResponse,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.adapters.antigravity_acp import TESTED_ANTIGRAVITY_VERSION
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.antigravity_acp.peerconfig import (
    PeerCommand,
    PeerEvent,
    PeerSettings,
)
from tests.framework_conformance.turnprobes import ALICE
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent

PEER_HOST = "127.0.0.1"
PEER_AGENT_NAME = "antigravity-test-peer"
SESSION_FILE = "peer-sessions.json"
JOURNAL_FILE = "peer-journal.jsonl"


class PeerEnvironment(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    GEMINI_HOME: str
    GEMINI_API_KEY: str | None = None


class AntigravityPeer(FakeACPAgent):
    """Persist provider session identities and record the actual received inputs."""

    def __init__(self, config: PeerSettings) -> None:
        super().__init__(
            supports_session_load=True, config_options=config.config_options
        )
        self.config = config
        self.environment = PeerEnvironment()
        self.home = Path(self.environment.GEMINI_HOME)
        if config.journal_root is None:
            raise ValueError("the peer requires its journal directory")
        self.journal = Path(config.journal_root) / self.home.name / JOURNAL_FILE
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.native_sessions = self.home / SESSION_FILE
        if self.native_sessions.exists():
            for session_id in json.loads(self.native_sessions.read_text()):
                self.knows_session(session_id)
                self.sessions.append({"session_id": session_id})
        self.active_session: str | None = None
        self.control_task: asyncio.Task[None] | None = None
        self.prompt_started = asyncio.Event()
        self.prompt_release = asyncio.Event()
        self.on_prompt(self.answer)
        self.stops_with(config.stop_reason)
        self.record(
            PeerEvent.STARTED,
            home=str(self.home),
            key=self.environment.GEMINI_API_KEY,
        )

    def record(self, event: PeerEvent, **data: Any) -> None:
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"event": event, **data}) + "\n")

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        response = await super().initialize(
            protocol_version, client_capabilities, **kwargs
        )
        if self.config.control_port is not None:
            self.control_task = asyncio.create_task(
                self.control(self.config.control_port)
            )
        capabilities = response.agent_capabilities
        return response.model_copy(
            update={
                "agent_capabilities": (
                    capabilities.model_copy(update={"session_capabilities": None})
                    if capabilities is not None
                    else None
                ),
                "agent_info": Implementation(
                    name=PEER_AGENT_NAME, version=TESTED_ANTIGRAVITY_VERSION
                ),
            }
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        await super().authenticate(method_id, **kwargs)
        self.record(PeerEvent.AUTHENTICATED, method=method_id)

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        response = await super().new_session(cwd, mcp_servers, **kwargs)
        self.knows_session(response.session_id)
        self.native_sessions.write_text(json.dumps(self.session_ids()))
        self.active_session = response.session_id
        self.record(PeerEvent.CREATED, session=response.session_id, cwd=cwd)
        return response

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
    ) -> LoadSessionResponse:
        response = await super().load_session(cwd, session_id, mcp_servers, **kwargs)
        self.active_session = session_id
        self.record(PeerEvent.LOADED, session=session_id)
        return response

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        response = await super().set_config_option(
            config_id, session_id, value, **kwargs
        )
        self.record(PeerEvent.CONFIGURED, option=config_id, value=value)
        return response

    async def permission(self, session_id: str) -> RequestPermissionResponse:
        call = self.config.permission
        if call is None:
            raise ValueError("control permission requires a configured tool call")
        response = await self.ask_permission(session_id, call, self.config.options)
        self.record(PeerEvent.PERMISSION, response=response.model_dump(mode="json"))
        return response

    async def answer(self, agent: FakeACPAgent, session_id: str) -> None:
        del agent
        self.record(PeerEvent.PROMPTED, text=self.prompt_texts()[-1])
        self.prompt_started.set()
        if self.config.hold_prompt:
            await self.prompt_release.wait()
        if self.config.prompt_error:
            raise RequestError.internal_error()
        if self.config.permission is not None and self.config.permission_on_prompt:
            await self.permission(session_id)
        if self.config.narration is not None:
            await self.emit(session_id, self.config.narration)
        if self.config.text is not None:
            await self.say(session_id, self.config.text)
        if self.config.reply is not None:
            await self.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={
                    "content": self.config.reply,
                    "mentions": [f"@{ALICE['id']}"],
                },
            )

    async def control(self, port: int) -> None:
        reader, writer = await asyncio.open_connection(PEER_HOST, port)
        try:
            while line := await reader.readline():
                command = PeerCommand(line.decode().strip())
                match command:
                    case PeerCommand.PERMISSION:
                        if self.active_session is None:
                            raise ValueError(
                                "permission requested before a session exists"
                            )
                        response = await self.permission(self.active_session)
                        payload = response.model_dump(mode="json")
                    case PeerCommand.PROMPTED:
                        await self.prompt_started.wait()
                        payload = {"event": PeerEvent.PROMPTED}
                    case PeerCommand.RELEASE_PROMPT:
                        self.prompt_release.set()
                        payload = {"command": command}
                writer.write(json.dumps(payload).encode() + b"\n")
                await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def close_control(self) -> None:
        if self.control_task is not None:
            self.control_task.cancel()
            await asyncio.gather(self.control_task, return_exceptions=True)


async def main() -> None:
    config = PeerSettings.model_validate_json(Path(sys.argv[1]).read_text())
    peer = AntigravityPeer(config)
    try:
        await run_agent(peer)
    finally:
        await peer.close_control()


if __name__ == "__main__":
    asyncio.run(main())
