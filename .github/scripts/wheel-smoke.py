#!/usr/bin/env python
"""Smoke-test the band wheel in an isolated, extras-free install.

Run with that install's interpreter: a script's own directory, not the
checkout, heads ``sys.path``, so ``band`` and ``band_sdk_core`` resolve to the
installed wheels. Every ``band_sdk_core`` symbol band calls is exercised here,
proving the pinned native wheel is callable, not just importable.
"""

from __future__ import annotations

import logging

from band_sdk_core import (
    AgentFailure,
    ClaimOutcome,
    ClaimRegistry,
    DecisionRegistry,
    ParticipantRoster,
    RetryTracker,
    Session,
    SessionPolicy,
    evaluate_adapter_result,
    evaluate_delivery_event,
    evaluate_drain_candidate,
    evaluate_next_message,
    is_authorized_sender,
    is_self_echo,
)

logger = logging.getLogger(__name__)


def main() -> None:
    registry = ClaimRegistry()
    assert registry.try_claim("room", "message") is True
    decisions = DecisionRegistry(max_pending=1)
    token, ticket = decisions.register_keyed("ask-1")
    assert decisions.try_claim(token, ticket) is ClaimOutcome.Claimed
    assert decisions.unclaimed_count() == 0
    assert decisions.cancel_all().claimed == ["ask-1"]
    assert is_authorized_sender(frozenset({"alice"}), "alice") is True
    tracker = RetryTracker()
    assert tracker.max_retries == 1
    roster = ParticipantRoster()
    assert roster.list() == []
    session = Session(SessionPolicy.default())
    assert session.begin_attempt(0.0) == 0
    assert is_self_echo("agent-1", "Agent", "agent-1") is True
    assert evaluate_drain_candidate(None, [], "agent-1") == {"decision": "no_candidate"}
    assert evaluate_delivery_event("room_added", "room-1", {}, "agent-1") == {
        "decision": "ignored",
        "event_type": "room_added",
    }
    assert evaluate_next_message("msg-1", None) == {"decision": "no_pending"}
    assert evaluate_adapter_result("room-1", "msg-1", True) == {
        "decision": "processed",
        "room_id": "room-1",
        "message_id": "msg-1",
    }
    failure = AgentFailure("wheel-smoke", "failure")
    assert failure.to_dict() == {
        "provider": "wheel-smoke",
        "code": None,
        "message": "failure",
        "detail": None,
    }
    logger.info("Isolated wheel smoke passed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
