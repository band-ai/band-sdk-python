"""Tests for ParlantHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains Parlant-specific assistant message formatting and
guideline-based conversation tests.
"""

from band.converters.parlant import ParlantHistoryConverter


class TestAssistantMessages:
    """Tests for Parlant-specific assistant message handling."""

    def test_includes_peer_agents_as_user_context(self):
        """Other agents' messages are included as user-attributed context."""
        converter = ParlantHistoryConverter(agent_name="Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "Here's my analysis.",
                "sender_name": "Helper Agent",
                "sender_type": "Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Helper Agent]: Here's my analysis."
        assert result[0]["sender"] == "Helper Agent"

    def test_message_type_alias_is_text_history(self):
        """Legacy message events should keep normal sender attribution."""
        converter = ParlantHistoryConverter(agent_name="Main Agent")
        raw = [
            {
                "role": "user",
                "content": "Legacy hello",
                "sender_name": "Alice",
                "sender_type": "User",
                "message_type": "message",
            }
        ]

        result = converter.convert(raw)

        assert result == [
            {
                "role": "user",
                "content": "[Alice]: Legacy hello",
                "sender": "Alice",
                "sender_type": "User",
            }
        ]

    def test_skips_whitespace_only_text_history(self):
        """Blank persisted text rows should not become replay context."""
        converter = ParlantHistoryConverter(agent_name="Main Agent")
        raw = [
            {
                "role": "user",
                "content": "   ",
                "sender_name": "Noisy Sender",
                "sender_type": "User",
                "message_type": "text",
            }
        ]

        assert converter.convert(raw) == []


class TestGuidelineBasedConversation:
    """Tests for Parlant-style guideline-based conversations."""

    def test_support_conversation_flow(self):
        """Should handle a customer support conversation flow."""
        converter = ParlantHistoryConverter(agent_name="Support Agent")
        raw = [
            # Customer question
            {
                "role": "user",
                "content": "I want a refund for my order",
                "sender_name": "Customer",
                "message_type": "text",
            },
            # Support agent responds (included for Parlant)
            {
                "role": "assistant",
                "content": "Let me check your order status...",
                "sender_name": "Support Agent",
                "message_type": "text",
            },
            # Completed tool events are replayed as inert history
            {
                "role": "assistant",
                "content": '{"tool": "check_order_status"}',
                "message_type": "tool_call",
            },
            # Tool result event
            {
                "role": "assistant",
                "content": '{"status": "delivered"}',
                "message_type": "tool_result",
            },
            # Customer follow-up
            {
                "role": "user",
                "content": "The product was damaged",
                "sender_name": "Customer",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 5

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Customer]: I want a refund for my order"

        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Let me check your order status..."

        assert result[2]["role"] == "assistant"
        assert result[2]["content"] == '[Tool Call]: {"tool": "check_order_status"}'

        assert result[3]["role"] == "user"
        assert result[3]["content"] == '[Tool Result]: {"status": "delivered"}'

        assert result[4]["role"] == "user"
        assert result[4]["content"] == "[Customer]: The product was damaged"

    def test_preserves_sender_type(self):
        """Should preserve sender_type in output."""
        converter = ParlantHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "sender_type": "User",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Hi there",
                "sender_name": "Helper Agent",
                "sender_type": "Agent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert result[0]["sender_type"] == "User"
        assert result[1]["sender_type"] == "Agent"

    def test_multi_agent_collaboration(self):
        """Should handle multi-agent collaboration in Parlant-style."""
        converter = ParlantHistoryConverter(agent_name="Coordinator")
        raw = [
            # User request
            {
                "role": "user",
                "content": "Analyze this document",
                "sender_name": "User",
                "message_type": "text",
            },
            # Coordinator delegates (included for Parlant)
            {
                "role": "assistant",
                "content": "I'll have the analyzer look at this.",
                "sender_name": "Coordinator",
                "message_type": "text",
            },
            # Analyzer responds
            {
                "role": "assistant",
                "content": "Analysis complete: ...",
                "sender_name": "Analyzer Agent",
                "message_type": "text",
            },
            # Coordinator summarizes (included for Parlant)
            {
                "role": "assistant",
                "content": "Here's the analysis summary.",
                "sender_name": "Coordinator",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # All text messages included (Parlant needs full history)
        assert len(result) == 4

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[User]: Analyze this document"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "I'll have the analyzer look at this."
        assert result[2]["role"] == "user"
        assert result[2]["content"] == "[Analyzer Agent]: Analysis complete: ..."
        assert result[3]["role"] == "assistant"
        assert result[3]["content"] == "Here's the analysis summary."
