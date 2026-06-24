"""Tolerant assertions for the live (Tier-2) baseline scenarios.

Tier-2 drives real LLM agents, so assertions must tolerate phrasing, count, and
ordering variance. These check what an agent *can do* — presence of required
facts, that it responded, that it addressed the right peer — rather than exact
reply counts or literal token recitation. The scenario-local filter helpers
(``_messages_containing`` etc.) live here too so all five scenarios share one
layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from band_rest import AsyncRestClient

from tests.e2e.helpers import (
    agent_text_messages,
    fetch_chat_messages,
    mention_handles,
    mention_ids,
    message_value,
)


def content_of(message: Any) -> str:
    return str(message_value(message, "content") or "")


def new_agent_messages(
    messages: list[Any], sender_id: str, before_ids: set[str]
) -> list[Any]:
    """Text messages from ``sender_id`` that are new since the turn boundary."""
    return agent_text_messages(messages, sender_id, before_ids)


def messages_containing(
    messages: list[Any], sender_id: str, text: str, before_ids: set[str]
) -> list[Any]:
    """New messages from ``sender_id`` whose content contains ``text`` (ci)."""
    needle = text.lower()
    return [
        message
        for message in new_agent_messages(messages, sender_id, before_ids)
        if needle in content_of(message).lower()
    ]


def messages_mentioning(
    messages: list[Any], sender_id: str, target_id: str, before_ids: set[str]
) -> list[Any]:
    """New messages from ``sender_id`` that @mention ``target_id`` (via metadata)."""
    return [
        message
        for message in new_agent_messages(messages, sender_id, before_ids)
        if target_id in mention_ids(message)
    ]


async def wait_until_agent_quiescent(
    client: AsyncRestClient,
    room_id: str,
    agent_id: str,
    *,
    quiet: float = 8.0,
    max_wait: float = 90.0,
    poll: float = 1.0,
) -> list[Any]:
    """Return the agent's text messages once it has posted nothing new for ``quiet``s.

    Lets a burst of planted messages fully settle before the next turn, so
    trailing replies don't bleed into and contaminate it. Adaptive (returns as
    soon as it's quiet), bounded by ``max_wait`` — not a fixed observation window.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait
    last_count = -1
    last_change = loop.time()
    messages: list[Any] = []
    while loop.time() < deadline:
        messages = agent_text_messages(
            await fetch_chat_messages(client, room_id), agent_id
        )
        if len(messages) != last_count:
            last_count = len(messages)
            last_change = loop.time()
        elif loop.time() - last_change >= quiet:
            return messages
        await asyncio.sleep(poll)
    return messages


def assert_agent_responded(messages: list[Any], *, min_count: int = 1) -> None:
    """The agent produced at least ``min_count`` replies — presence, not exact count."""
    assert len(messages) >= min_count, (
        f"expected >= {min_count} agent message(s), got {len(messages)}: "
        f"{[content_of(message) for message in messages]}"
    )


def assert_recalled_at_least(
    messages: list[Any], facts: Iterable[str], min_count: int
) -> None:
    """At least ``min_count`` of ``facts`` appear across messages (ci substring).

    Tolerant recall: an LLM may paraphrase, reorder, or split facts across
    replies, so we require a threshold of the planted facts rather than all of
    them in one message.
    """
    facts = list(facts)
    blob = "\n".join(content_of(message).lower() for message in messages)
    hits = [fact for fact in facts if fact.lower() in blob]
    assert len(hits) >= min_count, (
        f"expected >= {min_count} of {facts} recalled, found {hits} in: "
        f"{[content_of(message) for message in messages]}"
    )


def assert_contains_any(messages: list[Any], candidates: Iterable[str]) -> None:
    """At least one of ``candidates`` appears across messages (ci substring)."""
    candidates = list(candidates)
    blob = "\n".join(content_of(message).lower() for message in messages)
    assert any(candidate.lower() in blob for candidate in candidates), (
        f"expected one of {candidates} in: "
        f"{[content_of(message) for message in messages]}"
    )


def assert_addresses_peer(
    messages: list[Any], *, peer_id: str | None = None, handle: str | None = None
) -> None:
    """Every message addresses the peer via mention metadata (id or handle).

    Mentions are stored as ``@[[uuid]]`` tokens with the handle resolved into
    metadata, so this checks the metadata — not the raw content text.
    """
    assert messages, "no messages to check for peer mention"
    norm_handle = handle.lstrip("@").lower() if handle else None
    for message in messages:
        by_id = peer_id is not None and peer_id in mention_ids(message)
        by_handle = norm_handle is not None and norm_handle in mention_handles(message)
        assert by_id or by_handle, {
            "content": content_of(message),
            "mention_ids": sorted(mention_ids(message)),
            "mention_handles": sorted(mention_handles(message)),
            "wanted_id": peer_id,
            "wanted_handle": norm_handle,
        }
