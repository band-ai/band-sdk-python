"""Tests for CrewAIHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains CrewAI-specific assistant message formatting, tool event
replay, and crew workflow tests.
"""

from thenvoi.converters.crewai import CrewAIHistoryConverter


class TestAssistantMessages:
    """Tests for CrewAI-specific assistant message handling."""

    def test_includes_other_agents_as_assistant(self):
        """Other agents' messages are included with role assistant."""
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
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Here's my analysis."
        assert result[0]["sender"] == "Research Agent"


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
            # Research Analyst responds (skipped - own message)
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

        # Should have: user message + 2 other agents' messages
        # (Research Analyst's own message is skipped)
        assert len(result) == 3

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Alice]: Research AI trends for 2024"

        assert result[1]["role"] == "assistant"
        assert result[1]["sender"] == "Content Writer"

        assert result[2]["role"] == "assistant"
        assert result[2]["sender"] == "Editor"

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


class TestToolEventReplay:
    """Tests for CrewAI tool event replay formatting."""

    def test_converts_tool_call_to_assistant_replay_message(self):
        converter = CrewAIHistoryConverter(agent_name="Router")
        raw = [
            {
                "role": "assistant",
                "content": '{"tool": "thenvoi_lookup_peers", "input": {"page": 1}}',
                "sender_name": "Router",
                "sender_type": "Agent",
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == '[Tool Call] thenvoi_lookup_peers {"page": 1}'
        assert result[0]["sender"] == "Router"
        assert result[0]["sender_type"] == "Agent"

    def test_converts_tool_result_to_user_replay_message(self):
        converter = CrewAIHistoryConverter(agent_name="Router")
        raw = [
            {
                "role": "assistant",
                "content": '{"tool": "thenvoi_lookup_peers", "result": {"peers": ["a"]}}',
                "sender_name": "Router",
                "sender_type": "Agent",
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert (
            result[0]["content"]
            == '[Router]: [Tool Result] thenvoi_lookup_peers: {"peers": ["a"]}'
        )
        assert result[0]["sender"] == "Router"
        assert result[0]["sender_type"] == "Agent"

    def test_converts_tool_error_to_user_replay_message(self):
        converter = CrewAIHistoryConverter(agent_name="Router")
        raw = [
            {
                "role": "assistant",
                "content": '{"tool": "thenvoi_add_participant", "error": "No such peer"}',
                "sender_name": "Router",
                "sender_type": "Agent",
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert (
            result[0]["content"]
            == "[Router]: [Tool Error] thenvoi_add_participant: No such peer"
        )

    def test_skips_malformed_tool_events(self):
        converter = CrewAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "not json",
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"tool": "thenvoi_lookup_peers"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result == []
