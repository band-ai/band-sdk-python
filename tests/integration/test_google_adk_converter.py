"""Integration tests for Google ADK history converter with real platform data.

These tests verify that the GoogleADKHistoryConverter works correctly with real
session history from the platform. This catches mismatches between the expected
format in the converter and the actual format stored by the platform.

Run with: uv run pytest tests/integration/test_google_adk_converter.py -v -s --no-cov
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from thenvoi_rest import ChatEventRequest, ChatMessageRequest
from thenvoi_rest.types import ChatMessageRequestMentionsItem as Mention

from tests.integration.conftest import requires_api

logger = logging.getLogger(__name__)


def _create_tool_call_content(
    tool_name: str,
    args: dict,
    tool_call_id: str,
) -> str:
    """Create tool_call content in the format adapters use."""
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
    *,
    is_error: bool = False,
) -> str:
    """Create tool_result content in the format adapters use."""
    payload: dict = {
        "name": tool_name,
        "output": output,
        "tool_call_id": tool_call_id,
    }
    if is_error:
        payload["is_error"] = True
    return json.dumps(payload)


def _unique_id(prefix: str = "tc") -> str:
    """Generate a unique tool call ID for this test run."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@requires_api
class TestGoogleADKConverterIntegration:
    """Integration tests for GoogleADKHistoryConverter with real platform data."""

    async def test_converter_with_real_tool_history(
        self, api_client, shared_room, shared_agent1_info, shared_user_peer
    ):
        """Verify Google ADK converter handles real platform tool history.

        Flow: user message -> tool_call event -> tool_result event -> fetch -> convert
        """
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name
        tc_id = _unique_id("tc_adk_real")

        assert shared_user_peer is not None, "Need at least one peer"
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        # === STEP 1: Send a user message ===
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
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]
        logger.info("Fetched %d messages from platform", len(raw_history))

        # === STEP 5: Convert using Google ADK converter ===
        converter = GoogleADKHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)
        logger.info("Converted to %d ADK messages", len(result))

        # === STEP 6: Verify tool_call conversion ===
        # Find model message containing our function_call block
        function_call_blocks = [
            b
            for m in result
            if m.get("role") == "model" and isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict)
            and b.get("type") == "function_call"
            and b.get("id") == tc_id
        ]
        assert len(function_call_blocks) >= 1, (
            f"Should find function_call block with id {tc_id}"
        )
        assert function_call_blocks[0]["name"] == "get_weather"
        assert function_call_blocks[0]["args"] == {
            "location": "Tokyo",
            "unit": "celsius",
        }

        # === STEP 7: Verify tool_result conversion ===
        function_response_blocks = [
            b
            for m in result
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict)
            and b.get("type") == "function_response"
            and b.get("tool_call_id") == tc_id
        ]
        assert len(function_response_blocks) >= 1, (
            f"Should find function_response block with tool_call_id {tc_id}"
        )
        assert function_response_blocks[0]["name"] == "get_weather"
        assert function_response_blocks[0]["output"] == "Tokyo is 22C and sunny"

        logger.info(
            "SUCCESS: Google ADK converter correctly handled real platform data"
        )

    async def test_converter_batches_parallel_tool_calls(
        self, api_client, shared_room, shared_agent1_info
    ):
        """Verify converter batches parallel tool calls from platform."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name
        tc_id_1 = _unique_id("tc_adk_batch1")
        tc_id_2 = _unique_id("tc_adk_batch2")

        # === Create multiple tool_call events (parallel tool use) ===
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
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]

        converter = GoogleADKHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # === Verify batching: both function_calls in a single model message ===
        batched_message = None
        for m in result:
            if m.get("role") == "model" and isinstance(m.get("content"), list):
                block_ids = {
                    b.get("id")
                    for b in m["content"]
                    if isinstance(b, dict) and b.get("type") == "function_call"
                }
                if tc_id_1 in block_ids and tc_id_2 in block_ids:
                    batched_message = m
                    break

        assert batched_message is not None, (
            f"Should batch function_call {tc_id_1} and {tc_id_2} into single model message"
        )
        our_blocks = [
            b
            for b in batched_message["content"]
            if isinstance(b, dict)
            and b.get("type") == "function_call"
            and b.get("id") in (tc_id_1, tc_id_2)
        ]
        assert len(our_blocks) == 2, "Should have 2 function_call blocks in batch"

        # Verify function_response blocks are also batched
        batched_result_message = None
        for m in result:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                result_ids = {
                    b.get("tool_call_id")
                    for b in m["content"]
                    if isinstance(b, dict) and b.get("type") == "function_response"
                }
                if tc_id_1 in result_ids and tc_id_2 in result_ids:
                    batched_result_message = m
                    break

        assert batched_result_message is not None, (
            "Should batch function_response blocks into single user message"
        )

        logger.info(
            "SUCCESS: Google ADK converter correctly batches parallel tool calls"
        )


@requires_api
class TestGoogleADKConverterEdgeCases:
    """Edge case tests for Google ADK converter with real platform data."""

    async def test_skips_thought_events(self, api_client, shared_room):
        """Verify thought events are skipped in conversion."""
        if shared_room is None:
            pytest.skip("shared_room not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

        chat_id = shared_room
        marker = uuid.uuid4().hex[:8]
        thought_content = f"Let me think about this {marker}..."

        # Create thought event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=thought_content,
                message_type="thought",
            ),
        )

        # Fetch and convert
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]

        converter = GoogleADKHistoryConverter()
        result = converter.convert(raw_history)

        # Verify thought is not in output
        for msg in result:
            if isinstance(msg.get("content"), str):
                assert marker not in msg["content"], "Thought event should be skipped"

        logger.info("SUCCESS: Thought events are properly skipped")

    async def test_skips_error_events(self, api_client, shared_room):
        """Verify error events are skipped in conversion."""
        if shared_room is None:
            pytest.skip("shared_room not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

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
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]

        converter = GoogleADKHistoryConverter()
        result = converter.convert(raw_history)

        # Verify error is not in output
        for msg in result:
            if isinstance(msg.get("content"), str):
                assert marker not in msg["content"], "Error event should be skipped"

        logger.info("SUCCESS: Error events are properly skipped")

    async def test_error_tool_result_preserves_is_error_flag(
        self, api_client, shared_room, shared_agent1_info
    ):
        """Verify is_error flag is preserved through platform round-trip."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name
        tc_id = _unique_id("tc_adk_err")

        # Create tool_call event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_call_content(
                    "failing_tool", {"input": "test"}, tc_id
                ),
                message_type="tool_call",
            ),
        )

        # Create error tool_result event
        await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content=_create_tool_result_content(
                    "failing_tool",
                    "Error: tool execution failed",
                    tc_id,
                    is_error=True,
                ),
                message_type="tool_result",
            ),
        )

        # Fetch and convert
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]

        converter = GoogleADKHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # Find our function_response block
        error_blocks = [
            b
            for m in result
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict)
            and b.get("type") == "function_response"
            and b.get("tool_call_id") == tc_id
        ]
        assert len(error_blocks) >= 1, (
            f"Should find function_response block with tool_call_id {tc_id}"
        )
        assert error_blocks[0].get("is_error") is True, (
            "is_error flag should be preserved"
        )

        logger.info("SUCCESS: is_error flag preserved through platform round-trip")


@requires_api
class TestGoogleADKMultiTurnConversation:
    """Test converter with multi-turn conversation patterns."""

    async def test_full_conversation_flow(
        self, api_client, shared_room, shared_agent1_info, shared_user_peer
    ):
        """Test converter handles realistic multi-turn conversation."""
        if shared_room is None or shared_agent1_info is None:
            pytest.skip("shared_room or shared_agent1_info not available")

        from band.converters.google_adk import GoogleADKHistoryConverter

        chat_id = shared_room
        agent_name = shared_agent1_info.name

        assert shared_user_peer is not None
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        tc_id_1 = _unique_id("tc_adk_turn1")
        tc_id_2 = _unique_id("tc_adk_turn2")

        # === Turn 1: User asks, agent uses tool ===
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} What's the weather in NYC?",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )

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

        # === Turn 2: User follow-up, agent uses tool again ===
        await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} What about San Francisco?",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )

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
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id
        )
        raw_history = [msg.model_dump() for msg in context_response.data]

        converter = GoogleADKHistoryConverter(agent_name=agent_name)
        result = converter.convert(raw_history)

        # === Verify both tool calls are present ===
        our_function_call_ids = set()
        for m in result:
            if m.get("role") == "model" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "function_call"
                        and b.get("id") in (tc_id_1, tc_id_2)
                    ):
                        our_function_call_ids.add(b["id"])

        assert tc_id_1 in our_function_call_ids, f"Should find function_call {tc_id_1}"
        assert tc_id_2 in our_function_call_ids, f"Should find function_call {tc_id_2}"

        our_function_response_ids = set()
        for m in result:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "function_response"
                        and b.get("tool_call_id") in (tc_id_1, tc_id_2)
                    ):
                        our_function_response_ids.add(b["tool_call_id"])

        assert tc_id_1 in our_function_response_ids, (
            f"Should find function_response for {tc_id_1}"
        )
        assert tc_id_2 in our_function_response_ids, (
            f"Should find function_response for {tc_id_2}"
        )

        # === Verify role alternation (ADK requires user/model alternation) ===
        # All text messages should be "user" role, all tool calls should be "model"
        for m in result:
            if isinstance(m.get("content"), str):
                assert m["role"] == "user", (
                    f"Text messages should have role 'user', got '{m['role']}'"
                )
            elif isinstance(m.get("content"), list):
                block_types = {
                    b.get("type") for b in m["content"] if isinstance(b, dict)
                }
                if "function_call" in block_types:
                    assert m["role"] == "model", (
                        "function_call blocks should be in model messages"
                    )
                elif "function_response" in block_types:
                    assert m["role"] == "user", (
                        "function_response blocks should be in user messages"
                    )

        logger.info(
            "SUCCESS: Google ADK converter handled multi-turn conversation correctly"
        )
