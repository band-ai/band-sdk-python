"""Offline tests for the Docker-demo conversation circuit breaker.

The breaker is the demo's safety piece — it must be provably correct without a
live platform, so it never touches a clock or the network and is driven here by
a scripted message stream. These tests are the reason it can be trusted on stage.
"""

from __future__ import annotations

import pytest

from tests.loaders import load_script_module

breaker = load_script_module("examples/docker_demo/breaker.py", "docker_demo_breaker")

Action = breaker.Action
BreakerConfig = breaker.BreakerConfig
CircuitBreaker = breaker.CircuitBreaker
ObservedMessage = breaker.ObservedMessage
SenderClass = breaker.SenderClass


def msg(
    sender: SenderClass, t: float, *, mentions_architect: bool = False
) -> ObservedMessage:
    return ObservedMessage(
        sender_class=sender, timestamp=t, mentions_architect=mentions_architect
    )


@pytest.fixture
def config() -> BreakerConfig:
    # Small, explicit ceilings so a test's intent is legible from its message count.
    return BreakerConfig(soft_cap=4, hard_cap=8, wall_clock_s=100.0, grace_s=20.0)


def feed(cb: CircuitBreaker, messages: list[ObservedMessage]) -> list[Action]:
    """Record each message and collect every action the breaker emits along the way."""
    actions: list[Action] = []
    for m in messages:
        cb.record(m)
        actions.extend(cb.poll(m.timestamp))
    return actions


def test_quiet_meeting_emits_nothing(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    actions = feed(cb, [msg(SenderClass.PM, 1.0), msg(SenderClass.DEVELOPER, 2.0)])
    assert actions == [], (
        "a short design exchange under every cap must not trigger the breaker"
    )


def test_human_messages_never_count_toward_caps(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # Far more than soft_cap human turns, plus conductor turns — none should count.
    human_flood = [msg(SenderClass.HUMAN, float(i)) for i in range(20)]
    human_flood.append(msg(SenderClass.CONDUCTOR, 21.0))
    actions = feed(cb, human_flood)
    assert actions == [], (
        "human/conductor chatter must never trip the design cap — only agents do"
    )


def test_soft_cap_without_handoff_nudges_and_adds_architect(
    config: BreakerConfig,
) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # soft_cap design messages, no @architect mention anywhere.
    design = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.soft_cap)
    ]
    actions = feed(cb, design)
    assert actions == [Action.NUDGE_HANDOFF, Action.ADD_ARCHITECT], (
        "stalled design chat must both nudge the PM and add the Architect as a fallback"
    )


def test_soft_nudge_fires_at_most_once(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # Keep the design chat going well past soft_cap but below hard_cap.
    design = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.hard_cap - 1)
    ]
    actions = feed(cb, design)
    assert actions.count(Action.NUDGE_HANDOFF) == 1, (
        "the handoff nudge must fire once, not on every message past the soft cap"
    )


def test_pm_handoff_suppresses_the_nudge(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # The PM invites the Architect exactly at the soft cap — the authentic path.
    design = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.soft_cap - 1)
    ]
    design.append(msg(SenderClass.PM, float(config.soft_cap), mentions_architect=True))
    actions = feed(cb, design)
    assert Action.NUDGE_HANDOFF not in actions, (
        "when the PM hands off itself, the conductor must stay invisible"
    )


def test_note_architect_present_suppresses_the_nudge(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.note_architect_present()  # conductor detected the Architect joined
    design = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.soft_cap)
    ]
    actions = feed(cb, design)
    assert Action.NUDGE_HANDOFF not in actions, (
        "once the Architect is in the room we're in Act 2 — no nudge"
    )


def test_architect_decision_terminates_cleanly_after_grace(
    config: BreakerConfig,
) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(SenderClass.PM, 1.0, mentions_architect=True))
    cb.record(msg(SenderClass.ARCHITECT, 2.0))  # the decision

    assert cb.poll(2.0 + config.grace_s - 1) == [], (
        "must hold open during the grace window for trailing acks"
    )
    assert cb.poll(2.0 + config.grace_s) == [Action.TERMINATE_OK], (
        "clean stop once the grace window elapses"
    )


def test_terminate_wins_over_hard_cap(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # Push agent messages to the hard cap, but the last one is the Architect's decision.
    stream = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.hard_cap - 1)
    ]
    stream.append(
        msg(SenderClass.ARCHITECT, float(config.hard_cap), mentions_architect=False)
    )
    feed(cb, stream)
    assert cb.poll(config.hard_cap + config.grace_s) == [Action.TERMINATE_OK], (
        "a meeting that reached a decision must end cleanly, not as a hard kill"
    )


def test_hard_cap_kills_a_runaway(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    # Agents that never hand off and never let the Architect decide.
    runaway = [
        msg(SenderClass.PM if i % 2 == 0 else SenderClass.DEVELOPER, float(i + 1))
        for i in range(config.hard_cap)
    ]
    actions = feed(cb, runaway)
    assert Action.HARD_KILL in actions, (
        "an agent loop that hits the hard cap must be force-killed"
    )


def test_wall_clock_kills_a_slow_meeting(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(SenderClass.PM, 1.0))
    assert cb.poll(config.wall_clock_s) == [Action.HARD_KILL], (
        "exceeding the wall-clock ceiling must force-kill even under the message cap"
    )


def test_no_actions_after_terminal(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(SenderClass.PM, 1.0))
    assert cb.poll(config.wall_clock_s) == [Action.HARD_KILL]
    assert cb.stopped is True, "breaker must report it has stopped"
    cb.record(msg(SenderClass.DEVELOPER, config.wall_clock_s + 1))
    assert cb.poll(config.wall_clock_s + 5) == [], (
        "a stopped breaker must never emit another action"
    )


def test_context_manager_closes_the_meeting_on_exit(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    with cb as guard:
        assert guard is cb, (
            "entering the guard yields the breaker to drive inside the block"
        )
        cb.record(msg(SenderClass.PM, 1.0))
    assert cb.stopped is True, "leaving the guarded block must close the meeting"
    assert cb.poll(2.0) == [], (
        "a closed breaker emits no further actions — the runaway can't outlive the block"
    )


def test_context_manager_does_not_suppress_exceptions(config: BreakerConfig) -> None:
    with pytest.raises(ValueError):
        with CircuitBreaker(config, start_time=0.0):
            raise ValueError("boom")
