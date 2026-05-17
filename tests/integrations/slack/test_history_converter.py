"""Unit tests for ``SlackHistoryConverter`` (Step 8 — session rehydration).

The converter's only job is to recover the Slack thread identity for
one room from that room's platform history. Tests cover the happy path,
the "no Slack context" case, missing-field defenses, and the
"last-bootstrap-wins" tiebreak.
"""

from __future__ import annotations

from typing import Any

from thenvoi.converters.slack import SlackHistoryConverter
from thenvoi.integrations.slack.types import SlackRoomBinding


def _task_event(
    *,
    app_slug: str = "dev",
    channel: str = "C1",
    thread_ts: str = "100.0",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "slack_app_slug": app_slug,
        "slack_channel_id": channel,
        "slack_thread_ts": thread_ts,
        "slack_user_id": "U1",
        "slack_room_id": "room-X",
    }
    if extra:
        metadata.update(extra)
    return {
        "role": "user",
        "content": "Slack thread context",
        "sender_name": "agent",
        "sender_type": "Agent",
        "message_type": "task",
        "metadata": metadata,
    }


def _text_message(content: str = "hi") -> dict[str, Any]:
    return {
        "role": "user",
        "content": content,
        "sender_name": "alice",
        "sender_type": "user",
        "message_type": "text",
        "metadata": {},
    }


def test_returns_binding_when_bootstrap_event_present():
    state = SlackHistoryConverter().convert(
        [
            _text_message("earlier reply"),
            _task_event(app_slug="dev", channel="C42", thread_ts="123.45"),
            _text_message("later reply"),
        ]
    )
    assert state.binding == SlackRoomBinding(
        app_slug="dev",
        channel="C42",
        thread_ts="123.45",
    )


def test_returns_none_when_no_slack_context():
    state = SlackHistoryConverter().convert(
        [_text_message("hello"), _text_message("world")]
    )
    assert state.binding is None


def test_returns_none_for_empty_history():
    assert SlackHistoryConverter().convert([]).binding is None


def test_ignores_non_task_events_with_slack_metadata():
    """A regular message that happens to carry slack metadata isn't a
    bootstrap event and must not produce a binding."""
    state = SlackHistoryConverter().convert(
        [
            {
                "role": "user",
                "content": "hi",
                "sender_name": "alice",
                "sender_type": "user",
                "message_type": "text",
                "metadata": {
                    "slack_app_slug": "dev",
                    "slack_channel_id": "C1",
                    "slack_thread_ts": "1.0",
                },
            }
        ]
    )
    assert state.binding is None


def test_skips_task_event_missing_required_fields():
    state = SlackHistoryConverter().convert(
        [
            {
                "role": "user",
                "content": "Slack thread context",
                "sender_name": "agent",
                "sender_type": "Agent",
                "message_type": "task",
                "metadata": {"slack_app_slug": "dev"},  # no channel/thread_ts
            }
        ]
    )
    assert state.binding is None


def test_last_bootstrap_wins_when_history_has_multiple():
    state = SlackHistoryConverter().convert(
        [
            _task_event(app_slug="dev", channel="C1", thread_ts="1.0"),
            _task_event(app_slug="other", channel="C2", thread_ts="2.0"),
        ]
    )
    assert state.binding == SlackRoomBinding(
        app_slug="other", channel="C2", thread_ts="2.0"
    )


def test_handles_missing_metadata_field():
    """Some history entries may omit ``metadata`` entirely; treat as empty."""
    state = SlackHistoryConverter().convert(
        [
            {
                "role": "user",
                "content": "x",
                "sender_name": "alice",
                "sender_type": "user",
                "message_type": "task",
                # no "metadata" key at all
            }
        ]
    )
    assert state.binding is None
