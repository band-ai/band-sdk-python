#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "thenvoi-sdk[langgraph,anthropic,pydantic-ai,claude_sdk,parlant,crewai,a2a,codex]",
#   "python-dotenv>=1.1.1",
# ]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Run Thenvoi SDK agents using the composition pattern.

Usage:
    uv run python examples/run_agent.py                    # Default: langgraph
    uv run python examples/run_agent.py --example langgraph
    uv run python examples/run_agent.py --example pydantic_ai
    uv run python examples/run_agent.py --example pydantic_ai --streaming  # With tool_call/tool_result events
    uv run python examples/run_agent.py --example pydantic_ai --contacts auto      # Auto-approve contacts (CALLBACK)
    uv run python examples/run_agent.py --example pydantic_ai --contacts hub       # LLM decides in hub room
    uv run python examples/run_agent.py --example pydantic_ai --contacts broadcast # Broadcast-only awareness
    uv run python examples/run_agent.py --example pydantic_ai_contacts     # Contact management via chat (legacy)
    uv run python examples/run_agent.py --example pydantic_ai --model anthropic:claude-sonnet-4-5
    uv run python examples/run_agent.py --example anthropic
    uv run python examples/run_agent.py --example anthropic --streaming  # With tool_call/tool_result events
    uv run python examples/run_agent.py --example anthropic --model claude-sonnet-4-5-20250929
    uv run python examples/run_agent.py --example claude_sdk
    uv run python examples/run_agent.py --example claude_sdk --streaming  # With tool_call/tool_result events
    uv run python examples/run_agent.py --example claude_sdk --thinking   # Enable extended thinking
    uv run python examples/run_agent.py --example parlant
    uv run python examples/run_agent.py --example crewai
    uv run python examples/run_agent.py --example crewai --streaming  # Show tool calls
    uv run python examples/run_agent.py --example codex
    uv run python examples/run_agent.py --example codex --agent darter --codex-transport stdio
    uv run python examples/run_agent.py --example codex --agent darter --codex-transport ws --codex-ws-url ws://127.0.0.1:8765
    uv run python examples/run_agent.py --example a2a --a2a-url http://localhost:10000  # A2A bridge
    uv run python examples/run_agent.py --example a2a_gateway              # A2A Gateway (exposes peers)
    uv run python examples/run_agent.py --example a2a_gateway --gateway-port 8080  # Custom port

Configure agent in agent_config.yaml:
    uv run python examples/run_agent.py --agent test_agent
    uv run python examples/run_agent.py --agent my_custom_agent

Setup:
1. Copy .env.example to .env and configure:
   - THENVOI_REST_URL (default: production, change for local dev)
   - THENVOI_WS_URL (default: production, change for local dev)
   - OPENAI_API_KEY (required for langgraph/openai/parlant/crewai models)
   - ANTHROPIC_API_KEY (required for anthropic models)

2. Configure agent in agent_config.yaml

3. For A2A example, start a remote A2A agent first (e.g., LangGraph currency agent)

4. For A2A Gateway example, the gateway exposes Thenvoi platform peers as A2A endpoints
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from thenvoi import Agent
from thenvoi.config import load_agent_config
from thenvoi.core.types import AdapterFeatures, Emit
from thenvoi.platform.event import ContactRequestReceivedEvent, ContactEvent
from thenvoi.runtime.contact_tools import ContactTools
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

# Load environment from .env
load_dotenv()


def build_contact_config(
    mode: str | None,
    logger: logging.Logger,
) -> ContactEventConfig | None:
    """Build ContactEventConfig based on mode.

    Args:
        mode: Contact mode (none, auto, hub, broadcast)
        logger: Logger for auto-approve callback

    Returns:
        ContactEventConfig or None if mode is "none"
    """
    if mode is None or mode == "none":
        return None

    if mode == "auto":

        async def auto_approve(event: ContactEvent, tools: ContactTools) -> None:
            """Auto-approve all contact requests."""
            if isinstance(event, ContactRequestReceivedEvent):
                if event.payload:
                    logger.info(
                        "Auto-approving contact request from %s",
                        event.payload.from_handle,
                    )
                    await tools.respond_contact_request(
                        "approve", request_id=event.payload.id
                    )

        return ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=auto_approve,
            broadcast_changes=True,
        )

    if mode == "hub":
        return ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        )

    if mode == "broadcast":
        return ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )

    raise ValueError(f"Unknown contacts mode: {mode}")


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging."""
    log_level = level.upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


PYDANTIC_AI_INSTRUCTIONS = """
## CRITICAL: Your Capabilities and Limitations

**You have NO internet access and NO real-time data.**
- You CANNOT look up weather, news, stock prices, or any current information
- You MUST NOT invent or guess factual information like temperatures, prices, or dates
- For real-time data (weather, etc.), you MUST delegate to specialized agents (e.g., Weather Agent)

If you don't know something and can't delegate to another agent, say "I don't know" - never make up information.
"""

PARLANT_GUIDELINES = [
    {
        "condition": "User asks for help",
        "action": "Acknowledge and clarify before helping",
    },
    {"condition": "User says goodbye", "action": "Summarize and offer further help"},
]

CREWAI_DEFAULTS = {
    "role": "Research Assistant",
    "goal": "Help users find, analyze, and synthesize information",
    "backstory": "Expert researcher with attention to detail and ability to break down "
    "complex topics into understandable insights.",
}

# When --model is omitted, these per-example defaults kick in.  Examples not
# listed here (e.g. langgraph, claude_sdk) flow through with model=None so the
# adapter / underlying CLI picks its own default.  The user can always pass
# --model to override.
_DEFAULT_MODELS: dict[str, str] = {
    "pydantic_ai": "openai:gpt-4o",  # preserve previous global-default behavior
    "pydantic_ai_contacts": "anthropic:claude-sonnet-4-5",
    "contacts_auto": "anthropic:claude-sonnet-4-5",
    "contacts_hub": "anthropic:claude-sonnet-4-5",
    "contacts_broadcast": "anthropic:claude-sonnet-4-5",
    "anthropic": "claude-sonnet-4-5-20250929",
    "parlant": "gpt-4o",
    "crewai": "gpt-4o-mini",
    # claude_sdk: deliberately omitted — the npm `claude` binary picks its own default.
}

CONTACTS_INSTRUCTIONS = """
## Contact Management Assistant

You help manage contacts and contact requests on the Thenvoi platform.

### Available Actions

When the user asks you to manage contacts, use these tools:

1. **List contacts**: Use `thenvoi_list_contacts` to show current contacts
2. **Check requests**: Use `thenvoi_list_contact_requests` to see pending requests
3. **Approve request**: Use `thenvoi_respond_contact_request` with action="approve"
4. **Reject request**: Use `thenvoi_respond_contact_request` with action="reject"
5. **Add contact**: Use `thenvoi_add_contact` to send a contact request
6. **Remove contact**: Use `thenvoi_remove_contact` to remove an existing contact

### Response Format

When listing contacts or requests, format the information clearly:
- Show names and handles
- For requests, show who sent them and any message included
- Confirm actions after completing them

### Examples

User: "check my contact requests"
→ Call thenvoi_list_contact_requests()

User: "approve alice's request"
→ Call thenvoi_respond_contact_request(action="approve", handle="alice")

User: "reject the request from bob"
→ Call thenvoi_respond_contact_request(action="reject", handle="bob")

User: "list my contacts"
→ Call thenvoi_list_contacts()

User: "add john as a contact"
→ Call thenvoi_add_contact(handle="john")
"""


async def run_langgraph_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    custom_section: str,
    logger: logging.Logger,
) -> None:
    """Run the LangGraph agent."""
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver

    from thenvoi.adapters import LangGraphAdapter

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-4o"),
        checkpointer=InMemorySaver(),
        custom_section=custom_section,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting LangGraph agent...")
    await agent.run()


async def run_pydantic_ai_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    custom_section: str,
    enable_streaming: bool,
    contact_config: ContactEventConfig | None,
    logger: logging.Logger,
) -> None:
    """Run the Pydantic AI agent."""
    from thenvoi.adapters import PydanticAIAdapter

    # Augment custom_section for contact modes
    section = custom_section
    if contact_config:
        if contact_config.strategy == ContactEventStrategy.CALLBACK:
            section += "\n\nContact requests are handled automatically."
        elif contact_config.strategy == ContactEventStrategy.HUB_ROOM:
            section += "\n\nYou will receive contact requests in a hub room. Use tools to approve/reject."
        if contact_config.broadcast_changes:
            section += (
                "\nYou will see system messages when contacts are added or removed."
            )

    adapter = PydanticAIAdapter(
        model=model,
        custom_section=section,
        features=AdapterFeatures(emit={Emit.EXECUTION}) if enable_streaming else None,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    streaming_str = " with execution reporting" if enable_streaming else ""
    contacts_str = ""
    if contact_config:
        contacts_str = f", contacts={contact_config.strategy.value}"
        if contact_config.broadcast_changes:
            contacts_str += "+broadcast"
    logger.info(
        "Starting Pydantic AI agent with model: %s%s%s",
        model,
        streaming_str,
        contacts_str,
    )
    await agent.run()


async def run_anthropic_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    custom_section: str,
    enable_streaming: bool,
    contact_config: ContactEventConfig | None,
    logger: logging.Logger,
) -> None:
    """Run the Anthropic SDK agent."""
    from thenvoi.adapters import AnthropicAdapter

    adapter = AnthropicAdapter(
        model=model,
        prompt=custom_section,
        features=AdapterFeatures(emit={Emit.EXECUTION}) if enable_streaming else None,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    streaming_str = " with execution reporting" if enable_streaming else ""
    contacts_str = (
        f", contacts={contact_config.strategy.value}" if contact_config else ""
    )
    logger.info(
        "Starting Anthropic agent with model: %s%s%s",
        model,
        streaming_str,
        contacts_str,
    )
    await agent.run()


async def run_claude_sdk_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str | None,
    fallback_model: str | None,
    custom_section: str,
    enable_thinking: bool,
    enable_streaming: bool,
    contact_config: ContactEventConfig | None,
    logger: logging.Logger,
) -> None:
    """Run the Claude Agent SDK agent."""
    from thenvoi.adapters import ClaudeSDKAdapter

    adapter = ClaudeSDKAdapter(
        model=model,
        fallback_model=fallback_model,
        custom_section=custom_section,
        max_thinking_tokens=10000 if enable_thinking else None,
        features=AdapterFeatures(emit={Emit.EXECUTION}) if enable_streaming else None,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    options = []
    if enable_thinking:
        options.append("extended thinking")
    if enable_streaming:
        options.append("execution reporting")
    if contact_config:
        options.append(f"contacts={contact_config.strategy.value}")
    if fallback_model:
        options.append(f"fallback_model={fallback_model}")
    options_str = f" with {', '.join(options)}" if options else ""
    logger.info(
        "Starting Claude SDK agent with model: %s%s", model or "auto", options_str
    )
    await agent.run()


async def run_parlant_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    custom_section: str,
    enable_streaming: bool,
    logger: logging.Logger,
) -> None:
    """Run the Parlant agent."""
    from thenvoi.adapters import ParlantAdapter

    adapter = ParlantAdapter(
        model=model,
        custom_section=custom_section,
        guidelines=PARLANT_GUIDELINES,
        features=AdapterFeatures(emit={Emit.EXECUTION}) if enable_streaming else None,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Parlant agent with model: %s", model)
    await agent.run()


async def run_crewai_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    custom_section: str,
    enable_streaming: bool,
    logger: logging.Logger,
) -> None:
    """Run the CrewAI agent."""
    from thenvoi.adapters import CrewAIAdapter

    adapter = CrewAIAdapter(
        model=model,
        role=CREWAI_DEFAULTS["role"],
        goal=CREWAI_DEFAULTS["goal"],
        backstory=CREWAI_DEFAULTS["backstory"],
        custom_section=custom_section,
        features=AdapterFeatures(emit={Emit.EXECUTION}) if enable_streaming else None,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting CrewAI agent with model: %s", model)
    await agent.run()


async def run_codex_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    custom_section: str,
    codex_transport: str,
    codex_ws_url: str,
    codex_model: str | None,
    codex_personality: str,
    codex_approval_policy: str,
    codex_approval_mode: str,
    codex_turn_task_markers: bool,
    codex_cwd: str,
    codex_sandbox: str | None,
    codex_reasoning_effort: str | None,
    logger: logging.Logger,
) -> None:
    """Run the Codex app-server adapter."""
    from thenvoi.adapters import CodexAdapter
    from thenvoi.adapters.codex import CodexAdapterConfig

    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport=codex_transport,  # type: ignore[arg-type]  # str from CLI args, validated at runtime
            cwd=codex_cwd,
            model=codex_model,
            personality=codex_personality,  # type: ignore[arg-type]  # str from CLI args, validated at runtime
            approval_policy=codex_approval_policy,
            approval_mode=codex_approval_mode,  # type: ignore[arg-type]  # str from CLI args, validated at runtime
            sandbox=codex_sandbox,
            reasoning_effort=codex_reasoning_effort,  # type: ignore[arg-type]  # str from CLI args, validated at runtime
            codex_ws_url=codex_ws_url,
            custom_section=custom_section,
            include_base_instructions=True,
            enable_task_events=True,
            emit_turn_task_markers=codex_turn_task_markers,
            enable_execution_reporting=False,
            emit_thought_events=False,
            fallback_send_agent_text=True,
            experimental_api=True,
        )
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info(
        "Starting Codex agent (transport=%s, model=%s, cwd=%s)",
        codex_transport,
        codex_model or "auto",
        codex_cwd,
    )
    await agent.run()


async def run_pydantic_ai_contacts_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    logger: logging.Logger,
) -> None:
    """Run Pydantic AI agent with contact management focus.

    This example demonstrates using contact tools via natural language:
    - "check my contact requests"
    - "list my contacts"
    - "approve alice's request"
    - "reject bob"
    - "add john as a contact"
    """
    from thenvoi.adapters import PydanticAIAdapter

    adapter = PydanticAIAdapter(
        model=model,
        custom_section=CONTACTS_INSTRUCTIONS,
        features=AdapterFeatures(emit={Emit.EXECUTION}),  # Show tool calls
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Pydantic AI contacts agent with model: %s", model)
    logger.info("Try: 'check my contact requests', 'list contacts', 'approve X'")
    await agent.run()


async def run_contacts_auto_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    logger: logging.Logger,
) -> None:
    """Run agent with CALLBACK strategy that auto-approves contact requests.

    This example demonstrates:
    - ContactEventConfig with CALLBACK strategy
    - Auto-approve logic for contact requests
    - broadcast_changes=True to notify all rooms of contact updates
    """
    from thenvoi.adapters import PydanticAIAdapter
    from thenvoi.platform.event import ContactRequestReceivedEvent

    async def auto_approve(event: "ContactEvent", tools: "ContactTools") -> None:
        """Auto-approve all contact requests."""
        if isinstance(event, ContactRequestReceivedEvent):
            if event.payload:
                logger.info(
                    "Auto-approving contact request from %s", event.payload.from_handle
                )
                await tools.respond_contact_request(
                    "approve", request_id=event.payload.id
                )

    config = ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK,
        on_event=auto_approve,
        broadcast_changes=True,  # All rooms see "[Contacts]: @X is now a contact"
    )

    adapter = PydanticAIAdapter(
        model=model,
        custom_section="""You are a helpful assistant. Contact requests are handled automatically.
When you see system messages about new contacts, acknowledge them to the user.""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=config,
    )

    logger.info("Starting contacts auto-approve agent with model: %s", model)
    logger.info("Contact requests will be automatically approved")
    logger.info("All rooms will see broadcast: '@handle (name) is now a contact'")
    await agent.run()


async def run_contacts_hub_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    logger: logging.Logger,
) -> None:
    """Run agent with HUB_ROOM strategy for LLM-driven contact decisions.

    This example demonstrates:
    - ContactEventConfig with HUB_ROOM strategy
    - Contact events routed to a dedicated hub room
    - Agent can reason about requests and respond using tools
    - broadcast_changes=True to notify all rooms of outcomes
    """
    from thenvoi.adapters import PydanticAIAdapter

    config = ContactEventConfig(
        strategy=ContactEventStrategy.HUB_ROOM,
        hub_task_id="contacts-hub",  # Custom task ID for the hub room
        broadcast_changes=True,
    )

    adapter = PydanticAIAdapter(
        model=model,
        custom_section="""You are a helpful assistant that also manages contact requests.

When you receive contact request notifications in the hub room:
1. Review the request details (who sent it, any message included)
2. Decide whether to approve or reject based on the context
3. Use thenvoi_respond_contact_request tool to take action
4. Explain your decision to the user

Contact request format:
- [Contact Request] name (@handle) wants to connect. Message: "..."

Actions available:
- thenvoi_respond_contact_request(action="approve", handle="...")
- thenvoi_respond_contact_request(action="reject", handle="...")
""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=config,
    )

    logger.info("Starting contacts hub room agent with model: %s", model)
    logger.info("Contact events will appear in hub room for LLM reasoning")
    logger.info("All rooms will see broadcasts when contacts change")
    await agent.run()


async def run_contacts_broadcast_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    model: str,
    logger: logging.Logger,
) -> None:
    """Run agent with broadcast-only contact notifications.

    This example demonstrates:
    - ContactEventConfig with DISABLED strategy (no auto-handling)
    - broadcast_changes=True for awareness in all rooms
    - User can manually manage contacts via chat commands
    """
    from thenvoi.adapters import PydanticAIAdapter

    config = ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,  # No auto-handling
        broadcast_changes=True,  # But broadcast updates to all rooms
    )

    adapter = PydanticAIAdapter(
        model=model,
        custom_section=CONTACTS_INSTRUCTIONS
        + """

## System Messages
You will receive system messages when contacts are added or removed.
These appear as "[Contacts]: @handle (name) is now a contact" or similar.
Acknowledge these updates to the user when you see them.
""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=config,
    )

    logger.info("Starting contacts broadcast-only agent with model: %s", model)
    logger.info("Contact changes will be broadcast to all rooms")
    logger.info("Use chat commands to manually manage contacts")
    await agent.run()


async def run_a2a_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    a2a_url: str,
    enable_debug: bool,
    logger: logging.Logger,
) -> None:
    """Run the A2A bridge agent."""
    from thenvoi.adapters import A2AAdapter

    # Enable debug logging for A2A adapter to trace context_id and rehydration
    if enable_debug:
        logging.getLogger("thenvoi.integrations.a2a").setLevel(logging.DEBUG)
        logging.getLogger("thenvoi.converters.a2a").setLevel(logging.DEBUG)

    adapter = A2AAdapter(
        remote_url=a2a_url,
        streaming=True,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting A2A bridge agent (forwarding to %s)...", a2a_url)
    await agent.run()


async def run_a2a_gateway_agent(
    agent_id: str,
    api_key: str,
    rest_url: str,
    ws_url: str,
    gateway_port: int,
    enable_debug: bool,
    logger: logging.Logger,
) -> None:
    """Run the A2A Gateway agent.

    The gateway connects to Thenvoi platform and exposes discovered peers
    as A2A endpoints. External A2A agents can call these peers via standard
    A2A protocol.
    """
    from thenvoi.adapters import A2AGatewayAdapter

    # Enable debug logging for gateway adapter
    if enable_debug:
        logging.getLogger("thenvoi.integrations.a2a.gateway").setLevel(logging.DEBUG)

    gateway_url = f"http://localhost:{gateway_port}"

    adapter = A2AGatewayAdapter(
        rest_url=rest_url,
        api_key=api_key,
        gateway_url=gateway_url,
        port=gateway_port,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting A2A Gateway on %s...", gateway_url)
    logger.info("Peers will be exposed at:")
    logger.info(
        "  - %s/agents/{peer_id}/.well-known/agent.json (discovery)", gateway_url
    )
    logger.info("  - %s/agents/{peer_id}/v1/message:stream (messaging)", gateway_url)
    await agent.run()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Thenvoi SDK test agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python examples/run_agent.py                                     # LangGraph (default)
  uv run python examples/run_agent.py --example langgraph                 # LangGraph with OpenAI
  uv run python examples/run_agent.py --example pydantic_ai               # Pydantic AI with OpenAI
  uv run python examples/run_agent.py --example pydantic_ai --streaming   # With tool_call/tool_result events
  uv run python examples/run_agent.py --example pydantic_ai --model anthropic:claude-sonnet-4-5
  uv run python examples/run_agent.py --example pydantic_ai --contacts auto      # Auto-approve contacts
  uv run python examples/run_agent.py --example pydantic_ai --contacts hub       # LLM decides in hub room
  uv run python examples/run_agent.py --example pydantic_ai --contacts broadcast # Broadcast-only awareness
  uv run python examples/run_agent.py --example anthropic --contacts auto        # Anthropic with auto-approve
  uv run python examples/run_agent.py --example claude_sdk --contacts hub        # Claude SDK with hub room
  uv run python examples/run_agent.py --example anthropic                 # Anthropic SDK
  uv run python examples/run_agent.py --example anthropic --streaming     # With tool_call/tool_result events
  uv run python examples/run_agent.py --example claude_sdk                # Claude Agent SDK
  uv run python examples/run_agent.py --example claude_sdk --streaming    # With tool_call/tool_result events
  uv run python examples/run_agent.py --example claude_sdk --thinking     # With extended thinking
  uv run python examples/run_agent.py --example parlant                   # Parlant adapter
  uv run python examples/run_agent.py --example parlant --streaming       # With tool visibility
  uv run python examples/run_agent.py --example crewai                    # CrewAI adapter
  uv run python examples/run_agent.py --example crewai --streaming        # With tool visibility
  uv run python examples/run_agent.py --example codex                     # Codex app-server adapter
  uv run python examples/run_agent.py --example codex --agent darter      # Run Codex as darter agent
  uv run python examples/run_agent.py --example codex --codex-transport stdio
  uv run python examples/run_agent.py --example codex --codex-transport ws --codex-ws-url ws://127.0.0.1:8765
  uv run python examples/run_agent.py --example a2a                       # A2A bridge (default: localhost:10000)
  uv run python examples/run_agent.py --example a2a --debug               # A2A with debug logging (context_id tracing)
  uv run python examples/run_agent.py --example a2a --a2a-url http://remote:8080  # A2A with custom URL
  uv run python examples/run_agent.py --example a2a_gateway               # A2A Gateway (exposes peers)
  uv run python examples/run_agent.py --example a2a_gateway --debug       # A2A Gateway with debug logging
  uv run python examples/run_agent.py --example a2a_gateway --gateway-port 8080  # Custom gateway port
  uv run python examples/run_agent.py --agent my_custom_agent             # Use different agent config
  uv run python examples/run_agent.py --log-level DEBUG                   # Enable debug logging
        """,
    )
    parser.add_argument(
        "--example",
        "-e",
        choices=[
            "langgraph",
            "pydantic_ai",
            "pydantic_ai_contacts",
            "contacts_auto",
            "contacts_hub",
            "contacts_broadcast",
            "anthropic",
            "claude_sdk",
            "parlant",
            "crewai",
            "codex",
            "a2a",
            "a2a_gateway",
        ],
        default="langgraph",
        help="Which example agent to run (default: langgraph)",
    )
    parser.add_argument(
        "--agent",
        "-g",
        default=None,
        help="Agent key from agent_config.yaml (default: based on --example)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help=(
            "Model override; defaults vary by example (see _DEFAULT_MODELS). "
            "Omit for claude_sdk to let the npm claude binary pick its own default."
        ),
    )
    parser.add_argument(
        "--fallback-model",
        default=None,
        help=(
            "Fallback model passed to ClaudeAgentOptions.fallback_model "
            "(claude_sdk example only; ignored elsewhere). "
            "The npm claude binary uses it when the primary model is unavailable."
        ),
    )
    parser.add_argument(
        "--custom-section",
        "-c",
        default="You are a helpful assistant. Keep responses concise.",
        help="Custom instructions for the agent",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level (default: INFO or LOG_LEVEL env var)",
    )
    parser.add_argument(
        "--thinking",
        "-t",
        action="store_true",
        help="Enable extended thinking for Claude SDK (default: False)",
    )
    parser.add_argument(
        "--streaming",
        "-s",
        action="store_true",
        help="Enable tool call/result visibility for anthropic/claude_sdk/parlant/crewai (default: False)",
    )
    parser.add_argument(
        "--codex-transport",
        choices=["stdio", "ws"],
        default="stdio",
        help="Codex transport mode (default: stdio)",
    )
    parser.add_argument(
        "--codex-ws-url",
        default=os.getenv("CODEX_WS_URL", "ws://127.0.0.1:8765"),
        help="Codex WebSocket URL when --codex-transport=ws",
    )
    parser.add_argument(
        "--codex-role",
        default=None,
        help="Role name; loads prompt from prompts/{role}.md into custom_section",
    )
    parser.add_argument(
        "--codex-personality",
        choices=["friendly", "pragmatic", "none"],
        default="pragmatic",
        help="Codex personality (default: pragmatic)",
    )
    parser.add_argument(
        "--codex-model",
        default=None,
        help="Codex model id override (default: auto-select via model/list)",
    )
    parser.add_argument(
        "--codex-cwd",
        default=os.getcwd(),
        help="Working directory given to Codex app-server (default: current directory)",
    )
    parser.add_argument(
        "--codex-reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
        help="Codex reasoning effort level",
    )
    parser.add_argument(
        "--codex-sandbox",
        default=os.getenv("CODEX_SANDBOX", "external-sandbox"),
        help=(
            "Optional Codex sandbox override. "
            "Examples: external-sandbox, workspace-write, danger-full-access, "
            "or legacy aliases like workspaceWrite."
        ),
    )
    parser.add_argument(
        "--codex-approval-policy",
        default="never",
        help="Codex approvalPolicy value (default: never)",
    )
    parser.add_argument(
        "--codex-approval-mode",
        choices=["manual", "auto_accept", "auto_decline"],
        default="manual",
        help="How adapter answers Codex approval requests (default: manual)",
    )
    parser.add_argument(
        "--codex-turn-task-markers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit synthetic Codex turn started/completed task events (default: False)",
    )
    parser.add_argument(
        "--a2a-url",
        default=os.getenv("A2A_AGENT_URL", "http://localhost:10000"),
        help="URL of the remote A2A agent (default: http://localhost:10000 or A2A_AGENT_URL env var)",
    )
    parser.add_argument(
        "--gateway-port",
        type=int,
        default=int(os.getenv("GATEWAY_PORT", "10000")),
        help="Port for A2A Gateway (default: 10000 or GATEWAY_PORT env var)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging for adapter internals (e.g., A2A context_id tracing)",
    )
    parser.add_argument(
        "--contacts",
        choices=["none", "auto", "hub", "broadcast"],
        default="none",
        help="Contact handling strategy: none (disabled), auto (auto-approve), "
        "hub (LLM decides in hub room), broadcast (awareness only)",
    )

    args = parser.parse_args()

    logger = setup_logging(args.log_level)

    # Set default agent based on example type if not specified
    default_agents = {
        "langgraph": "simple_agent",
        "pydantic_ai": "simple_agent",
        "pydantic_ai_contacts": "simple_agent",
        "contacts_auto": "simple_agent",
        "contacts_hub": "simple_agent",
        "contacts_broadcast": "simple_agent",
        "anthropic": "anthropic_agent",
        "claude_sdk": "anthropic_agent",
        "parlant": "parlant_agent",
        "crewai": "crewai_agent",
        "codex": "simple_agent",
        "a2a": "a2a_agent",
        "a2a_gateway": "a2a_gateway_agent",
    }
    if args.agent is None:
        args.agent = default_agents.get(args.example, "simple_agent")

    # Load URLs from environment
    rest_url = os.getenv("THENVOI_REST_URL")
    ws_url = os.getenv("THENVOI_WS_URL")

    if not rest_url:
        parser.error("THENVOI_REST_URL environment variable is required")
    if not ws_url:
        parser.error("THENVOI_WS_URL environment variable is required")

    # Load agent credentials
    try:
        agent_id, api_key = load_agent_config(args.agent)
    except Exception as e:
        parser.error(f"Failed to load agent config '{args.agent}': {e}")

    logger.info("Agent: %s (%s)", args.agent, agent_id)
    logger.info("Example: %s", args.example)
    logger.info("REST URL: %s", rest_url)
    logger.info("WS URL: %s", ws_url)

    # Build contact config if specified
    contact_config = build_contact_config(args.contacts, logger)
    if contact_config:
        logger.info(
            "Contacts: %s (broadcast=%s)",
            contact_config.strategy.value,
            contact_config.broadcast_changes,
        )

    # Resolve model: when the user didn't pass --model, use the per-example
    # default if listed.  May stay None (e.g. for claude_sdk and langgraph),
    # in which case the adapter / underlying CLI picks its own default.
    model = args.model
    if model is None:
        model = _DEFAULT_MODELS.get(args.example)

    try:
        if args.example == "langgraph":
            await run_langgraph_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                custom_section=args.custom_section,
                logger=logger,
            )
        elif args.example == "pydantic_ai":
            await run_pydantic_ai_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                custom_section=args.custom_section,
                enable_streaming=args.streaming,
                contact_config=contact_config,
                logger=logger,
            )
        elif args.example == "pydantic_ai_contacts":
            await run_pydantic_ai_contacts_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                logger=logger,
            )
        elif args.example == "contacts_auto":
            await run_contacts_auto_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                logger=logger,
            )
        elif args.example == "contacts_hub":
            await run_contacts_hub_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                logger=logger,
            )
        elif args.example == "contacts_broadcast":
            await run_contacts_broadcast_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                logger=logger,
            )
        elif args.example == "anthropic":
            await run_anthropic_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                custom_section=args.custom_section,
                enable_streaming=args.streaming,
                contact_config=contact_config,
                logger=logger,
            )
        elif args.example == "claude_sdk":
            await run_claude_sdk_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                fallback_model=args.fallback_model,
                custom_section=args.custom_section,
                enable_thinking=args.thinking,
                enable_streaming=args.streaming,
                contact_config=contact_config,
                logger=logger,
            )
        elif args.example == "parlant":
            await run_parlant_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                custom_section=args.custom_section,
                enable_streaming=args.streaming,
                logger=logger,
            )
        elif args.example == "crewai":
            await run_crewai_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                model=model,
                custom_section=args.custom_section,
                enable_streaming=args.streaming,
                logger=logger,
            )
        elif args.example == "codex":
            # Load role prompt from file if --codex-role is set
            codex_custom = args.custom_section
            if args.codex_role:
                prompt_file = (
                    Path(__file__).parent
                    / "codex"
                    / "prompts"
                    / f"{args.codex_role}.md"
                )
                if prompt_file.exists():
                    codex_custom = prompt_file.read_text(encoding="utf-8")
                    logger.info("Using role prompt from: %s", prompt_file)
                else:
                    logger.warning(
                        "Role '%s' specified but no prompt file at %s",
                        args.codex_role,
                        prompt_file,
                    )

            await run_codex_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                custom_section=codex_custom,
                codex_transport=args.codex_transport,
                codex_ws_url=args.codex_ws_url,
                codex_model=args.codex_model,
                codex_personality=args.codex_personality,
                codex_approval_policy=args.codex_approval_policy,
                codex_approval_mode=args.codex_approval_mode,
                codex_turn_task_markers=args.codex_turn_task_markers,
                codex_cwd=args.codex_cwd,
                codex_sandbox=args.codex_sandbox,
                codex_reasoning_effort=args.codex_reasoning_effort,
                logger=logger,
            )
        elif args.example == "a2a":
            await run_a2a_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                a2a_url=args.a2a_url,
                enable_debug=args.debug,
                logger=logger,
            )
        elif args.example == "a2a_gateway":
            await run_a2a_gateway_agent(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                gateway_port=args.gateway_port,
                enable_debug=args.debug,
                logger=logger,
            )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    asyncio.run(main())
