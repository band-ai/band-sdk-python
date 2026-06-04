"""Tests for GoogleADKHistoryConverter.

Tests for shared converter behavior (empty input, own-message filtering,
sender formatting, tool event handling) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains Google ADK-specific conversion behavior.
"""

from __future__ import annotations

import json

from band.converters.google_adk import GoogleADKHistoryConverter


class TestBasicConversion:
    """Tests for basic message conversion."""

    def test_empty_history(self):
        """Should return empty list for empty input."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert([])
        assert result == []

    def test_single_user_message(self):
        """Should convert user text message."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Hello",
                    "sender_name": "Alice",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Alice]: Hello"

    def test_multiple_user_messages(self):
        """Should convert multiple messages."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Hello",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
                {
                    "role": "user",
                    "content": "Hi there",
                    "sender_name": "Bob",
                    "message_type": "text",
                },
            ]
        )
        assert len(result) == 2
        assert "[Alice]: Hello" in result[0]["content"]
        assert "[Bob]: Hi there" in result[1]["content"]


class TestOwnMessageHandling:
    """Tests for handling this agent's own messages.

    INT-509: own-agent text is kept (as ``role="model"``) so that on
    rehydration the LLM sees its prior replies and does not re-answer
    messages it already handled.
    """

    def test_own_messages_kept_as_model_role(self):
        """Should preserve this agent's text as model role with bare content."""
        converter = GoogleADKHistoryConverter(agent_name="TestBot")
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "I'm the bot response",
                    "sender_name": "TestBot",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "model"
        # No [TestBot]: prefix on own-agent content; speaker is implicit in role.
        assert result[0]["content"] == "I'm the bot response"

    def test_includes_other_agent_messages(self):
        """Should include messages from other agents as user role."""
        converter = GoogleADKHistoryConverter(agent_name="TestBot")
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "I'm another bot",
                    "sender_name": "OtherBot",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[OtherBot]" in result[0]["content"]

    def test_set_agent_name_marks_subsequent_replies_as_model(self):
        """set_agent_name should switch which sender is treated as 'own'."""
        converter = GoogleADKHistoryConverter()
        converter.set_agent_name("TestBot")

        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "My response",
                    "sender_name": "TestBot",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "model"
        assert result[0]["content"] == "My response"

    def test_empty_agent_name_does_not_claim_nameless_assistant_rows(self):
        """A converter built with the default ``agent_name=""`` must NOT
        treat nameless assistant rows as its own.  Without this guard the
        comparison ``sender_name == self._agent_name`` reduces to
        ``"" == ""`` and every peer message without a sender_name would
        be misattributed to this agent (emitted as ``role="model"`` with
        no prefix), swapping the INT-509 bug for the opposite one.
        """
        converter = GoogleADKHistoryConverter()  # agent_name defaults to ""
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "anonymous peer reply",
                    "sender_name": "",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        # Nameless assistant row must be a peer (role="user"), not own.
        assert result[0]["role"] == "user"
        # No name → no [name]: prefix; bare content carrying speaker via role.
        assert result[0]["content"] == "anonymous peer reply"


class TestToolEvents:
    """Tests for tool call and tool result conversion."""

    def test_tool_call_conversion(self):
        """Should convert tool_call to model message with function_call block."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "band_send_message",
                            "args": {"content": "Hello"},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                }
            ]
        )
        # Orphan patching adds a synthetic response for the unmatched call
        assert len(result) == 2
        assert result[0]["role"] == "model"
        blocks = result[0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "function_call"
        assert blocks[0]["name"] == "band_send_message"
        assert blocks[0]["args"] == {"content": "Hello"}
        # Synthetic error response
        assert result[1]["role"] == "user"

    def test_tool_result_conversion(self):
        """Should convert tool_result to user message with function_response block."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "band_send_message",
                            "output": '{"status": "sent"}',
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "user"
        blocks = result[0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "function_response"
        assert blocks[0]["name"] == "band_send_message"

    def test_tool_call_and_result_pair(self):
        """Should batch tool call and result into separate messages."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "test"},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "output": "result data",
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                },
            ]
        )
        assert len(result) == 2
        assert result[0]["role"] == "model"
        assert result[1]["role"] == "user"

    def test_batches_multiple_tool_calls(self):
        """Should batch consecutive tool calls into single model message."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "tool_a",
                            "args": {},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "tool_b",
                            "args": {},
                            "tool_call_id": "tc-2",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
            ]
        )
        # Orphan patching adds a synthetic response for unmatched calls
        assert len(result) == 2
        assert result[0]["role"] == "model"
        assert len(result[0]["content"]) == 2
        # Synthetic error responses for both orphaned calls
        assert result[1]["role"] == "user"

    def test_error_tool_result(self):
        """Should include is_error flag on error results."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "failing_tool",
                            "output": "Error: tool failed",
                            "tool_call_id": "tc-1",
                            "is_error": True,
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                }
            ]
        )
        assert len(result) == 1
        blocks = result[0]["content"]
        assert blocks[0]["is_error"] is True


class TestEdgeCases:
    """Tests for edge cases."""

    def test_missing_sender_name(self):
        """Should handle missing sender_name."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Hello",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_empty_sender_name(self):
        """Should handle empty sender_name."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Hello",
                    "sender_name": "",
                    "message_type": "text",
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_skips_thought_messages(self):
        """Should skip thought message types."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "Thinking about the problem...",
                    "sender_name": "TestBot",
                    "message_type": "thought",
                }
            ]
        )
        assert len(result) == 0

    def test_skips_error_messages(self):
        """Should skip error message types."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "Error: something went wrong",
                    "sender_name": "TestBot",
                    "message_type": "error",
                }
            ]
        )
        assert len(result) == 0

    def test_malformed_tool_call_json(self):
        """Should skip malformed tool call JSON."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "not json",
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                }
            ]
        )
        assert len(result) == 0

    def test_malformed_tool_result_json(self):
        """Should skip malformed tool result JSON."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "not json at all",
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                }
            ]
        )
        assert len(result) == 0

    def test_batches_multiple_tool_results(self):
        """Should batch consecutive tool results into single user message."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "tool_a",
                            "output": "result_a",
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "tool_b",
                            "output": "result_b",
                            "tool_call_id": "tc-2",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                },
            ]
        )
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) == 2

    def test_trailing_pending_tool_calls(self):
        """Should flush trailing tool calls at end of history."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "test"},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
            ]
        )
        # Model message from pending flush + synthetic response from orphan patch
        assert len(result) == 2
        assert result[0]["role"] == "model"
        assert result[0]["content"][0]["type"] == "function_call"
        assert result[1]["role"] == "user"

    def test_mixed_messages_and_tools(self):
        """Should handle interleaved text messages and tool events."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Search for cats",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "cats"},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "output": "Found: cats are great",
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                },
                {
                    "role": "user",
                    "content": "Thanks!",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
            ]
        )
        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "model"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "user"


class TestOrphanedToolCallPatching:
    """Tests for orphaned function_call patching."""

    def test_patches_orphaned_tool_call_no_result(self):
        """Should inject synthetic error response for tool call without result."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "test"},
                            "tool_call_id": "tc-orphan",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
                {
                    "role": "user",
                    "content": "Follow up",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
            ]
        )
        # Should have: model (tool call), user (synthetic result + text), user (follow up)
        # The orphan patch inserts a synthetic response into or before the next user msg
        assert len(result) >= 2
        assert result[0]["role"] == "model"
        # The second message should contain the synthetic function_response
        user_msg = result[1]
        assert user_msg["role"] == "user"
        content = user_msg["content"]
        assert isinstance(content, list)
        has_synthetic = any(
            isinstance(b, dict)
            and b.get("type") == "function_response"
            and "interrupted" in b.get("output", "")
            for b in content
        )
        assert has_synthetic

    def test_no_patch_when_result_present(self):
        """Should not patch when tool call has matching result."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "test"},
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "output": "found it",
                            "tool_call_id": "tc-1",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_result",
                },
            ]
        )
        assert len(result) == 2
        # No synthetic responses should be added
        user_content = result[1]["content"]
        assert isinstance(user_content, list)
        for block in user_content:
            if isinstance(block, dict) and block.get("type") == "function_response":
                assert "interrupted" not in block.get("output", "")

    def test_patches_orphan_at_end_of_history(self):
        """Should patch orphaned tool call at end of history with no following message."""
        converter = GoogleADKHistoryConverter()
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "name": "search",
                            "args": {"q": "test"},
                            "tool_call_id": "tc-end",
                        }
                    ),
                    "sender_name": "TestBot",
                    "message_type": "tool_call",
                },
            ]
        )
        # Should have model message + injected user message with synthetic response
        assert len(result) == 2
        assert result[0]["role"] == "model"
        assert result[1]["role"] == "user"
        content = result[1]["content"]
        assert isinstance(content, list)
        assert any(
            b.get("type") == "function_response"
            and "interrupted" in b.get("output", "")
            for b in content
            if isinstance(b, dict)
        )


class TestRehydrationRegressionINT509:
    """Regression: ADK adapter sent duplicate replies after kill/restart.

    Before the fix the converter dropped own-agent text rows
    ("redundant with tool results"). Pre-restart history was rehydrated as
    a series of unanswered user turns, so the LLM re-answered them. The
    fix maps every text row through, keeping own-agent replies as
    ``role="model"`` so the rehydrated transcript reflects the real
    conversation.
    """

    def test_rehydrated_history_preserves_alternating_turns(self):
        """A user/agent ping-pong should round-trip through the converter
        with the agent's replies intact."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        result = converter.convert(
            [
                {
                    "role": "user",
                    "content": "Hello",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
                {
                    "role": "assistant",
                    "content": "Hello! I'm here to help.",
                    "sender_name": "ResearchBot",
                    "message_type": "text",
                },
                {
                    "role": "user",
                    "content": "What's the capital of France?",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
                {
                    "role": "assistant",
                    "content": "Paris.",
                    "sender_name": "ResearchBot",
                    "message_type": "text",
                },
                {
                    "role": "user",
                    "content": "Remember pineapple",
                    "sender_name": "Alice",
                    "message_type": "text",
                },
                {
                    "role": "assistant",
                    "content": "I'll remember 'pineapple'.",
                    "sender_name": "ResearchBot",
                    "message_type": "text",
                },
            ]
        )

        # Every turn survives the round-trip.
        assert len(result) == 6
        roles = [m["role"] for m in result]
        assert roles == ["user", "model", "user", "model", "user", "model"]

        # Peer messages carry the [name]: prefix; own messages do not.
        assert result[0]["content"] == "[Alice]: Hello"
        assert result[1]["content"] == "Hello! I'm here to help."
        assert result[3]["content"] == "Paris."
        assert result[5]["content"] == "I'll remember 'pineapple'."

    def test_peer_agent_replies_stay_distinct_from_own_replies(self):
        """Cross-room isolation requires that peer-agent text is never
        labeled as this agent's own reply, even when the senders share a
        common prefix."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "From me",
                    "sender_name": "ResearchBot",
                    "message_type": "text",
                },
                {
                    "role": "assistant",
                    "content": "From a peer",
                    "sender_name": "ResearchBotJr",
                    "message_type": "text",
                },
            ]
        )

        assert len(result) == 2
        assert result[0]["role"] == "model"
        assert result[0]["content"] == "From me"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "[ResearchBotJr]: From a peer"


class TestFormatTranscript:
    """Tests for ``format_transcript`` — the converter renders its own
    history shape into the text transcript the ADK adapter rehydrates.

    Owning rendering on the converter (instead of the adapter) gives the
    own-agent label a single source of truth: the same ``_agent_name`` that
    drives own-vs-peer attribution during ``convert()`` also drives the
    speaker label in the transcript.  Without that, the label and the
    attribution could drift apart and reintroduce INT-509.
    """

    def test_empty_history(self):
        """Empty history renders as the empty string."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        assert converter.format_transcript([]) == ""

    def test_labels_own_turns_with_agent_name(self):
        """Own ``role='model'`` text turns carry the agent's name as label."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        transcript = converter.format_transcript(
            [
                {"role": "user", "content": "[Alice]: Hello"},
                {"role": "model", "content": "Hi back"},
            ]
        )
        assert transcript == "[Alice]: Hello\n[ResearchBot]: Hi back"

    def test_own_turns_unlabeled_without_agent_name(self):
        """Without a configured agent name, own turns render as bare
        content — peer prefixes are already on user lines from ``convert()``.

        In production ``SimpleAdapter.on_started`` always calls
        ``set_agent_name`` before any message arrives, so this branch is
        the rare bootstrap-only case.
        """
        converter = GoogleADKHistoryConverter()
        transcript = converter.format_transcript(
            [
                {"role": "user", "content": "[Alice]: Hello"},
                {"role": "model", "content": "Hi back"},
            ]
        )
        assert transcript == "[Alice]: Hello\nHi back"

    def test_set_agent_name_updates_label(self):
        """``set_agent_name`` is the runtime hook used by ``SimpleAdapter.on_started``
        and must change the transcript label going forward."""
        converter = GoogleADKHistoryConverter()
        converter.set_agent_name("ResearchBot")
        transcript = converter.format_transcript([{"role": "model", "content": "ok"}])
        assert transcript == "[ResearchBot]: ok"

    def test_renders_tool_call_block(self):
        """Function-call blocks render as a compact ``[Tool Call]`` preview
        with the tool name and JSON-serialized arguments."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        transcript = converter.format_transcript(
            [
                {
                    "role": "model",
                    "content": [
                        {
                            "type": "function_call",
                            "id": "call_1",
                            "name": "band_send_message",
                            "args": {"content": "Hello"},
                        }
                    ],
                }
            ]
        )
        assert "[Tool Call] band_send_message" in transcript
        assert '"content": "Hello"' in transcript

    def test_renders_tool_result_block(self):
        """Function-response blocks render as a compact ``[Tool Result]`` preview."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        transcript = converter.format_transcript(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "function_response",
                            "tool_call_id": "call_1",
                            "name": "band_send_message",
                            "output": '{"status": "sent"}',
                        }
                    ],
                }
            ]
        )
        assert "[Tool Result] band_send_message" in transcript
        assert '{"status": "sent"}' in transcript

    def test_truncates_long_tool_result_output(self):
        """A single noisy tool result must not be allowed to dominate the
        rehydrated context window — long outputs are truncated to a fixed
        preview length with an ellipsis."""
        converter = GoogleADKHistoryConverter(agent_name="ResearchBot")
        long_output = "x" * 1000
        transcript = converter.format_transcript(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "function_response",
                            "tool_call_id": "call_1",
                            "name": "noisy_tool",
                            "output": long_output,
                        }
                    ],
                }
            ]
        )
        assert "..." in transcript
        assert len(transcript) < 500
