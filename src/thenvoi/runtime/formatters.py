"""Pure functions for message formatting. No I/O, fully unit-testable."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _normalize_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _chronological_messages(messages: list[dict]) -> list[dict]:
    """Sort messages by timestamp, stably, without displacing untimestamped ones.

    Messages lacking a parseable ``inserted_at``/``created_at`` inherit the
    sort key of the nearest preceding timestamped message, so they keep their
    original position relative to their neighbors instead of migrating to one
    end of the list.
    """
    keyed: list[tuple[datetime, int, dict[str, Any]]] = []
    last_timestamp = datetime.min.replace(tzinfo=timezone.utc)
    for index, message in enumerate(messages):
        raw_timestamp = message.get("inserted_at") or message.get("created_at")
        parsed = _normalize_timestamp(raw_timestamp)
        if parsed is not None:
            last_timestamp = parsed
        keyed.append((last_timestamp, index, message))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [message for _timestamp, _index, message in keyed]


def replace_uuid_mentions(content: str, participants: list[dict]) -> str:
    """
    Replace UUID mentions in content with @handle format using participants list.

    Args:
        content: Message content potentially containing @[[uuid]] patterns
        participants: List of participants with {id, handle, name, type}

    Returns:
        Content with UUID mentions replaced by @handle
    """
    if not participants or not content:
        return content

    for p in participants:
        participant_id = p.get("id")
        handle = p.get("handle")
        if participant_id and handle:
            content = content.replace(f"@[[{participant_id}]]", f"@{handle}")

    return content


def format_message_for_llm(msg: dict, participants: list[dict] | None = None) -> dict:
    """
    Map platform message to LLM format.

    Args:
        msg: Platform message dict with sender_type, content, sender_name
        participants: Optional list of participants for UUID mention replacement

    Returns:
        Dict with role, content, sender_name, sender_type, message_type, metadata
    """
    sender_type = msg.get("sender_type", "")
    sender_name = msg.get("sender_name") or msg.get("name") or sender_type

    content = msg.get("content", "")
    if participants:
        content = replace_uuid_mentions(content, participants)

    return {
        "role": "assistant" if sender_type == "Agent" else "user",
        "content": content,
        "sender_name": sender_name,
        "sender_type": sender_type,
        "message_type": msg.get("message_type", "text"),
        "metadata": msg.get("metadata", {}),
    }


def format_history_for_llm(
    messages: list[dict],
    exclude_id: str | None = None,
    participants: list[dict] | None = None,
) -> list[dict]:
    """
    Format platform message history for LLM injection.

    Args:
        messages: List of platform message dicts
        exclude_id: Message ID to exclude (usually current message)
        participants: Optional list of participants for UUID mention replacement

    Returns:
        List of formatted message dicts
    """
    return [
        format_message_for_llm(m, participants=participants)
        for m in _chronological_messages(messages)
        if m.get("id") != exclude_id
    ]


def build_participants_message(participants: list[dict]) -> str:
    """
    Build participant list message for LLM context.

    Includes instruction to use thenvoi_send_message with handles or names.

    Args:
        participants: List of participant dicts with id, name, type, handle

    Returns:
        Formatted string for LLM system message
    """
    if not participants:
        return "## Current Participants\nNo other participants in this room."

    lines = ["## Current Participants"]
    for p in participants:
        p_type = p.get("type", "Unknown")
        p_name = p.get("name", "Unknown")
        p_handle = p.get("handle", "Unknown")
        description = str(p.get("description") or "").strip()
        line = f"- @{p_handle} — {p_name} ({p_type})"
        if description:
            line = f"{line}: {description}"
        lines.append(line)

    lines.append("")
    lines.append(
        "IMPORTANT: In thenvoi_send_message mentions, always use the exact "
        "handle shown above (e.g. '@john' for users, '@john/weather-agent' "
        "for agents), NOT the display name. Handles are lowercase with no spaces."
    )

    return "\n".join(lines)
