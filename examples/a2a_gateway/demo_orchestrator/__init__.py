"""Demo Orchestrator Agent.

A demo A2A agent that uses the A2A Gateway to call Band platform peers.
This agent acts as a client to the gateway, demonstrating end-to-end
agent-to-agent communication.

Usage:
    uv run python examples/a2a_gateway/demo_orchestrator/__main__.py --gateway-url http://localhost:10000

Architecture:
    User → Demo Orchestrator → A2A Gateway → Band Platform → Peer
                             ↑                                    ↓
                             ←←←←←←← SSE Response ←←←←←←←←←←←←←←←←
"""

try:
    from .agent import OrchestratorAgent
    from .agent_executor import OrchestratorAgentExecutor
    from .remote_agent import GatewayClient
except ImportError:
    from agent import OrchestratorAgent
    from agent_executor import OrchestratorAgentExecutor
    from remote_agent import GatewayClient

__all__ = ["OrchestratorAgent", "OrchestratorAgentExecutor", "GatewayClient"]
