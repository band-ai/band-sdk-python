"""ACP (Agent Client Protocol) integration for Band SDK.

This module provides bidirectional ACP support:

1. **ACP Server** (Editor -> Band): Editors use Band as an ACP agent.
   The "Super-Agent" pattern exposes a single ACP facade over multi-agent
   orchestration.

2. **ACP Client Adapter** (Band -> Remote ACP Runtime): Band forwards
   messages to remote ACP runtimes (Codex CLI, Gemini CLI, Claude Code, etc.)
   via a Band bridge layered over a generic ACP runtime.

3. **Architectural analogy to A2A**:
   - Outbound A2A: `A2AAdapter` bridges to a remote A2A peer.
   - Outbound ACP: `ACPClientAdapter` bridges to `ACPRuntime` for subprocess/session plumbing.
   - Inbound A2A: `GatewayServer` + `A2AGatewayAdapter`.
   - Inbound ACP: `ACPServer` + `BandACPServerAdapter`.

   Where ACP differs: outbound ACP can manage local subprocess lifecycle and keep
   runtime-specific behavior in thin profiles; A2A outbound is always remote.

Example (ACP Server):
    from band import Agent
    from band.integrations.acp import BandACPServerAdapter, ACPServer
    from acp import run_agent

    adapter = BandACPServerAdapter(
        rest_url="https://app.band.ai",
        api_key="your-api-key",
    )
    server = ACPServer(adapter)
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.start()
    await run_agent(server)

Example (ACP Client):
    from band import Agent
    from band.integrations.acp import ACPClientAdapter

    adapter = ACPClientAdapter(command="codex", cwd="/workspace")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from band.integrations.acp.client_adapter import ACPClientAdapter
    from band.integrations.acp.client_types import (
        ACPClientSessionState,
        BandACPClient,
    )
    from band.integrations.acp.event_converter import EventConverter
    from band.integrations.acp.push_handler import ACPPushHandler
    from band.integrations.acp.router import AgentRouter
    from band.integrations.acp.server import ACPServer
    from band.integrations.acp.server_adapter import BandACPServerAdapter
    from band.integrations.acp.types import (
        ACPSessionState,
        CollectedChunk,
        PendingACPPrompt,
    )

__all__ = [
    "ACPClientAdapter",
    "ACPClientSessionState",
    "ACPPushHandler",
    "ACPServer",
    "ACPSessionState",
    "AgentRouter",
    "BandACPClient",
    "BandACPServerAdapter",
    "CollectedChunk",
    "EventConverter",
    "PendingACPPrompt",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ACPClientAdapter": ("band.integrations.acp.client_adapter", "ACPClientAdapter"),
    "ACPClientSessionState": (
        "band.integrations.acp.client_types",
        "ACPClientSessionState",
    ),
    "BandACPClient": ("band.integrations.acp.client_types", "BandACPClient"),
    "EventConverter": ("band.integrations.acp.event_converter", "EventConverter"),
    "ACPPushHandler": ("band.integrations.acp.push_handler", "ACPPushHandler"),
    "AgentRouter": ("band.integrations.acp.router", "AgentRouter"),
    "ACPServer": ("band.integrations.acp.server", "ACPServer"),
    "BandACPServerAdapter": (
        "band.integrations.acp.server_adapter",
        "BandACPServerAdapter",
    ),
    "ACPSessionState": ("band.integrations.acp.types", "ACPSessionState"),
    "CollectedChunk": ("band.integrations.acp.types", "CollectedChunk"),
    "PendingACPPrompt": ("band.integrations.acp.types", "PendingACPPrompt"),
}


def __getattr__(name: str) -> object:
    if name in _IMPORT_MAP:
        module_path, attr_name = _IMPORT_MAP[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
