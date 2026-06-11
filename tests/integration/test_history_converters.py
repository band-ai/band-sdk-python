"""Integration tests for history converters with real platform data.

These tests verify that history converters work correctly with real session
history from the platform. This catches any mismatches between the expected
format in the converters and the actual format stored by the platform.

Room sharing strategy:
    All test classes share a single ``shared_room`` fixture (session-scoped)
    to stay within the platform's 10-room-per-agent limit. Each test posts
    messages with a unique ``uuid`` prefix and filters assertions by that
    prefix, so concurrent or sequential runs don't interfere. The tradeoff
    is that room history grows over time; if converter tests start failing
    due to context window limits, either prune the room or create a fresh
    agent.

Run with: uv run pytest tests/integration/test_history_converters.py -v -s
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from thenvoi_rest import ChatEventRequest, ChatMessageRequest
from thenvoi_rest.types import ChatMessageRequestMentionsItem as Mention

from tests.integration.conftest import fetch_all_context, requires_api

logger = logging.getLogger(__name__)


def _create_tool_call_content(
    tool_name: str,
    args: dict,
    tool_call_id: str,
) -> str:
    """Create tool_call content in the format adapters use.

    Format: {"name": "...", "args": {...}, "tool_call_id": "..."}
    """
    return json.dumps(
        {
            "name": tool_name,
            "args": args,
            "tool_call_id": tool_call_id,
        }
    )


def _create_tool_result_content(
    tool_name: str,
    output: str,
    tool_call_id: str,
) -> str:
    """Create tool_result content in the format adapters use.

    Format: {"name": "...", "output": "...", "tool_call_id": "..."}
    """
    return json.dumps(
        {
            "name": tool_name,
            "output": output,
            "tool_call_id": tool_call_id,
        }
    )


def _unique_id(prefix: str = "toolu") -> str:
    """Generate a unique tool call ID for this test run."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@requires_api
class TestAnthropicConverterIntegration:
    """Integration tests for AnthropicHistoryConverter with real platform data."""

    async def test_converter_with_real_tool_history(
        self, api_client, shared_room, shared_agent1_info, shared_user_peer
    ):
        """Verify Anthropic converter handles real platform tool history."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.anthropic import AnthropicHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name

        # Use unique tool_call_id per run to avoid conflicts
        tc_id = _unique_id("toolu_real")

        assert shared_user_peer is not None, "Need at least one peer"
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        # === STEP 1: Send a user message (with mention to trigger it) ===
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} What's the weather in Tokyo?",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )
        logger.info("Sent initial message")

        # === STEP 2: Create tool_call event ===
        tool_call_content = _create_tool_call_content(
            tool_name="get_weather",
            args={"location": "Tokyo", "unit": "celsius"},
            tool_call_id=tc_id,
        )
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=tool_call_content,
                message_type="tool_call",
            ),
        )
        logger.info("Created tool_call event with id %s", tc_id)

        # === STEP 3: Create tool_result event ===
        tool_result_content = _create_tool_result_content(
            tool_name="get_weather",
            output="Tokyo is 22C and sunny",
            tool_call_id=tc_id,
        )
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=tool_result_content,
                message_type="tool_result",
            ),
        )
        logger.info("Created tool_result event")

        # === STEP 4: Fetch history from platform ===
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]
        logger.info("Fetched %d messages from platform", len(raw_history))

        # === STEP 5: Convert using Anthropic converter ===
        converter = AnthropicHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)
        logger.info("Converted to %d Anthropic messages", len(result))

        # === STEP 6: Verify conversion (find our specific tool_call_id) ===
        tool_use_blocks = [
            b
            for m in result
            if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "tool_use" and b.get("id") == tc_id
        ]
        assert len(tool_use_blocks) >= 1, f"Should find tool_use block with id {tc_id}"
        assert tool_use_blocks[0]["name"] == "get_weather"
        assert tool_use_blocks[0]["input"] == {"location": "Tokyo", "unit": "celsius"}

        tool_result_blocks = [
            b
            for m in result
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "tool_result" and b.get("tool_use_id") == tc_id
        ]
        assert len(tool_result_blocks) >= 1, (
            f"Should find tool_result block with tool_use_id {tc_id}"
        )
        assert tool_result_blocks[0]["content"] == "Tokyo is 22C and sunny"

        logger.info("SUCCESS: Anthropic converter correctly handled real platform data")

    async def test_converter_batches_parallel_tool_calls(
        self, api_client, shared_room, shared_agent1_info
    ):
        """Verify converter batches parallel tool calls from platform."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.anthropic import AnthropicHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name

        # Use unique IDs per run
        tc_id_1 = _unique_id("toolu_batch1")
        tc_id_2 = _unique_id("toolu_batch2")

        # === Create multiple tool_call events (simulating parallel tool use) ===
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "get_weather", {"location": "Tokyo"}, tc_id_1
                ),
                message_type="tool_call",
            ),
        )
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "get_weather", {"location": "London"}, tc_id_2
                ),
                message_type="tool_call",
            ),
        )

        # === Create corresponding tool_result events ===
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "get_weather", "Tokyo: 22C", tc_id_1
                ),
                message_type="tool_result",
            ),
        )
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "get_weather", "London: 15C", tc_id_2
                ),
                message_type="tool_result",
            ),
        )

        # === Fetch and convert ===
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]

        converter = AnthropicHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # === Verify batching (find the message containing our specific IDs) ===
        # Find the assistant message that contains both our tool_use blocks
        batched_message = None
        for m in result:
            if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                block_ids = {
                    b.get("id") for b in m["content"] if b.get("type") == "tool_use"
                }
                if tc_id_1 in block_ids and tc_id_2 in block_ids:
                    batched_message = m
                    break

        assert batched_message is not None, (
            f"Should batch tool_use {tc_id_1} and {tc_id_2} into single message"
        )
        our_blocks = [
            b
            for b in batched_message["content"]
            if b.get("type") == "tool_use" and b.get("id") in (tc_id_1, tc_id_2)
        ]
        assert len(our_blocks) == 2, "Should have 2 tool_use blocks in batch"

        # Verify tool_result blocks are also batched
        batched_result_message = None
        for m in result:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                result_ids = {
                    b.get("tool_use_id")
                    for b in m["content"]
                    if b.get("type") == "tool_result"
                }
                if tc_id_1 in result_ids and tc_id_2 in result_ids:
                    batched_result_message = m
                    break

        assert batched_result_message is not None, (
            "Should batch tool_result into single message"
        )

        logger.info(
            "SUCCESS: Anthropic converter correctly batches parallel tool calls"
        )


@requires_api
class TestPydanticAIConverterIntegration:
    """Integration tests for PydanticAIHistoryConverter with real platform data."""

    async def test_converter_with_real_tool_history(
        self, api_client, shared_room, shared_agent1_info, shared_user_peer
    ):
        """Verify PydanticAI converter handles real platform tool history."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        pydantic_ai_messages = pytest.importorskip("pydantic_ai.messages")
        ModelRequest = pydantic_ai_messages.ModelRequest
        ModelResponse = pydantic_ai_messages.ModelResponse

        from band.converters.pydantic_ai import PydanticAIHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name
        tc_id = _unique_id("call_pai")

        assert shared_user_peer is not None, "Need at least one peer"
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        # === Create tool events ===
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} Search for Python tutorials",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )

        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "search", {"query": "Python tutorials"}, tc_id
                ),
                message_type="tool_call",
            ),
        )

        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "search", '["tutorial1", "tutorial2"]', tc_id
                ),
                message_type="tool_result",
            ),
        )

        # === Fetch and convert ===
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]

        converter = PydanticAIHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # === Verify conversion (find our specific tool_call_id) ===
        tool_call_parts = [
            p
            for m in result
            if isinstance(m, ModelResponse)
            for p in m.parts
            if hasattr(p, "tool_call_id") and p.tool_call_id == tc_id
        ]
        assert len(tool_call_parts) >= 1, f"Should have ToolCallPart with id {tc_id}"
        assert tool_call_parts[0].tool_name == "search"
        assert tool_call_parts[0].args == {"query": "Python tutorials"}

        tool_return_parts = [
            p
            for m in result
            if isinstance(m, ModelRequest)
            for p in m.parts
            if hasattr(p, "tool_call_id")
            and hasattr(p, "content")
            and p.tool_call_id == tc_id
        ]
        assert len(tool_return_parts) >= 1, (
            f"Should have ToolReturnPart with id {tc_id}"
        )
        assert tool_return_parts[0].tool_name == "search"

        logger.info(
            "SUCCESS: PydanticAI converter correctly handled real platform data"
        )


@requires_api
class TestMixedConversationIntegration:
    """Test converters with mixed conversation patterns (text + tools)."""

    async def test_full_conversation_flow(
        self, api_client, shared_room, shared_agent1_info, shared_user_peer
    ):
        """Test converter handles realistic conversation with multiple turns."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.anthropic import AnthropicHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name

        assert shared_user_peer is not None
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        tc_id_1 = _unique_id("toolu_turn1")
        tc_id_2 = _unique_id("toolu_turn2")

        # === Simulate multi-turn conversation ===
        # Turn 1: User asks question
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} What's the weather in NYC?",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )

        # Turn 1: Agent uses tool
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "get_weather", {"location": "NYC"}, tc_id_1
                ),
                message_type="tool_call",
            ),
        )

        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "get_weather", "NYC: 18C, cloudy", tc_id_1
                ),
                message_type="tool_result",
            ),
        )

        # Turn 2: User asks follow-up
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} What about San Francisco?",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )

        # Turn 2: Agent uses tool again
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "get_weather", {"location": "San Francisco"}, tc_id_2
                ),
                message_type="tool_call",
            ),
        )

        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "get_weather", "SF: 15C, foggy", tc_id_2
                ),
                message_type="tool_result",
            ),
        )

        # === Fetch and convert ===
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]

        converter = AnthropicHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # === Verify our specific tool calls are present ===
        our_tool_use_ids = set()
        for m in result:
            if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_use" and b.get("id") in (
                        tc_id_1,
                        tc_id_2,
                    ):
                        our_tool_use_ids.add(b["id"])

        assert tc_id_1 in our_tool_use_ids, f"Should find tool_use {tc_id_1}"
        assert tc_id_2 in our_tool_use_ids, f"Should find tool_use {tc_id_2}"

        our_tool_result_ids = set()
        for m in result:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_result" and b.get("tool_use_id") in (
                        tc_id_1,
                        tc_id_2,
                    ):
                        our_tool_result_ids.add(b["tool_use_id"])

        assert tc_id_1 in our_tool_result_ids, f"Should find tool_result for {tc_id_1}"
        assert tc_id_2 in our_tool_result_ids, f"Should find tool_result for {tc_id_2}"

        logger.info(
            "SUCCESS: Converter handled multi-turn conversation with tool calls"
        )


@requires_api
class TestEdgeCasesIntegration:
    """Test converter edge cases with real platform data."""

    async def test_handles_thought_events(
        self, api_client, shared_room, shared_agent1_info
    ):
        """Verify thought events are properly skipped."""
        if shared_room is None:
            pytest.skip("shared_room not available")

        from band.converters.anthropic import AnthropicHistoryConverter

        chat_id = shared_room
        marker = uuid.uuid4().hex[:8]
        thought_content = f"Let me think about this request {marker}..."
        tc_id = _unique_id("toolu_thought")

        # Create thought event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=thought_content,
                message_type="thought",
            ),
        )

        # Create tool_call event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content("analyze", {"text": "hello"}, tc_id),
                message_type="tool_call",
            ),
        )

        # Fetch and convert
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]

        converter = AnthropicHistoryConverter()
        result = converter.convert(raw_history)

        # Verify our specific thought is not in output
        for msg in result:
            if isinstance(msg.get("content"), str):
                assert marker not in msg["content"], "Thought event should be skipped"

        # But tool_call should be present
        tool_use_blocks = [
            b
            for m in result
            if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "tool_use" and b.get("id") == tc_id
        ]
        assert len(tool_use_blocks) >= 1, f"Tool call {tc_id} should be present"

        logger.info("SUCCESS: Thought events are properly skipped")

    async def test_handles_error_events(
        self, api_client, shared_room, shared_agent1_info
    ):
        """Verify error events are properly skipped."""
        if shared_room is None:
            pytest.skip("shared_room not available")

        from band.converters.anthropic import AnthropicHistoryConverter

        chat_id = shared_room
        marker = uuid.uuid4().hex[:8]
        error_content = f"Error: API rate limit exceeded {marker}"

        # Create error event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=error_content,
                message_type="error",
            ),
        )

        # Fetch and convert
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]

        converter = AnthropicHistoryConverter()
        result = converter.convert(raw_history)

        # Verify our specific error is not in output
        for msg in result:
            if isinstance(msg.get("content"), str):
                assert marker not in msg["content"], "Error event should be skipped"

        logger.info("SUCCESS: Error events are properly skipped")


@requires_api
class TestMentionReplacementIntegration:
    """Integration tests for UUID mention replacement in message history."""

    async def test_replaces_uuid_mentions_with_handles(
        self, api_client, shared_room, shared_user_peer
    ):
        """Verify platform stores mentions as UUIDs and SDK converts back to handles."""
        if shared_room is None or shared_user_peer is None:
            pytest.skip("shared_room or shared_user_peer not available")

        from band.runtime.formatters import format_history_for_llm

        chat_id = shared_room
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name
        marker = uuid.uuid4().hex[:8]

        # Get participants to find peer's handle
        participants_response = (
            await api_client.agent_api_participants.list_agent_chat_participants(
                chat_id
            )
        )
        participants = [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "handle": getattr(p, "handle", None),
            }
            for p in participants_response.data
        ]

        peer_participant = next((p for p in participants if p["id"] == peer_id), None)
        assert peer_participant is not None, "Peer should be in participants"
        peer_handle = peer_participant.get("handle")
        assert peer_handle, "Peer must have a handle for this test"
        logger.info("Peer: %s (id: %s, handle: %s)", peer_name, peer_id, peer_handle)

        # Send message with unique marker and mention
        message_content = f"Hey {marker}, can you help me?"
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=message_content,
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )
        logger.info("Sent message: %s (with mention in array)", message_content)

        # Verify raw history contains UUID format
        context_items = await fetch_all_context(api_client, chat_id)
        raw_history = [msg.model_dump() for msg in context_items]
        logger.info("Fetched %d messages from platform", len(raw_history))

        raw_message = next(
            (m for m in raw_history if marker in m.get("content", "")),
            None,
        )
        assert raw_message is not None, (
            f"Should find the message with marker {marker} in raw history"
        )
        raw_content = raw_message["content"]
        logger.info("Raw content from platform: %s", raw_content)

        # Platform prepends @[[uuid]] to content
        assert f"@[[{peer_id}]]" in raw_content, (
            f"Raw content should contain UUID mention @[[{peer_id}]], "
            f"got: {raw_content}"
        )

        # Verify formatted history converts UUID to handle
        formatted_history = format_history_for_llm(
            raw_history, participants=participants
        )

        formatted_message = next(
            (m for m in formatted_history if marker in m["content"]),
            None,
        )
        assert formatted_message is not None, "Should find formatted message"
        formatted_content = formatted_message["content"]

        # UUID should be replaced with @handle
        assert f"@[[{peer_id}]]" not in formatted_content, (
            f"Formatted content should NOT contain UUID @[[{peer_id}]], "
            f"got: {formatted_content}"
        )
        assert f"@{peer_handle}" in formatted_content, (
            f"Formatted content should contain @{peer_handle}, got: {formatted_content}"
        )

        logger.info(
            "SUCCESS: Platform stored @[[%s]], SDK converted to @%s",
            peer_id,
            peer_handle,
        )
