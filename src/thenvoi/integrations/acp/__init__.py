"""ACP (Agent Client Protocol) integration for Thenvoi SDK.

This module provides bidirectional ACP support:

1. **ACP Server** (Editor -> Thenvoi): Editors use Thenvoi as an ACP agent.
   The "Super-Agent" pattern exposes a single ACP facade over multi-agent
   orchestration.

2. **ACP Client Adapter** (Thenvoi -> External ACP Runtime): Thenvoi forwards
   messages to external ACP runtimes (Codex CLI, Gemini CLI, Claude Code, etc.)
   via a Thenvoi bridge layered over a generic ACP runtime.

3. **Architectural analogy to A2A**:
   - Outbound A2A: `A2AAdapter` bridges to a remote A2A peer.
   - Outbound ACP: `ACPClientAdapter` bridges to `ACPRuntime` for subprocess/session plumbing.
   - Inbound A2A: `GatewayServer` + `A2AGatewayAdapter`.
   - Inbound ACP: `ACPServer` + `ThenvoiACPServerAdapter`.

   Where ACP differs: outbound ACP can manage local subprocess lifecycle and keep
   runtime-specific behavior in thin profiles; A2A outbound is always remote.

Example (ACP Server):
    from thenvoi import Agent
    from thenvoi.integrations.acp import ThenvoiACPServerAdapter, ACPServer
    from acp import run_agent

    adapter = ThenvoiACPServerAdapter(
        rest_url="https://app.thenvoi.com",
        api_key="your-api-key",
    )
    server = ACPServer(adapter)
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.start()
    await run_agent(server)

Example (ACP Client):
    from thenvoi import Agent
    from thenvoi.integrations.acp import ACPClientAdapter

    adapter = ACPClientAdapter(command="codex", cwd="/workspace")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thenvoi.integrations.acp.client_adapter import ACPClientAdapter
    from thenvoi.integrations.acp.client_types import (
        ACPClientSessionState,
        ThenvoiACPClient,
    )
    from thenvoi.integrations.acp.event_converter import EventConverter
    from thenvoi.integrations.acp.push_handler import ACPPushHandler
    from thenvoi.integrations.acp.router import AgentRouter
    from thenvoi.integrations.acp.server import ACPServer
    from thenvoi.integrations.acp.server_adapter import ThenvoiACPServerAdapter
    from thenvoi.integrations.acp.types import (
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
    "CollectedChunk",
    "EventConverter",
    "PendingACPPrompt",
    "ThenvoiACPClient",
    "ThenvoiACPServerAdapter",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ACPClientAdapter": ("thenvoi.integrations.acp.client_adapter", "ACPClientAdapter"),
    "ACPClientSessionState": (
        "thenvoi.integrations.acp.client_types",
        "ACPClientSessionState",
    ),
    "ThenvoiACPClient": ("thenvoi.integrations.acp.client_types", "ThenvoiACPClient"),
    "EventConverter": ("thenvoi.integrations.acp.event_converter", "EventConverter"),
    "ACPPushHandler": ("thenvoi.integrations.acp.push_handler", "ACPPushHandler"),
    "AgentRouter": ("thenvoi.integrations.acp.router", "AgentRouter"),
    "ACPServer": ("thenvoi.integrations.acp.server", "ACPServer"),
    "ThenvoiACPServerAdapter": (
        "thenvoi.integrations.acp.server_adapter",
        "ThenvoiACPServerAdapter",
    ),
    "ACPSessionState": ("thenvoi.integrations.acp.types", "ACPSessionState"),
    "CollectedChunk": ("thenvoi.integrations.acp.types", "CollectedChunk"),
    "PendingACPPrompt": ("thenvoi.integrations.acp.types", "PendingACPPrompt"),
}


def __getattr__(name: str) -> object:
    if name in _IMPORT_MAP:
        module_path, attr_name = _IMPORT_MAP[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
