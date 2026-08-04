"""Strands Agents history converter."""

from __future__ import annotations

import logging
from typing import Any

try:
    from strands.types.content import ContentBlock, Message, Role
except ImportError as e:
    raise ImportError(
        "Strands Agents dependencies not installed. "
        "Install with: uv add band-sdk[strands]"
    ) from e

from band.converters.parsing import (
    INTERRUPTED_TOOL_TEXT,
    parse_tool_call,
    parse_tool_result,
)
from band.core.protocols import HistoryConverter
from band.core.types import MessageType

logger = logging.getLogger(__name__)

StrandsMessages = list[Message]


def _tool_use_ids(message: Message) -> list[str]:
    """Return the toolUse ids an assistant message asks to be answered."""
    if message["role"] != "assistant":
        return []
    return [
        block["toolUse"]["toolUseId"]
        for block in message["content"]
        if "toolUse" in block
    ]


def _answered_ids(message: Message | None) -> set[str]:
    """Return the toolUse ids a user message answers with toolResult blocks."""
    if message is None or message["role"] != "user":
        return set()
    return {
        block["toolResult"]["toolUseId"]
        for block in message["content"]
        if "toolResult" in block
    }


def _synthetic_results(tool_use_ids: list[str]) -> list[ContentBlock]:
    """Build error toolResults that close out unanswered toolUse blocks."""
    return [
        {
            "toolResult": {
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": INTERRUPTED_TOOL_TEXT}],
            }
        }
        for tool_use_id in tool_use_ids
    ]


def _patch_orphaned_tool_uses(messages: StrandsMessages) -> None:
    """Answer every unanswered toolUse with a synthetic error toolResult.

    Converse rejects an assistant toolUse whose toolResult does not follow it,
    and the room transcript can be missing one: the platform ``tool_result``
    event may have failed to send, or its payload may be unparseable. Without
    this repair the broken pair is replayed on every later turn in the room, so
    the whole room stops responding.

    Synthetic results join a following user message only when that message
    already carries toolResults; otherwise they are inserted ahead of it, so
    the tool answers stay first in the turn once same-role messages merge.

    Mutates ``messages`` in place.
    """
    # Reverse order so inserting a repair never shifts an index still to visit.
    for index in reversed(range(len(messages))):
        pending = _tool_use_ids(messages[index])
        if not pending:
            continue

        follower = messages[index + 1] if index + 1 < len(messages) else None
        answered = _answered_ids(follower)
        orphaned = [
            tool_use_id for tool_use_id in pending if tool_use_id not in answered
        ]
        if not orphaned:
            continue

        logger.warning(
            "Patching %d unanswered toolUse block(s): %s", len(orphaned), orphaned
        )
        if follower is not None and answered:
            follower["content"] = _synthetic_results(orphaned) + list(
                follower["content"]
            )
        else:
            messages.insert(
                index + 1, {"role": "user", "content": _synthetic_results(orphaned)}
            )


def _merge_consecutive_roles(messages: StrandsMessages) -> StrandsMessages:
    """Combine neighbouring messages that share a role.

    Bedrock's Converse rejects a conversation that does not alternate between
    user and assistant, and room history routinely produces same-role
    neighbours: two peers speaking in a row, or a peer's turn landing behind
    the tool results it waited for.

    """
    merged: StrandsMessages = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] = list(merged[-1]["content"]) + list(
                message["content"]
            )
            continue
        merged.append(message)
    return merged


class ConverseTranscript:
    """Build Converse messages, keeping each toolUse next to its toolResult.

    Converse pairs an assistant toolUse message with the user toolResult
    message that immediately follows it. Room history interleaves freely: a
    peer can post while a tool is still running, and Strands runs a round's
    tools concurrently, so a later call can be recorded after an earlier
    result. An exchange therefore stays open until every call it made has been
    answered — its calls, its results, and the turns that arrived during it are
    buffered until then, and only then emitted as one paired sequence.
    """

    def __init__(self) -> None:
        self._messages: StrandsMessages = []
        self._tool_uses: list[ContentBlock] = []
        self._tool_results: list[ContentBlock] = []
        self._held_turns: StrandsMessages = []
        self._unanswered: set[str] = set()

    @property
    def in_tool_exchange(self) -> bool:
        """Whether a toolUse is still waiting for its toolResult."""
        return bool(self._unanswered)

    def add_tool_use(self, tool_use_id: str, name: str, args: dict[str, Any]) -> None:
        """Record a tool call, batching a round's calls into one assistant message."""
        self._tool_uses.append(
            {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": args}}
        )
        self._unanswered.add(tool_use_id)

    def add_tool_result(
        self, tool_use_id: str, name: str, output: str, *, is_error: bool
    ) -> None:
        """Record a tool result, closing the exchange once nothing is outstanding."""
        if tool_use_id not in self._unanswered:
            # The matching tool_call event never reached history (failed send or
            # unparseable payload). Converse rejects a toolResult with no
            # preceding toolUse, so give the result a call to answer.
            logger.warning(
                "Synthesizing toolUse for orphaned toolResult %s (%s)",
                tool_use_id,
                name,
            )
            self.add_tool_use(tool_use_id, name, {})
        self._tool_results.append(
            {
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "status": "error" if is_error else "success",
                    "content": [{"text": output}],
                }
            }
        )
        self._unanswered.discard(tool_use_id)
        if not self.in_tool_exchange:
            self._close_tool_exchange()

    def add_turn(self, role: Role, text: str) -> None:
        """Append a text turn, holding it back while a tool exchange is open."""
        turn: Message = {"role": role, "content": [{"text": text}]}
        if self.in_tool_exchange:
            self._held_turns.append(turn)
            return
        self._messages.append(turn)

    def build(self) -> StrandsMessages:
        """Return the finished transcript: every call answered, roles alternating."""
        self._close_tool_exchange()
        _patch_orphaned_tool_uses(self._messages)
        return _merge_consecutive_roles(self._messages)

    def _close_tool_exchange(self) -> None:
        """Emit the exchange's calls, then its results, then the turns it held."""
        if self._tool_uses:
            self._messages.append(
                {"role": "assistant", "content": list(self._tool_uses)}
            )
            self._tool_uses.clear()
        if self._tool_results:
            self._messages.append({"role": "user", "content": list(self._tool_results)})
            self._tool_results.clear()
        self._messages.extend(self._held_turns)
        self._held_turns.clear()
        self._unanswered.clear()


class StrandsHistoryConverter(HistoryConverter[StrandsMessages]):
    """Convert Band history to Strands Converse messages."""

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Messages from this agent are preserved
                       as assistant turns. Messages from other agents are included
                       as user turns with a [name] prefix.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so the converter can recognize this agent's own messages.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> StrandsMessages:
        """Convert platform history to Strands Converse format."""
        transcript = ConverseTranscript()

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")

            match message_type:
                case MessageType.TOOL_CALL:
                    self._handle_tool_call(content, transcript)
                case MessageType.TOOL_RESULT:
                    self._handle_tool_result(content, transcript)
                case MessageType.TEXT:
                    self._handle_text(hist, content, transcript)
                case MessageType.THOUGHT | MessageType.ERROR | MessageType.TASK:
                    # Known platform-internal types intentionally excluded from
                    # LLM history.
                    pass
                case _:
                    logger.warning("Unknown message_type in history: %s", message_type)

        return transcript.build()

    def _handle_tool_call(self, content: str, transcript: ConverseTranscript) -> None:
        """Record a tool call."""
        parsed = parse_tool_call(content)
        if parsed:
            transcript.add_tool_use(parsed.tool_call_id, parsed.name, parsed.args)

    def _handle_tool_result(self, content: str, transcript: ConverseTranscript) -> None:
        """Record a tool result."""
        parsed = parse_tool_result(content)
        if parsed:
            transcript.add_tool_result(
                parsed.tool_call_id,
                parsed.name,
                parsed.output,
                is_error=parsed.is_error,
            )

    def _handle_text(
        self, hist: dict[str, Any], content: str, transcript: ConverseTranscript
    ) -> None:
        """Append a text turn: own text as assistant, others as user."""
        role = hist.get("role", "user")
        sender_name = hist.get("sender_name", "")
        is_own = (
            role == "assistant" and self._agent_name and sender_name == self._agent_name
        )

        # Own platform text during an open tool exchange is usually the side
        # effect of band_send_message; the tool call already records it, and an
        # assistant turn here would split the toolUse from its toolResult.
        if is_own and transcript.in_tool_exchange:
            return

        if is_own:
            # Preserve own text so restart rehydration knows the agent already replied.
            transcript.add_turn("assistant", content)
        else:
            # User messages AND other agents' messages
            transcript.add_turn(
                "user", f"[{sender_name}]: {content}" if sender_name else content
            )
