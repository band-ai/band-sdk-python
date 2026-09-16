"""Offline tests for the Docker-demo conversation circuit breaker.

The breaker is the demo's safety piece — it must be provably correct without a
live platform, so it never touches a clock or the network and is driven here by
a scripted message stream. These tests are the reason it can be trusted on stage,
and they pin the dangerous ambiguities (what counts as a decision, what a mere
@mention proves, whose clock times the grace window) explicitly.
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

PM, DEV, ARCH = SenderClass.PM, SenderClass.DEVELOPER, SenderClass.ARCHITECT

# decided_interactive drives the meeting to a verdict and opens the floor at this
# instant; the open-floor tests measure every deadline relative to it (VERDICT_AT +
# idle) so the timeline reads as intent, not bare timestamps.
VERDICT_AT = 10.0


def msg(
    sender,
    t,
    *,
    mentions_architect=False,
    is_final_decision=False,
    is_presenter=False,
    is_end_signal=False,
) -> ObservedMessage:
    return ObservedMessage(
        sender_class=sender,
        timestamp=t,
        mentions_architect=mentions_architect,
        is_final_decision=is_final_decision,
        is_presenter=is_presenter,
        is_end_signal=is_end_signal,
    )


def presenter(t: float, *, end: bool = False) -> ObservedMessage:
    """A genuine presenter message at time t (optionally the end phrase)."""
    return msg(SenderClass.HUMAN, t, is_presenter=True, is_end_signal=end)


def decided_interactive(idle_s: float = 30.0) -> CircuitBreaker:
    """A breaker in interactive mode driven straight to a verdict, floor just opened.

    Returns the breaker after the first post-verdict poll (which emits OPEN_FLOOR),
    so a test can exercise the open floor from t=verdict onward.
    """
    cfg = BreakerConfig(
        soft_cap=4,
        handoff_deadline=2,
        hard_cap=8,
        wall_clock_s=100.0,
        interactive=True,
        open_floor_idle_s=idle_s,
    )
    cb = CircuitBreaker(cfg, start_time=0.0)
    cb.record(msg(ARCH, VERDICT_AT, is_final_decision=True))
    assert cb.poll(VERDICT_AT) == [Action.OPEN_FLOOR], (
        "verdict must open the floor once"
    )
    return cb


def design_run(n: int, start: int = 1) -> list[ObservedMessage]:
    """n alternating PM/Dev design messages, timestamped from `start`."""
    return [msg(PM if i % 2 == 0 else DEV, float(start + i)) for i in range(n)]


@pytest.fixture
def config() -> BreakerConfig:
    # Small, explicit ceilings so a test's intent is legible from its message count.
    return BreakerConfig(
        soft_cap=4, handoff_deadline=2, hard_cap=8, wall_clock_s=100.0, grace_s=20.0
    )


def feed(cb: CircuitBreaker, messages: list[ObservedMessage]) -> list[Action]:
    """Record each message and collect every action the breaker emits along the way."""
    actions: list[Action] = []
    for m in messages:
        cb.record(m)
        actions.extend(cb.poll(m.timestamp))
    return actions


# --- counting / quiet path -----------------------------------------------------


def test_quiet_meeting_emits_nothing(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    assert feed(cb, [msg(PM, 1.0), msg(DEV, 2.0)]) == [], (
        "a short exchange under every cap must not trigger the breaker"
    )


def test_non_agent_messages_never_count_toward_caps(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    flood = [msg(SenderClass.HUMAN, float(i)) for i in range(20)] + [
        msg(SenderClass.UNKNOWN, 21.0)
    ]
    assert feed(cb, flood) == [], (
        "human/unknown chatter must never trip the design cap — only agents do"
    )


# --- two-tier handoff (nudge, then a separate add-fallback) ---------------------


def test_soft_cap_nudges_the_pm_only(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    actions = feed(cb, design_run(config.soft_cap))
    assert actions == [Action.NUDGE_HANDOFF], (
        "at the soft cap the PM is nudged — not force-added in the same breath"
    )


def test_handoff_deadline_adds_architect_after_the_nudge(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    actions = feed(cb, design_run(config.soft_cap + config.handoff_deadline))
    assert actions == [Action.NUDGE_HANDOFF, Action.ADD_ARCHITECT], (
        "nudge first; only after the handoff deadline with no Architect do we add it"
    )


def test_nudge_and_add_each_fire_at_most_once(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    actions = feed(cb, design_run(config.hard_cap - 1))
    assert actions.count(Action.NUDGE_HANDOFF) == 1, (
        "the nudge fires once, not every message past the soft cap"
    )
    assert actions.count(Action.ADD_ARCHITECT) == 1, "the add-fallback fires once"


def test_pm_handoff_request_suppresses_the_nudge(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    stream = design_run(config.soft_cap - 1)
    stream.append(msg(PM, float(config.soft_cap), mentions_architect=True))
    assert Action.NUDGE_HANDOFF not in feed(cb, stream), (
        "when the PM invites the Architect, the conductor stays quiet"
    )


def test_mention_without_a_present_architect_still_adds_fallback(
    config: BreakerConfig,
) -> None:
    # Finding 2: a mere @mention does not prove the Architect joined; if the PM's
    # add silently failed, the fallback must still fire (not wait for the hard kill).
    cb = CircuitBreaker(config, start_time=0.0)
    stream = [msg(PM, 1.0, mentions_architect=True)] + design_run(
        config.soft_cap + config.handoff_deadline, start=2
    )
    actions = feed(cb, stream)
    assert Action.ADD_ARCHITECT in actions, (
        "a mention whose add never landed must not suppress the add-fallback"
    )
    assert Action.NUDGE_HANDOFF not in actions, (
        "but the mention does suppress the (now redundant) nudge"
    )


def test_present_architect_suppresses_nudge_and_add(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.note_architect_present()  # conductor confirmed the Architect actually joined
    actions = feed(cb, design_run(config.soft_cap + config.handoff_deadline))
    assert actions == [], (
        "once the Architect is really in the room, neither nudge nor add is needed"
    )


# --- decision signal ------------------------------------------------------------


def test_non_verdict_architect_message_does_not_end_the_meeting(
    config: BreakerConfig,
) -> None:
    # Finding 1: an Architect "let me review this" must not start the grace timer.
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(PM, 1.0, mentions_architect=True))
    cb.record(msg(ARCH, 2.0, is_final_decision=False))
    assert cb.poll(50.0) == [], (
        "a non-verdict Architect message must not terminate the meeting"
    )
    assert cb.stopped is False, (
        "the breaker stays open until an explicit decision (or a cap)"
    )


def test_marked_decision_terminates_after_grace(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(PM, 1.0, mentions_architect=True))
    cb.record(msg(ARCH, 2.0, is_final_decision=True))
    assert cb.poll(10.0) == [], (
        "first poll stamps the decision on the conductor clock, then holds for grace"
    )
    assert cb.poll(10.0 + config.grace_s - 1) == [], (
        "hold through the grace window for trailing acks"
    )
    assert cb.poll(10.0 + config.grace_s) == [Action.TERMINATE_OK], (
        "clean stop once grace elapses"
    )


def test_grace_uses_conductor_clock_not_server_timestamp(config: BreakerConfig) -> None:
    # Finding 4: the decision message's server timestamp is wildly skewed; the grace
    # window must be measured from the conductor's poll time, not that timestamp.
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(ARCH, 9999.0, is_final_decision=True))  # server clock far ahead
    assert cb.poll(100.0) == [], (
        "grace anchors at the conductor's receipt time (100), not the 9999 server stamp"
    )
    assert cb.poll(100.0 + config.grace_s - 1) == [], (
        "still within grace measured from 100"
    )
    assert cb.poll(100.0 + config.grace_s) == [Action.TERMINATE_OK], (
        "terminates one grace after the conductor saw it"
    )


# --- caps vs decision -----------------------------------------------------------


def test_decision_survives_wall_clock_and_hard_cap(config: BreakerConfig) -> None:
    # Finding 3: a meeting that reached a decision is never hard-killed; its tail is
    # bounded by grace (so total may be wall_clock + grace — documented, not "absolute").
    cb = CircuitBreaker(config, start_time=0.0)
    feed(cb, design_run(config.hard_cap - 1))  # right up against the hard cap
    cb.record(msg(ARCH, 500.0, is_final_decision=True))
    assert cb.poll(config.wall_clock_s + 5) == [], (
        "past the wall clock, a decided meeting holds — it is not killed"
    )
    assert cb.poll(config.wall_clock_s + 5 + config.grace_s) == [Action.TERMINATE_OK], (
        "it ends via the grace tail"
    )


def test_hard_cap_kills_a_runaway(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    assert Action.HARD_KILL in feed(cb, design_run(config.hard_cap)), (
        "an agent loop hitting the hard cap is force-killed"
    )


def test_wall_clock_kills_a_slow_meeting(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(PM, 1.0))
    assert cb.poll(config.wall_clock_s) == [Action.HARD_KILL], (
        "the wall-clock ceiling force-kills even under the cap"
    )


def test_no_actions_after_terminal(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    cb.record(msg(PM, 1.0))
    assert cb.poll(config.wall_clock_s) == [Action.HARD_KILL]
    assert cb.stopped is True, "breaker reports it has stopped"
    cb.record(msg(DEV, config.wall_clock_s + 1))
    assert cb.poll(config.wall_clock_s + 5) == [], (
        "a stopped breaker never emits another action"
    )


# --- context-manager guard ------------------------------------------------------


def test_context_manager_closes_the_meeting_on_exit(config: BreakerConfig) -> None:
    cb = CircuitBreaker(config, start_time=0.0)
    with cb as guard:
        assert guard is cb, (
            "entering the guard yields the breaker to drive inside the block"
        )
        cb.record(msg(PM, 1.0))
    assert cb.stopped is True, "leaving the guarded block closes the meeting"
    assert cb.poll(2.0) == [], (
        "a closed breaker emits no further actions — the runaway can't outlive the block"
    )


def test_context_manager_does_not_suppress_exceptions(config: BreakerConfig) -> None:
    with pytest.raises(ValueError):
        with CircuitBreaker(config, start_time=0.0):
            raise ValueError("boom")


# --- interactive open floor -----------------------------------------------------


def test_verdict_opens_the_floor_instead_of_terminating() -> None:
    cb = decided_interactive()
    # decided_interactive already asserts the first poll emitted OPEN_FLOOR; a
    # second poll shortly after must NOT terminate — the presenter has the room.
    assert cb.poll(VERDICT_AT + 1) == [], (
        "an interactive meeting must not close on the verdict"
    )
    assert cb.stopped is False, "the floor stays open for the presenter"


def test_open_floor_opens_only_once() -> None:
    cb = decided_interactive()
    assert cb.poll(VERDICT_AT + 2) == [], (
        "OPEN_FLOOR must fire once, not on every subsequent poll"
    )


def test_presenter_end_phrase_closes_the_floor() -> None:
    cb = decided_interactive()
    cb.record(presenter(VERDICT_AT + 5, end=True))
    assert cb.poll(VERDICT_AT + 5) == [Action.TERMINATE_OK], (
        "the presenter's end phrase ends the meeting"
    )
    assert "presenter ended" in cb.stop_reason


def test_open_floor_terminates_after_presenter_idle() -> None:
    idle = 30.0
    cb = decided_interactive(idle_s=idle)
    # No presenter message: the floor must close exactly one idle window after the verdict.
    assert cb.poll(VERDICT_AT + idle - 1) == [], (
        "still within the idle window — hold the room open"
    )
    assert cb.poll(VERDICT_AT + idle) == [Action.TERMINATE_OK], (
        "presenter silence for the full idle window after the verdict must auto-close"
    )
    assert "idle" in cb.stop_reason


def test_presenter_activity_resets_the_idle_timer() -> None:
    idle = 30.0
    cb = decided_interactive(idle_s=idle)
    # A presenter message inside the original window pushes the deadline out to
    # (spoke_at + idle), proving the timer anchors on presenter activity, not the verdict.
    spoke_at = VERDICT_AT + idle - 5
    cb.record(presenter(spoke_at))
    assert cb.poll(spoke_at) == [], (
        "presenter just spoke — timer resets, room stays open"
    )
    assert cb.poll(spoke_at + idle - 1) == [], (
        "still within the window reset from the last presenter message"
    )
    assert cb.poll(spoke_at + idle) == [Action.TERMINATE_OK], (
        "idle measured from the last presenter message, not from the verdict"
    )


def test_agent_chatter_alone_does_not_keep_the_floor_open() -> None:
    idle = 30.0
    cb = decided_interactive(idle_s=idle)
    # Agents keep talking right up to the deadline, but only presenter input holds the floor.
    for t in (VERDICT_AT + 2, VERDICT_AT + 5, VERDICT_AT + 10, VERDICT_AT + idle - 1):
        cb.record(msg(PM if int(t) % 2 else DEV, t))
        assert cb.poll(t) == [], (
            "agent messages must not reset the presenter idle timer"
        )
    assert cb.poll(VERDICT_AT + idle) == [Action.TERMINATE_OK], (
        "with no presenter input, the floor still closes on idle despite agent chatter"
    )


def test_non_interactive_still_terminates_on_grace() -> None:
    cfg = BreakerConfig(wall_clock_s=100.0, grace_s=20.0)  # interactive defaults False
    cb = CircuitBreaker(cfg, start_time=0.0)
    cb.record(msg(ARCH, 10.0, is_final_decision=True))
    assert cb.poll(10.0) == [], "non-interactive: verdict starts the grace tail"
    assert cb.poll(31.0) == [Action.TERMINATE_OK], (
        "non-interactive (headless/CI) still closes on decision + grace, no open floor"
    )


def test_presenter_can_end_before_any_verdict() -> None:
    # The presenter must be able to stop the meeting at any time — not only during
    # the post-verdict open floor. Here the design is still in progress, no verdict.
    cfg = BreakerConfig(interactive=True, wall_clock_s=100.0, open_floor_idle_s=30.0)
    cb = CircuitBreaker(cfg, start_time=0.0)
    cb.record(msg(PM, 1.0))
    cb.record(msg(DEV, 2.0))
    cb.record(presenter(3.0, end=True))
    assert cb.poll(3.0) == [Action.TERMINATE_OK], (
        "an end phrase ends the meeting mid-discussion, before any verdict"
    )
    assert "presenter ended" in cb.stop_reason
