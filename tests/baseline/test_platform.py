"""Offline baseline checks for the platform context passed to an adapter."""

from __future__ import annotations

from band.runtime.formatters import build_participants_message, format_history_for_llm


def test_mention_routing_uses_handles_and_never_exposes_participant_ids() -> None:
    participants = [
        {
            "id": "agent-opaque-id",
            "name": "Research Agent",
            "type": "Agent",
            "handle": "org/research",
        },
        {
            "id": "user-opaque-id",
            "name": "Baseline User",
            "type": "User",
            "handle": "baseline-user",
        },
    ]

    prompt = build_participants_message(participants)

    assert "@org/research" in prompt
    assert "@baseline-user" in prompt
    assert "agent-opaque-id" not in prompt
    assert "user-opaque-id" not in prompt


def test_history_hydration_trims_the_trigger_and_normalizes_mentions() -> None:
    history = [
        {
            "id": "message-prior",
            "sender_type": "User",
            "sender_name": "Baseline User",
            "content": "Ask @[[agent-id]] about marker-alpha",
        },
        {
            "id": "message-trigger",
            "sender_type": "User",
            "sender_name": "Baseline User",
            "content": "This current message must not be duplicated",
        },
    ]

    formatted = format_history_for_llm(
        history,
        exclude_id="message-trigger",
        participants=[{"id": "agent-id", "handle": "org/research"}],
    )

    assert formatted == [
        {
            "role": "user",
            "content": "Ask @org/research about marker-alpha",
            "sender_name": "Baseline User",
            "sender_type": "User",
            "message_type": "text",
            "metadata": {},
        }
    ]
