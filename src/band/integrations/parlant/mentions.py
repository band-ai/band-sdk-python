"""How Parlant tools take mentions: one comma-separated string, not a list."""

from __future__ import annotations

from typing import Any

from band.runtime.tools import append_available_mention_handles

# Parlant tools take mentions as a comma-separated string, not the master
# model's list[str], so the master description needs this appended — it is
# genuinely Parlant-specific and not something get_tool_description() covers.
# Phrased without "array"/"list" wording so it doesn't read as contradicting
# the master text's "mentions array" line right above it.
SEND_MESSAGE_MENTIONS_NOTE = (
    "\n\nThis tool's mentions argument is a single string: separate multiple "
    'handles with commas, e.g. "@alice, @bob/agent".'
)

# Same divergence as SEND_MESSAGE_MENTIONS_NOTE, appended to the per-argument
# description instead of the tool-level one: the master field's list-oriented
# text would otherwise reach the LLM unqualified for this comma-separated param.
SEND_MESSAGE_MENTIONS_PARAM_NOTE = (
    " This tool takes it as a single comma-separated string, not a list, "
    'e.g. "@alice, @bob/agent".'
)


def split_mentions(mentions: str) -> list[str]:
    """Parlant's one comma-separated mentions string, as the platform's handle list."""
    return [mention.strip() for mention in mentions.split(",") if mention.strip()]


def missing_mentions_error(tools: Any) -> str:
    """The refusal of a send with no mentions, listing the handles to retry with."""
    return "Error: " + with_mention_handles("At least one mention is required", tools)


def with_mention_handles(message: str, tools: Any) -> str:
    """``message`` plus the handles this room offers, so a bad mention can retry."""
    return append_available_mention_handles(
        message, tools.participants, getattr(tools, "agent_id", None)
    )
