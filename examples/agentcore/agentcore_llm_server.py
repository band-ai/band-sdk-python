"""MCP tool server for AgentCore — wraps Claude as a chat tool.

Deploy this as a Bedrock AgentCore runtime to expose an LLM-powered
agent via MCP protocol. The bridge's AgentCoreHandler sends
``tools/call`` requests which this server handles.

Local testing:
    pip install mcp anthropic
    ANTHROPIC_API_KEY=sk-... python agentcore_llm_server.py

Deploy to AgentCore:
    Package this as a container image and register it via
    ``create_agent_runtime`` in the bedrock-agentcore-control API.
"""

from __future__ import annotations

import logging
import os

import anthropic
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("thenvoi-llm-agent")

_client: anthropic.Anthropic | None = None
_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
_system_prompt = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful assistant connected to the Thenvoi platform. "
    "Be concise and friendly. Respond directly to what the user asks.",
)


def _get_client() -> anthropic.Anthropic:
    """Return the Anthropic client, creating it lazily on first use."""
    global _client  # noqa: PLW0603
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


@mcp.tool()
def chat(message: str) -> str:
    """Chat with the user. Receives a message and returns an AI response.

    Args:
        message: The user's message to respond to.

    Returns:
        The AI assistant's response.
    """
    logger.info("Received message: %s", message[:100])

    client = _get_client()
    response = client.messages.create(
        model=_model,
        max_tokens=1024,
        system=_system_prompt,
        messages=[{"role": "user", "content": message}],
    )

    reply = response.content[0].text
    logger.info("Response: %s", reply[:100])
    return reply


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
