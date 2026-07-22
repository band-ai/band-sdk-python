"""Tests for the Docker-demo conductor's message-projection helpers.

The async polling loop is thin IO; the parts that can silently misclassify a
sender (and so mis-count the breaker) are the pure projections — those are what
these tests pin down.
"""

from __future__ import annotations

import datetime as dt

from band_rest.types import ChatMessage

from tests.loaders import load_script_module

conductor = load_script_module(
    "examples/docker_demo/conductor.py", "docker_demo_conductor"
)

Roster = conductor.Roster
SenderClass = conductor.SenderClass
to_observed = conductor.to_observed

PM, DEV, ARCH = "pm-id", "dev-id", "arch-id"


def make_message(
    sender_id: str, sender_type: str = "Agent", *, mentions: list[str] | None = None
) -> ChatMessage:
    metadata = (
        {"mentions": [{"id": m} for m in mentions]} if mentions is not None else None
    )
    return ChatMessage(
        id="m1",
        content="hi",
        message_type="text",
        sender_id=sender_id,
        sender_type=sender_type,
        inserted_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        metadata=metadata,
    )


def roster() -> Roster:
    return Roster(pm_id=PM, dev_id=DEV, architect_id=ARCH)


def test_classify_maps_each_agent_to_its_role() -> None:
    r = roster()
    assert r.classify(make_message(PM)) is SenderClass.PM, (
        "PM's agent id must classify as PM"
    )
    assert r.classify(make_message(DEV)) is SenderClass.DEVELOPER, (
        "Dev's agent id must classify as DEVELOPER"
    )
    assert r.classify(make_message(ARCH)) is SenderClass.ARCHITECT, (
        "Architect's agent id must classify as ARCHITECT"
    )


def test_classify_treats_unknown_user_as_human() -> None:
    r = roster()
    assert (
        r.classify(make_message("someone", sender_type="User")) is SenderClass.HUMAN
    ), "an unrecognized User is the presenter/conductor — a human, never counted"


def test_classify_treats_unknown_agent_as_unknown() -> None:
    r = roster()
    assert (
        r.classify(make_message("stray", sender_type="Agent")) is SenderClass.UNKNOWN
    ), "an unrecognized Agent must not be silently counted as a design participant"


def test_mentions_architect_detects_the_handoff() -> None:
    r = roster()
    assert r.mentions_architect(make_message(PM, mentions=[ARCH])) is True, (
        "a PM message @mentioning the architect is the Act 1->Act 2 handoff signal"
    )
    assert r.mentions_architect(make_message(PM, mentions=[DEV])) is False, (
        "mentioning the Dev is not a handoff"
    )
    assert r.mentions_architect(make_message(PM)) is False, (
        "no metadata means no mention"
    )


def test_to_observed_projects_all_breaker_inputs() -> None:
    r = roster()
    obs = to_observed(make_message(PM, mentions=[ARCH]), r)
    assert obs.sender_class is SenderClass.PM, (
        "projection must carry the classified sender"
    )
    assert obs.mentions_architect is True, "projection must carry the handoff signal"
    assert (
        obs.timestamp == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    ), (
        "projection must use the message's own timestamp so the breaker's clock is the room's clock"
    )
