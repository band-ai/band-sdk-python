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
    sender_id: str,
    sender_type: str = "Agent",
    *,
    mentions: list[str] | None = None,
    id: str = "m1",
    content: str = "hi",
) -> ChatMessage:
    metadata = (
        {"mentions": [{"id": m} for m in mentions]} if mentions is not None else None
    )
    return ChatMessage(
        id=id,
        content=content,
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
    obs = to_observed(make_message(PM, mentions=[ARCH]), r, set())
    assert obs.sender_class is SenderClass.PM, (
        "projection must carry the classified sender"
    )
    assert obs.mentions_architect is True, "projection must carry the handoff signal"
    assert (
        obs.timestamp == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    ), (
        "projection must use the message's own timestamp so the breaker's clock is the room's clock"
    )


def test_presenter_message_marks_presenter_activity() -> None:
    r = roster()
    obs = to_observed(make_message("presenter", sender_type="User", id="p1"), r, set())
    assert obs.is_presenter is True, (
        "a human message that isn't one of our own posts is genuine presenter activity"
    )
    assert obs.is_end_signal is False, (
        "an ordinary presenter message doesn't end the meeting"
    )


def test_conductor_own_post_is_not_presenter_activity() -> None:
    r = roster()
    # Same identity as the presenter (User), but its id is in the self-posted set:
    # the invite/closer must never masquerade as presenter input (would reset idle).
    obs = to_observed(make_message("presenter", sender_type="User", id="c1"), r, {"c1"})
    assert obs.is_presenter is False, (
        "the conductor's own posts share the presenter's identity but must not count as activity"
    )


def test_end_phrase_from_presenter_is_an_end_signal() -> None:
    r = roster()
    msg = make_message(
        "presenter", sender_type="User", id="p2", content="ok, end meeting"
    )
    obs = to_observed(msg, r, set())
    assert obs.is_end_signal is True, (
        "the presenter's end phrase must be projected as the meeting-ending signal"
    )


def test_end_phrase_from_an_agent_is_ignored() -> None:
    r = roster()
    msg = make_message(PM, id="a1", content="let's wrap up the API section")
    obs = to_observed(msg, r, set())
    assert obs.is_end_signal is False, (
        "only the presenter can end the meeting — an agent saying 'wrap up' must not"
    )


def test_conductor_caps_do_not_drift_from_breaker_defaults() -> None:
    # Guards the single-source rule: ConductorSettings cap defaults must equal the
    # BreakerConfig defaults, so the two can never silently drift (300 vs 600 again).
    # `interactive` is intentionally different (conductor defaults to interactive,
    # the breaker to headless-safe), so normalize just that one mode flag.
    import dataclasses

    settings = conductor.ConductorSettings()
    normalized = dataclasses.replace(settings.breaker_config(), interactive=False)
    assert normalized == conductor.BreakerConfig(), (
        "conductor's default caps/idle must match the single-source BreakerConfig defaults"
    )
