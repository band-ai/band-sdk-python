"""Serve the SDK startup command without replacing client-side readiness."""

from __future__ import annotations

from pathlib import Path

from band.integrations.ompprocess import (
    OMP_MCP_RECEIPT,
    McpStartupReceipt,
    omp_mcp_ready_command,
)
from band.integrations.ompwire import OmpCommandName
from tests.integrations.ompframes import Frame
from tests.integrations.omppeers.base import RpcPeer


class StartupPeer(RpcPeer):
    def __init__(self, plugin: Path | None) -> None:
        super().__init__()
        self.plugin = plugin

    def prompt(self, frame: Frame) -> None:
        if (
            self.plugin is None
            or frame.message != f"/{omp_mcp_ready_command(str(self.plugin))}"
        ):
            self.run_prompt(frame)
            return
        self.prompt_id = frame.id
        self.settled = False
        receipt = McpStartupReceipt(systemPromptReady=True, servers=[])
        (self.plugin / OMP_MCP_RECEIPT).write_text(
            receipt.model_dump_json(), encoding="utf-8"
        )
        self.respond(frame, {"agentInvoked": False})
        self.settle_session()

    def run_prompt(self, frame: Frame) -> None:
        super().prompt(frame)

    def handle_command(self, frame: Frame) -> None:
        if frame.type == OmpCommandName.GET_AVAILABLE_COMMANDS:
            commands = (
                []
                if self.plugin is None
                else [{"name": omp_mcp_ready_command(str(self.plugin))}]
            )
            self.respond(frame, {"commands": commands})
            return
        super().handle_command(frame)
