"""Google ADK history converter."""

from __future__ import annotations

import json
import logging
from typing import Any

from band.core.protocols import HistoryConverter

from ._tool_parsing import parse_tool_call, parse_tool_result

logger = logging.getLogger(__name__)

# Type alias for Google ADK messages (dict-based, converted to Content by adapter)
GoogleADKMessages = list[dict[str, Any]]

# Truncate long tool-result strings in transcript previews so a single
# noisy tool result cannot dominate the rehydrated context window.
_MAX_TOOL_OUTPUT_PREVIEW = 200


def _patch_orphaned_tool_calls(messages: GoogleADKMessages) -> None:
    """Inject synthetic function_response blocks for orphaned function_call blocks.

    Gemini expects every ``function_call`` in a model message to have a
    corresponding ``function_response`` in the next user message.  When
    history is corrupted (e.g. interrupted tool execution), some calls may
    lack results.  This function injects error responses so the history is
    valid for transcript rendering.

    Mutations happen in-place via list insertion.
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") != "model" or not isinstance(msg.get("content"), list):
            i += 1
            continue

        # Collect function_call IDs and their tool names in this model message
        call_names: dict[str, str] = {
            block["id"]: block.get("name", "")
            for block in msg["content"]
            if isinstance(block, dict)
            and block.get("type") == "function_call"
            and "id" in block
        }
        call_ids = set(call_names)

        if not call_ids:
            i += 1
            continue

        # Check the next message for matching function_responses
        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        matched_ids: set[str] = set()

        if (
            next_msg
            and next_msg.get("role") == "user"
            and isinstance(next_msg.get("content"), list)
        ):
            matched_ids = {
                block["tool_call_id"]
                for block in next_msg["content"]
                if isinstance(block, dict)
                and block.get("type") == "function_response"
                and block.get("tool_call_id") in call_ids
            }

        orphaned_ids = call_ids - matched_ids

        if orphaned_ids:
            sorted_ids = sorted(orphaned_ids)
            logger.warning(
                "Patching %d orphaned function_call block(s): %s",
                len(sorted_ids),
                sorted_ids,
            )
            synthetic_results = [
                {
                    "type": "function_response",
                    "tool_call_id": uid,
                    "name": call_names.get(uid, ""),
                    "output": "Error: tool execution was interrupted",
                    "is_error": True,
                }
                for uid in sorted_ids
            ]

            if next_msg is not None and next_msg.get("role") == "user":
                if isinstance(next_msg["content"], str):
                    next_msg["content"] = synthetic_results + [
                        {"type": "text", "text": next_msg["content"]}
                    ]
                elif isinstance(next_msg["content"], list):
                    next_msg["content"] = synthetic_results + next_msg["content"]
            else:
                messages.insert(
                    i + 1,
                    {"role": "user", "content": synthetic_results},
                )
                # Skip past the newly inserted synthetic message so it is
                # not re-examined (it contains no function_call blocks).
                i += 1

        i += 1


def _flush_pending_tool_calls(
    messages: GoogleADKMessages, pending_tool_calls: list[dict[str, Any]]
) -> None:
    """Flush pending tool calls into a single model message."""
    if pending_tool_calls:
        messages.append(
            {
                "role": "model",
                "content": list(pending_tool_calls),
            }
        )
        pending_tool_calls.clear()


def _flush_pending_tool_results(
    messages: GoogleADKMessages, pending_tool_results: list[dict[str, Any]]
) -> None:
    """Flush pending tool results into a single user message."""
    if pending_tool_results:
        messages.append(
            {
                "role": "user",
                "content": list(pending_tool_results),
            }
        )
        pending_tool_results.clear()


class GoogleADKHistoryConverter(HistoryConverter[GoogleADKMessages]):
    """
    Converts platform history to Google ADK message format.

    Output: [{"role": "user"|"model", "content": "..." | [...]}]

    Handles:
    - text from this agent: ``role="model"`` with bare content (matches the
      shape the adapter appends live in ``_room_history`` after each reply,
      so a rehydrated own-reply has the same role/content shape as a live
      one — the surrounding transcript is not byte-identical because tool
      events are folded into separate ``function_call``/``function_response``
      blocks and peer messages carry a ``[name]:`` prefix)
    - text from other agents and users: ``role="user"`` with ``[name]:``
      prefix so the LLM can attribute speakers
    - tool_call: ``role="model"`` message with ``function_call`` content blocks
    - tool_result: ``role="user"`` message with ``function_response`` blocks

    Tool events are stored in platform as JSON:
    - tool_call: {"name": "...", "args": {...}, "tool_call_id": "..."}
    - tool_result: {"name": "...", "output": "...", "tool_call_id": "...", "is_error": bool}

    Note: The adapter creates a fresh InMemoryRunner per message and injects
    history as a text transcript (via ``_format_history_transcript``), so the
    structured function_call/function_response blocks produced here are
    consumed only for transcript formatting and conformance validation, not
    passed directly to ADK as ``Content`` objects.

    Why own-agent text is kept (and not dropped as "redundant with tool
    results"): the agent's text replies are NOT recorded as tool results,
    they are recorded as ``message_type="text"`` rows.  Dropping them on
    rehydration leaves the LLM looking at a series of unanswered user
    messages and re-answering questions it already handled — the bug
    documented in INT-509 (ADK duplicate response after crash recovery).

    Own-agent attribution requires a non-empty ``agent_name`` to be set
    via the constructor or ``set_agent_name()``.  Without it, every
    nameless assistant row would be attributed to this agent, swapping
    one false-attribution bug for another.
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Used to decide whether an
                       assistant text message came from this agent (in which
                       case it is emitted with ``role="model"``) or from a
                       peer (emitted as ``role="user"`` with a ``[name]:``
                       prefix).
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set this agent's name for own-vs-peer attribution.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> GoogleADKMessages:
        """Convert platform history to Google ADK format."""
        messages: GoogleADKMessages = []
        pending_tool_calls: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")

            if message_type == "tool_call":
                _flush_pending_tool_results(messages, pending_tool_results)

                parsed = parse_tool_call(content)
                if parsed:
                    tool_call_block = {
                        "type": "function_call",
                        "id": parsed.tool_call_id,
                        "name": parsed.name,
                        "args": parsed.args,
                    }
                    pending_tool_calls.append(tool_call_block)

            elif message_type == "tool_result":
                _flush_pending_tool_calls(messages, pending_tool_calls)

                parsed = parse_tool_result(content)
                if parsed:
                    tool_result_block: dict[str, Any] = {
                        "type": "function_response",
                        "tool_call_id": parsed.tool_call_id,
                        "name": parsed.name,
                        "output": parsed.output,
                    }
                    if parsed.is_error:
                        tool_result_block["is_error"] = True
                    pending_tool_results.append(tool_result_block)

            elif message_type in ("thought", "error"):
                # Thought and error events are not included in LLM history
                continue

            elif message_type == "text":
                _flush_pending_tool_calls(messages, pending_tool_calls)
                _flush_pending_tool_results(messages, pending_tool_results)

                role = hist.get("role", "user")
                sender_name = hist.get("sender_name", "")

                if (
                    role == "assistant"
                    and self._agent_name
                    and sender_name == self._agent_name
                ):
                    # Own-agent text reply: emit as model turn with bare
                    # content (no [name]: prefix). This matches the shape the
                    # adapter appends to ``_room_history`` live after each
                    # response, so the LLM sees its own prior replies in
                    # context.  The empty-name guard prevents a default
                    # ``agent_name=""`` from misattributing every nameless
                    # assistant row to this agent.
                    messages.append({"role": "model", "content": content})
                    continue

                messages.append(
                    {
                        "role": "user",
                        "content": f"[{sender_name}]: {content}"
                        if sender_name
                        else content,
                    }
                )

        # Flush any remaining pending tool calls and results
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        # Patch orphaned function_call blocks that lack matching
        # function_response blocks (e.g. interrupted tool execution).
        _patch_orphaned_tool_calls(messages)

        return messages

    def format_transcript(self, history: GoogleADKMessages) -> str:
        """Render converted history as a labeled text transcript.

        The ADK adapter creates a fresh ``InMemoryRunner`` per message and
        injects accumulated history as a text transcript, so this is the
        layer where rehydration becomes a single string the LLM reads.

        Own-agent text turns (``role="model"`` with string content) are
        labeled with this agent's own name so the LLM can distinguish its
        prior replies from peer turns.  Peer turns are already prefixed
        with ``[sender_name]:`` by ``convert()``; passing them through
        unchanged keeps the prefix shape uniform across the transcript.
        Without the own-turn label the bootstrap transcript looks like a
        series of speakerless lines between peer messages, which is what
        produced the duplicate-reply behavior in INT-509.

        Tool events render as compact ``[Tool Call]`` / ``[Tool Result]``
        previews so the rehydrated context shows what work was already
        done in the prior session.
        """
        lines: list[str] = []
        for msg in history:
            content = msg.get("content", "")
            if isinstance(content, str):
                if msg.get("role") == "model" and self._agent_name:
                    lines.append(f"[{self._agent_name}]: {content}")
                else:
                    lines.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "function_call":
                        lines.append(
                            f"[Tool Call] {block.get('name', 'unknown')}"
                            f" ({json.dumps(block.get('args', {}), default=str)})"
                        )
                    elif block_type == "function_response":
                        output = str(block.get("output", ""))
                        truncated = (
                            output[:_MAX_TOOL_OUTPUT_PREVIEW] + "..."
                            if len(output) > _MAX_TOOL_OUTPUT_PREVIEW
                            else output
                        )
                        lines.append(
                            f"[Tool Result] {block.get('name', 'unknown')}: {truncated}"
                        )
        return "\n".join(lines)
