"""
Anthropic adapter using SimpleAdapter pattern.

Extracted from thenvoi.integrations.anthropic.agent.ThenvoiAnthropicAgent.
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import Any, ClassVar, cast

from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam, ToolParam, ToolUseBlock

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
)
from thenvoi.converters.anthropic import AnthropicHistoryConverter, AnthropicMessages
from thenvoi.runtime.custom_tools import (
    CustomToolDef,
    custom_tools_to_schemas,
    execute_custom_tool,
    find_custom_tool,
)
from thenvoi.runtime.prompts import render_system_prompt

logger = logging.getLogger(__name__)


class AnthropicAdapter(SimpleAdapter[AnthropicMessages]):
    """
    Anthropic SDK adapter using SimpleAdapter pattern.

    Uses the Anthropic Python SDK directly for Claude interactions,
    with manual conversation history and tool loop management.

    Example:
        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5-20250929",
            prompt="You are a helpful assistant.",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY},
                emit={Emit.EXECUTION},
            ),
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        api_key: str | None = None,
        system_prompt: str | None = None,
        prompt: str | None = None,
        max_tokens: int = 4096,
        history_converter: AnthropicHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        features: AdapterFeatures | None = None,
        include_base_instructions: bool = True,
        # --- Deprecated (one release, then remove) ---
        anthropic_api_key: str | None = None,
        custom_section: str | None = None,
        enable_execution_reporting: bool = False,
        enable_memory_tools: bool = False,
    ):
        # --- Selective: api_key rename ---
        if anthropic_api_key is not None:
            warnings.warn(
                "anthropic_api_key is deprecated, use api_key instead",
                DeprecationWarning,
                stacklevel=2,
            )
            if api_key is not None:
                raise ThenvoiConfigError(
                    "Cannot pass both api_key and anthropic_api_key"
                )
            api_key = anthropic_api_key

        # --- Selective: prompt rename ---
        if custom_section is not None:
            warnings.warn(
                "custom_section is deprecated, use prompt instead",
                DeprecationWarning,
                stacklevel=2,
            )
            if prompt is not None:
                raise ThenvoiConfigError("Cannot pass both prompt and custom_section")
            prompt = custom_section

        # --- Universal: boolean → AdapterFeatures migration ---
        if enable_memory_tools or enable_execution_reporting:
            if features is not None:
                raise ThenvoiConfigError(
                    "Cannot pass both features= and legacy boolean params "
                    "(enable_memory_tools, enable_execution_reporting)"
                )
            warnings.warn(
                "enable_memory_tools/enable_execution_reporting are deprecated, "
                "use features=AdapterFeatures(...) instead",
                DeprecationWarning,
                stacklevel=2,
            )
            caps: frozenset[Capability] = frozenset()
            emit: frozenset[Emit] = frozenset()
            if enable_memory_tools:
                caps = caps | frozenset({Capability.MEMORY})
            if enable_execution_reporting:
                emit = emit | frozenset({Emit.EXECUTION})
            features = AdapterFeatures(capabilities=caps, emit=emit)

        super().__init__(
            history_converter=history_converter or AnthropicHistoryConverter(),
            features=features,
        )

        self.model = model
        self.system_prompt = system_prompt
        self._prompt = prompt
        self._include_base_instructions = include_base_instructions
        self.max_tokens = max_tokens

        # Anthropic client (uses ANTHROPIC_API_KEY env var if not provided)
        self.client = AsyncAnthropic(api_key=api_key)

        # Per-room conversation history (Anthropic SDK is stateless)
        self._message_history: dict[str, list[dict[str, Any]]] = {}
        # Rendered system prompt (set after start)
        self._system_prompt: str = ""
        # Custom tools (user-provided)
        self._custom_tools: list[CustomToolDef] = additional_tools or []

    # --- Copied from ThenvoiAnthropicAgent._on_started ---
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt after agent metadata is fetched.

        Prompt precedence:
          1. If ``system_prompt`` was provided at construction time, it wins —
             ``prompt``, ``include_base_instructions``, and ``features``-based
             capability sections are all ignored.
          2. Otherwise, ``render_system_prompt`` renders the SDK base prompt
             (unless ``include_base_instructions=False``) plus ``prompt``, with
             capability sections gated on ``features.capabilities``.
        """
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self.system_prompt or render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self._prompt or "",
            include_base_instructions=self._include_base_instructions,
            features=self.features,
        )
        logger.info("Anthropic adapter started for agent: %s", agent_name)

    # --- Adapted from ThenvoiAnthropicAgent._handle_message ---
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: AnthropicMessages,  # Already converted by SimpleAdapter
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle incoming message.

        KEY DESIGN:
        - System prompt sent ONLY on first API call (part of messages param)
        - Historical messages injected on first message to prime conversation
        - Participant list injected ONLY when it changes
        - Contact changes injected when broadcast
        - Tool loop runs until no more tool_use blocks
        """
        logger.debug("Handling message %s in room %s", msg.id, room_id)

        # Initialize history for this room on first message
        # Note: history is already converted by SimpleAdapter via history_converter
        if is_session_bootstrap:
            if history:
                self._message_history[room_id] = list(history)
                logger.info(
                    "Room %s: Loaded %s historical messages",
                    room_id,
                    len(history),
                )
            else:
                self._message_history[room_id] = []
                logger.info("Room %s: No historical messages found", room_id)
        elif room_id not in self._message_history:
            # Safety: ensure history exists even if not first message
            self._message_history[room_id] = []

        # Inject participants message if changed
        if participants_msg:
            self._message_history[room_id].append(
                {
                    "role": "user",
                    "content": f"[System]: {participants_msg}",
                }
            )
            logger.info("Room %s: Participants updated", room_id)

        # Inject contacts message if present
        if contacts_msg:
            self._message_history[room_id].append(
                {
                    "role": "user",
                    "content": f"[System]: {contacts_msg}",
                }
            )
            logger.info("Room %s: Contacts broadcast received", room_id)

        # Add current message
        user_message = msg.format_for_llm()
        self._message_history[room_id].append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        # Log message count
        total_messages = len(self._message_history[room_id])
        logger.info(
            "Room %s: Calling Anthropic with %s messages (first_msg=%s)",
            room_id,
            total_messages,
            is_session_bootstrap,
        )

        # Get tool schemas in Anthropic format (typed helper)
        include_memory = Capability.MEMORY in self.features.capabilities
        include_contacts = Capability.CONTACTS in self.features.capabilities
        tool_schemas = tools.get_anthropic_tool_schemas(
            include_memory=include_memory,
            include_contacts=include_contacts,
        )
        # Merge custom tool schemas
        if self._custom_tools:
            tool_schemas = list(tool_schemas)  # Make mutable copy
            custom_schemas = custom_tools_to_schemas(self._custom_tools, "anthropic")
            tool_schemas.extend(cast(list[ToolParam], custom_schemas))

        # Tool loop - let LLM decide when to stop
        while True:
            try:
                response = await self._call_anthropic(
                    messages=self._message_history[room_id],
                    tools=tool_schemas,
                )
            except Exception as e:
                logger.error("Error calling Anthropic: %s", e, exc_info=True)
                await self._report_error(tools, str(e))
                raise  # Re-raise so message is marked as failed

            # Check for tool use
            if response.stop_reason != "tool_use":
                # No more tool calls - extract text content if any
                text_content = self._extract_text_content(response.content)
                if text_content:
                    self._message_history[room_id].append(
                        {
                            "role": "assistant",
                            "content": text_content,
                        }
                    )
                logger.debug(
                    "Room %s: Completed with stop_reason=%s",
                    room_id,
                    response.stop_reason,
                )
                break

            # Add assistant response with tool_use blocks to history
            serialized_content = self._serialize_content_blocks(response.content)
            self._message_history[room_id].append(
                {
                    "role": "assistant",
                    "content": serialized_content,
                }
            )

            # Process tool calls
            tool_results = await self._process_tool_calls(response, tools)

            # Add tool results to history
            self._message_history[room_id].append(
                {
                    "role": "user",
                    "content": tool_results,
                }
            )

        logger.debug(
            "Message %s processed successfully (history now has %s messages)",
            msg.id,
            len(self._message_history[room_id]),
        )

    # --- Copied from ThenvoiAnthropicAgent._cleanup_session ---
    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up message history", room_id)

    # --- Copied from ThenvoiAnthropicAgent._call_anthropic ---
    async def _call_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolParam],
    ) -> Message:
        """
        Call Anthropic API with messages and tools.

        Args:
            messages: Conversation history
            tools: Tool schemas in Anthropic format

        Returns:
            Anthropic Message response
        """
        return await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system_prompt,
            messages=cast(list[MessageParam], messages),
            tools=tools,
        )

    # --- Copied from ThenvoiAnthropicAgent._extract_text_content ---
    def _extract_text_content(self, content: list) -> str:
        """Extract text content from response content blocks."""
        from anthropic.types import TextBlock

        texts = []
        for block in content:
            if isinstance(block, TextBlock) and block.text:
                texts.append(block.text)
        return " ".join(texts) if texts else ""

    # --- Copied from ThenvoiAnthropicAgent._serialize_content_blocks ---
    def _serialize_content_blocks(self, content: list) -> list[dict[str, Any]]:
        """Serialize content blocks to dict format for message history."""
        from anthropic.types import TextBlock

        serialized = []
        for block in content:
            if isinstance(block, ToolUseBlock):
                serialized.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
            elif isinstance(block, TextBlock):
                if block.text:  # Only include non-empty text
                    serialized.append(
                        {
                            "type": "text",
                            "text": block.text,
                        }
                    )
        return serialized

    # --- Copied from ThenvoiAnthropicAgent._process_tool_calls ---
    async def _process_tool_calls(
        self, response: Message, tools: AgentToolsProtocol
    ) -> list[dict[str, Any]]:
        """
        Process tool_use blocks from response and execute tools.

        Args:
            response: Anthropic Message with tool_use blocks
            tools: AgentToolsProtocol instance for execution

        Returns:
            List of tool_result content blocks for next API call
        """
        tool_results = []

        for block in response.content:
            if not isinstance(block, ToolUseBlock):
                continue

            tool_name = block.name
            tool_input = block.input
            tool_use_id = block.id

            logger.debug("Executing tool: %s with input: %s", tool_name, tool_input)

            # Report tool call if enabled (JSON format with tool_call_id for linking)
            # Best-effort: event reporting must never crash tool execution
            if Emit.EXECUTION in self.features.emit:
                try:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": tool_name,
                                "args": tool_input,
                                "tool_call_id": tool_use_id,
                            }
                        ),
                        message_type="tool_call",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to send tool_call event: %s",
                        e,
                    )

            # Execute tool (check custom tools first, then platform tools)
            try:
                custom_tool = find_custom_tool(self._custom_tools, tool_name)
                if custom_tool:
                    result = await execute_custom_tool(custom_tool, tool_input)
                else:
                    result = await tools.execute_tool_call(tool_name, tool_input)
                result_str = (
                    json.dumps(result, default=str)
                    if not isinstance(result, str)
                    else result
                )
                is_error = False
            except Exception as e:
                result_str = f"Error: {e}"
                is_error = True
                logger.error("Tool %s failed: %s", tool_name, e)

            # Report tool result if enabled (JSON format with tool_call_id for linking)
            # Best-effort: event reporting must never crash tool execution
            if Emit.EXECUTION in self.features.emit:
                try:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": tool_name,
                                "output": result_str,
                                "tool_call_id": tool_use_id,
                            }
                        ),
                        message_type="tool_result",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to send tool_result event: %s",
                        e,
                    )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_str,
                    "is_error": is_error,
                }
            )

        return tool_results

    # --- Copied from BaseFrameworkAgent._report_error ---
    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception as e:
            logger.warning("Failed to send error event: %s", e)
