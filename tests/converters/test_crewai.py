"""Tests for CrewAIHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains CrewAI-specific assistant message formatting and crew
workflow tests.
"""

from band.converters.crewai import CrewAIHistoryConverter


class TestAssistantMessages:
    """Tests for CrewAI-specific assistant message handling."""

    def test_includes_peer_agents_as_user_context(self):
        """Other agents' messages are included as user-attributed context."""
        converter = CrewAIHistoryConverter(agent_name="Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "Here's my analysis.",
                "sender_name": "Research Agent",
                "sender_type": "Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Research Agent]: Here's my analysis."
        assert result[0]["sender"] == "Research Agent"

    def test_message_type_alias_is_text_history(self):
        """Legacy message events should keep normal sender attribution."""
        converter = CrewAIHistoryConverter(agent_name="Main Agent")
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
        converter = CrewAIHistoryConverter(agent_name="Main Agent")
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


class TestCrewWorkflow:
    """Tests for CrewAI-style crew workflows."""

    def test_research_crew_conversation(self):
        """Should handle a research crew conversation flow."""
        converter = CrewAIHistoryConverter(agent_name="Research Analyst")
        raw = [
            # User request
            {
                "role": "user",
                "content": "Research AI trends for 2024",
                "sender_name": "Alice",
                "message_type": "text",
            },
            # Research Analyst responds (included for transcript rehydration)
            {
                "role": "assistant",
                "content": "Here are the key AI trends...",
                "sender_name": "Research Analyst",
                "message_type": "text",
            },
            # Writer receives and drafts
            {
                "role": "assistant",
                "content": "Based on the research, here's my draft...",
                "sender_name": "Content Writer",
                "message_type": "text",
            },
            # Editor reviews
            {
                "role": "assistant",
                "content": "Final edited version...",
                "sender_name": "Editor",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Should have: user message + own-agent message + peer-agent context.
        assert len(result) == 4

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Alice]: Research AI trends for 2024"

        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Here are the key AI trends..."
        assert result[1]["sender"] == "Research Analyst"

        assert result[2]["role"] == "user"
        assert (
            result[2]["content"]
            == "[Content Writer]: Based on the research, here's my draft..."
        )
        assert result[2]["sender"] == "Content Writer"

        assert result[3]["role"] == "user"
        assert result[3]["content"] == "[Editor]: Final edited version..."
        assert result[3]["sender"] == "Editor"

    def test_preserves_sender_type(self):
        """Should preserve sender_type in output."""
        converter = CrewAIHistoryConverter()
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
