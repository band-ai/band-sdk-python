"""Orchestrator agent that calls A2A Gateway peers.

This module provides a LangGraph-based orchestrator agent that can route
user requests to appropriate peers exposed by the A2A Gateway.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable
from typing import Annotated, Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

try:
    from .remote_agent import GatewayClient
except ImportError:
    from remote_agent import GatewayClient

logger = logging.getLogger(__name__)

# Global gateway client instance (set by OrchestratorAgent)
_gateway_client: GatewayClient | None = None

def set_gateway_client(client: GatewayClient) -> None:
    """Set the global gateway client for tools to use."""
    global _gateway_client
    _gateway_client = client

@tool
async def call_peer_agent(
    peer_id: str,
    message: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Call a Thenvoi platform peer via the A2A Gateway.

    Use this tool to delegate tasks to specialized agents available on the
    Thenvoi platform. Each peer has specific capabilities - choose the
    appropriate peer based on the user's request.

    Args:
        peer_id: The ID of the peer to call (e.g., 'weather', 'servicenow', 'data-analyst')
        message: The message to send to the peer. Be specific and provide all
            necessary context for the peer to complete the task.

    Returns:
        The peer's response containing the result of the delegated task.
    """
    if _gateway_client is None:
        return "Error: Gateway client not initialized"

    # Extract context_id from LangGraph config to preserve conversation session
    context_id = config.get("configurable", {}).get("thread_id")

    try:
        response = await _gateway_client.call_peer(
            peer_id,
            message,
            context_id=context_id,
        )
        return response
    except Exception as e:
        logger.error("Error calling peer '%s': %s", peer_id, e)
        return f"Error calling peer '{peer_id}': {e}"

class ResponseFormat(BaseModel):
    """Response format for the orchestrator agent."""

    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str

class OrchestratorAgent:
    """Orchestrator agent that routes requests to A2A Gateway peers.

    This agent uses a LangGraph ReAct pattern to intelligently route user
    requests to specialized peers available via the A2A Gateway.

    Example:
        import logging
        logger = logging.getLogger(__name__)

        agent = OrchestratorAgent(
            gateway_url="http://localhost:10000",
            available_peers=["weather", "servicenow"],
        )
        async for item in agent.stream("What's the weather in NYC?", "ctx-123"):
            logger.info("%s", item)
    """

    SYSTEM_INSTRUCTION = """You are an orchestrator agent that helps users by routing their requests to specialized agents on the Thenvoi platform.

Available peers you can call via the A2A Gateway:
{peers_list}

Your role:
1. Understand what the user is asking for
2. Determine which peer(s) can best handle the request
3. Use the call_peer_agent tool to delegate to the appropriate peer
4. Synthesize responses from peers into a helpful answer

If no suitable peer is available for a request, explain what peers are available and what they can help with.

Always be helpful and provide clear responses to the user."""

    FORMAT_INSTRUCTION = (
        "Set response status to input_required if the user needs to provide more information. "
        "Set response status to error if there is an error while processing the request. "
        "Set response status to completed if the request is complete."
    )

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(
        self,
        gateway_url: str,
        available_peers: list[str] | None = None,
        model: str = "gpt-5.4-mini",
    ):
        """Initialize the orchestrator agent.

        Args:
            gateway_url: Base URL of the A2A Gateway
            available_peers: List of available peer IDs (optional, will be shown in system prompt)
            model: OpenAI model to use (default: gpt-5.4-mini)
        """
        self.gateway_url = gateway_url
        self.available_peers = available_peers or []

        # Set up gateway client for tools
        self.gateway_client = GatewayClient(gateway_url)
        set_gateway_client(self.gateway_client)

        # Build system prompt with available peers
        if self.available_peers:
            peers_list = "\n".join(f"- {peer}" for peer in self.available_peers)
        else:
            peers_list = "(Peers will be discovered dynamically from the gateway)"

        system_prompt = self.SYSTEM_INSTRUCTION.format(peers_list=peers_list)

        # Create LangGraph agent
        self.model = ChatOpenAI(model=model)
        self.memory = MemorySaver()
        self.tools = [call_peer_agent]

        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=self.memory,
            prompt=system_prompt,
            response_format=(self.FORMAT_INSTRUCTION, ResponseFormat),
        )

    async def stream(
        self, query: str, context_id: str
    ) -> AsyncIterable[dict[str, Any]]:
        """Stream responses from the orchestrator agent.

        Args:
            query: User's query
            context_id: Context ID for conversation continuity

        Yields:
            Response items with status and content
        """
        inputs = {"messages": [("user", query)]}
        config = {"configurable": {"thread_id": context_id}}

        # Stream through the graph (async for async tool support)
        async for item in self.graph.astream(inputs, config, stream_mode="values"):
            message = item["messages"][-1]

            # Check if the agent is making tool calls
            if hasattr(message, "tool_calls") and message.tool_calls:
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Routing request to peer agent...",
                }

        # Get final response
        yield await self._aget_agent_response(config)

    async def _aget_agent_response(self, config: dict) -> dict[str, Any]:
        """Get the structured response from the agent (async).

        Args:
            config: LangGraph config with thread_id

        Returns:
            Response dict with status and content
        """
        current_state = await self.graph.aget_state(config)
        structured_response = current_state.values.get("structured_response")

        if structured_response and isinstance(structured_response, ResponseFormat):
            if structured_response.status == "input_required":
                return {
                    "is_task_complete": False,
                    "require_user_input": True,
                    "content": structured_response.message,
                }
            if structured_response.status == "error":
                return {
                    "is_task_complete": False,
                    "require_user_input": True,
                    "content": structured_response.message,
                }
            if structured_response.status == "completed":
                return {
                    "is_task_complete": True,
                    "require_user_input": False,
                    "content": structured_response.message,
                }

        return {
            "is_task_complete": False,
            "require_user_input": True,
            "content": "Unable to process your request. Please try again.",
        }

    async def close(self) -> None:
        """Close the gateway client."""
        await self.gateway_client.close()
