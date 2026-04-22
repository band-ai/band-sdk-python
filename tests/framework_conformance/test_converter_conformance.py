"""Parameterized conformance tests for all history converters.

These tests verify the shared behavioral contract across all six framework
converters. Framework-specific behavior (tool batching, message object types,
etc.) remains in the per-framework test files under tests/converters/.
"""

from __future__ import annotations

import pytest

# Shared tool-event fixture payloads.  These include both top-level and nested
# ``data.*`` paths so all current frameworks find the fields they need.  If you
# add a converter with a different tool-event schema, verify the payloads in
# tests/framework_configs/fixtures.py still cover it (or extend them).
from tests.framework_configs.fixtures import (
    TOOL_CALL_LOOKUP,
    TOOL_CALL_SEARCH,
    TOOL_CALL_SEARCH_EMPTY,
    TOOL_RESULT_LOOKUP,
    TOOL_RESULT_SEARCH,
    TOOL_RESULT_SEARCH_FOUND,
)
from tests.framework_configs.converters import SenderBehavior

# Expected content for each SenderBehavior when the raw content is "Hello!".
# Coupled to the test payloads below — tests that use _assert_sender_content()
# MUST pass raw messages with content="Hello!" for the assertion to hold.
_SENDER_BEHAVIOR_EXPECTED: dict[SenderBehavior, str] = {
    SenderBehavior.CONTENT_AS_IS: "Hello!",
    SenderBehavior.BRACKETS_EMPTY: "[]: Hello!",
    SenderBehavior.UNKNOWN_PREFIX: "[Unknown]: Hello!",
}


def _assert_sender_content(content: str, behavior: SenderBehavior) -> None:
    """Assert *content* matches the expected value for *behavior*."""
    expected = _SENDER_BEHAVIOR_EXPECTED[behavior]
    assert content == expected, (
        f"{behavior.name}: expected {expected!r}, got {content!r}"
    )


class TestUserTextMessages:
    """All converters must handle user text messages consistently."""

    def test_converts_user_text_with_sender_name(
        self, converter_config, make_converter, output
    ):
        """User text messages include sender name prefix in output."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello, agent!",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1
        assert output.content_contains(result, "[Alice]: Hello, agent!")

    def test_handles_empty_sender_name(self, converter_config, make_converter, output):
        """User messages with empty sender_name are handled per framework."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1
        _assert_sender_content(
            output.get_content(result, 0), converter_config.empty_sender_behavior
        )

    def test_handles_missing_sender_name(
        self, converter_config, make_converter, output
    ):
        """User messages with no sender_name key are handled per framework."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1
        _assert_sender_content(
            output.get_content(result, 0), converter_config.missing_sender_behavior
        )


class TestEmptyHistory:
    """All converters must handle empty input."""

    def test_empty_history(self, converter_config, make_converter):
        """Empty history returns the framework's empty result."""
        converter = make_converter()

        result = converter.convert([])

        assert result == converter_config.empty_result


class TestMessageTypeDefaults:
    """All converters default to text message type."""

    def test_defaults_to_text_message_type(
        self, converter_config, make_converter, output
    ):
        """Messages without message_type are treated as text."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Bob",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) >= 1
        assert output.content_contains(result, "Bob")


class TestThoughtMessageSkipping:
    """All converters must skip thought messages."""

    def test_skips_thought_messages(self, converter_config, make_converter, output):
        """Thought messages are always filtered out."""
        converter = make_converter()
        raw = [
            {
                "role": "assistant",
                "content": "I'm thinking about this...",
                "message_type": "thought",
            }
        ]

        result = converter.convert(raw)

        assert output.is_empty(result)


class TestOwnMessageFiltering:
    """Converters filter own messages (except Parlant which includes all)."""

    def test_own_message_handling(self, converter_config, make_converter, output):
        """Own agent text messages: filtered for most, included for Parlant."""
        converter = make_converter(agent_name="Agent")
        raw = [
            {
                "role": "assistant",
                "content": "I'll help you with that.",
                "sender_name": "Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        if converter_config.filters_own_messages:
            assert output.is_empty(result)
        else:
            # Parlant includes own messages
            assert output.result_length(result) == 1

    def test_includes_other_agents_messages(
        self, converter_config, make_converter, output
    ):
        """Other agents' messages are always included."""
        converter = make_converter(agent_name="Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "It's sunny today!",
                "sender_name": "Weather Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1

    def test_skips_only_own_keeps_others(
        self, converter_config, make_converter, output
    ):
        """Only own agent messages are filtered; others are kept."""
        converter = make_converter()
        converter.set_agent_name("Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "I'll help",
                "sender_name": "Main Agent",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "It's sunny!",
                "sender_name": "Weather Agent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        if converter_config.filters_own_messages:
            assert output.result_length(result) == 1  # Only Weather Agent
        else:
            assert output.result_length(result) == 2  # Both kept (Parlant)

    def test_set_agent_name_updates_filtering(
        self, converter_config, make_converter, output
    ):
        """set_agent_name dynamically changes which messages are filtered."""
        converter = make_converter()
        raw = [
            {
                "role": "assistant",
                "content": "Hello",
                "sender_name": "Agent",
                "message_type": "text",
            }
        ]

        # Before setting name - all assistant messages included
        assert output.result_length(converter.convert(raw)) == 1

        # After setting name
        converter.set_agent_name("Agent")
        result_after = converter.convert(raw)

        if converter_config.filters_own_messages:
            assert output.is_empty(result_after)
        else:
            # Parlant still includes own messages
            assert output.result_length(result_after) == 1

    def test_includes_all_when_no_agent_name(
        self, converter_config, make_converter, output
    ):
        """When no agent name is set, all assistant messages are included."""
        converter = make_converter()
        raw = [
            {
                "role": "assistant",
                "content": "Hello from agent 1",
                "sender_name": "Agent 1",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Hello from agent 2",
                "sender_name": "Agent 2",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 2


class TestEdgeCases:
    """Edge cases with config-driven behavior."""

    def test_handles_empty_content(self, converter_config, make_converter, output):
        """Messages with empty content: some converters skip, others keep."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        if converter_config.skips_empty_content:
            assert output.is_empty(result)
        else:
            assert output.result_length(result) == 1
            # Verify the sender prefix is preserved even with empty content.
            assert output.content_contains(result, "Alice")

    def test_defaults_to_user_role(self, converter_config, make_converter, output):
        """Messages without role default to 'user'."""
        if not converter_config.has_role_concept:
            pytest.skip(
                f"{converter_config.display_name} returns string (no role concept)"
            )

        converter = make_converter()
        raw = [
            {
                "content": "Hello",
                "sender_name": "Bob",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.get_role(result, 0) == "user"


class TestOutputShape:
    """Verify framework-specific output types and structure.

    These tests catch regressions where a converter returns the wrong Python
    type (e.g. dict instead of HumanMessage for LangChain) or drops
    framework-specific metadata fields (e.g. sender/sender_type for CrewAI).
    """

    def test_user_text_produces_correct_type(
        self, converter_config, make_converter, output
    ):
        """User text message produces the correct framework-specific type."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1
        output.assert_element_type(result, 0, "user")

    def test_result_container_type(self, converter_config, make_converter, output):
        """Converter result is the expected container type (list or str)."""
        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        output.assert_result_type(result)

    def test_other_agent_text_produces_correct_type(
        self, converter_config, make_converter, output
    ):
        """Other agent's text message produces the correct framework-specific type."""
        converter = make_converter(agent_name="Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "It's sunny today!",
                "sender_name": "Weather Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert output.result_length(result) == 1
        output.assert_element_type(result, 0, converter_config.other_agent_output_role)

    def test_user_text_has_sender_metadata(
        self, converter_config, make_converter, output
    ):
        """User text message includes sender/sender_type (CrewAI, Parlant)."""
        if not converter_config.has_sender_metadata:
            pytest.skip(
                f"{converter_config.display_name} does not include sender metadata"
            )

        converter = make_converter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        output.assert_sender_metadata(result, 0, "Alice", "User")

    def test_other_agent_has_sender_metadata(
        self, converter_config, make_converter, output
    ):
        """Other agent's message includes sender name (CrewAI, Parlant)."""
        if not converter_config.has_sender_metadata:
            pytest.skip(
                f"{converter_config.display_name} does not include sender metadata"
            )

        converter = make_converter(agent_name="Main Agent")
        raw = [
            {
                "role": "assistant",
                "content": "Here's what I found.",
                "sender_name": "Research Agent",
                "sender_type": "Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        output.assert_sender_metadata(result, 0, "Research Agent", "Agent")


class TestToolEventHandling:
    """Converters that skip tool events entirely."""

    def test_tool_events_skipped_for_simple_converters(
        self, converter_config, make_converter, output
    ):
        """Converters marked as text-only skip tool_call/tool_result messages entirely."""
        if not converter_config.skips_tool_events:
            pytest.skip(
                f"{converter_config.display_name} processes tool events (tested below)"
            )

        converter = make_converter()
        raw = [
            {
                "role": "assistant",
                "content": '{"event": "on_tool_start", "name": "search"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"event": "on_tool_end", "output": "result"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert output.is_empty(result)


class TestToolEventConversion:
    """Converters that process tool events must convert them to framework format."""

    def test_converts_tool_call_to_framework_format(
        self, converter_config, make_converter, output
    ):
        """Tool_call is converted and the tool name appears in output."""
        if converter_config.skips_tool_events:
            pytest.skip(f"{converter_config.display_name} skips tool events")

        converter = make_converter()
        raw = [TOOL_CALL_SEARCH, TOOL_RESULT_SEARCH]

        result = converter.convert(raw)

        assert not output.is_empty(result)
        # Tool name must appear somewhere in the converted output
        assert output.content_contains(result, "search")
        # At least one output entry (some frameworks batch call+result into one message)
        assert output.result_length(result) >= 1

    def test_converts_tool_result_paired_with_call(
        self, converter_config, make_converter, output
    ):
        """Tool_result output content is present and paired with its tool_call_id."""
        if converter_config.skips_tool_events:
            pytest.skip(f"{converter_config.display_name} skips tool events")

        converter = make_converter()
        raw = [TOOL_CALL_LOOKUP, TOOL_RESULT_LOOKUP]

        result = converter.convert(raw)

        assert output.result_length(result) >= 1
        assert output.content_contains(result, "found item 42")
        assert output.content_contains(result, "lookup")

    def test_mixed_history_includes_user_assistant_tool_messages(
        self, converter_config, make_converter, output
    ):
        """User + other-agent assistant + tool_call + tool_result converted in order."""
        if converter_config.skips_tool_events:
            pytest.skip(f"{converter_config.display_name} skips tool events")

        # Use agent_name so own-message filtering is active, and attribute
        # the assistant text to a *different* agent so it is always included.
        converter = make_converter(agent_name="MyBot")
        raw = [
            {
                "role": "user",
                "content": "Run search",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Searching...",
                "sender_name": "HelperBot",
                "message_type": "text",
            },
            TOOL_CALL_SEARCH_EMPTY,
            TOOL_RESULT_SEARCH_FOUND,
        ]

        result = converter.convert(raw)

        length = output.result_length(result)
        assert length >= 2
        assert output.content_contains(result, "Alice")
        assert output.content_contains(result, "search")
        assert output.content_contains(result, "found")
        # Verify ordering: user message must appear before tool result.
        # "found" only appears in TOOL_RESULT_SEARCH_FOUND so it won't
        # false-match the user text.
        assert "[Alice]" in output.get_content(result, 0)
        found_indices = [
            i for i in range(length) if "found" in output.get_content(result, i)
        ]
        assert found_indices, (
            "Ordering check failed: no message contained 'found'; "
            "expected tool result content in converter output."
        )
        found_idx = found_indices[0]
        assert 0 < found_idx, (
            f"User message (index 0) must precede tool result (index {found_idx})"
        )
