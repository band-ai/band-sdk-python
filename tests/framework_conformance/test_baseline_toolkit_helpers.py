"""PR-run unit tests for baseline-toolkit helpers ported from add-band.

Pure logic that would otherwise be skipped under ``tests/e2e/**`` (E2E-gated),
so it lives here to run on every PR — no platform, no keys.
"""

from __future__ import annotations

import pytest
from band.client.streaming import MessageCreatedPayload

from tests.e2e.baseline.toolkit.exchange import (
    MentionChainStep,
    chain_completed,
    marker,
)
from tests.e2e.baseline.toolkit.observations import Replies
from tests.e2e.baseline.toolkit.visibility import (
    Seed,
    SeedAuthor,
    Timing,
    VisibilityOutcome,
)


def _reply(sender_id: str, content: str) -> MessageCreatedPayload:
    now = "2026-01-01T00:00:00Z"
    return MessageCreatedPayload(
        id="m",
        content=content,
        message_type="text",
        sender_id=sender_id,
        sender_type="Agent",
        inserted_at=now,
        updated_at=now,
    )


class _Agent:
    def __init__(self, agent_id: str, name: str) -> None:
        self.id = agent_id
        self.name = name


def test_marker_is_run_scoped() -> None:
    assert marker("abc") == "E2E-MARK-ABC"
    assert marker("abc", prefix="PA-MARK") == "PA-MARK-ABC"


def test_chain_completed_requires_all_steps() -> None:
    token = marker("run-1")
    asker = _Agent("a", "Asker")
    responder = _Agent("b", "Responder")
    steps = (
        MentionChainStep(asker),
        MentionChainStep(responder),
        MentionChainStep(asker),
    )
    incomplete = [
        _reply("a", f"ask {token}"),
        _reply("b", f"answer {token}"),
    ]
    complete = incomplete + [_reply("a", f"relay {token}")]
    assert chain_completed(steps, token, incomplete) is False
    assert chain_completed(steps, token, complete) is True


def test_visibility_outcome_declared_blind() -> None:
    outcome = VisibilityOutcome(
        seed=Seed(SeedAuthor.USER, Timing.LIVE),
        reader_name="reader",
        token="PA-VIS-TOKEN",
        escape="UNKNOWN-ABC",
        replies=Replies([_reply("reader", "UNKNOWN-ABC")]),
    )
    assert outcome.saw_seed is False
    assert outcome.declared_blind is True
    outcome.assert_seed_invisible()


def test_visibility_outcome_seed_echo_is_leak() -> None:
    outcome = VisibilityOutcome(
        seed=Seed(SeedAuthor.PEER, Timing.PRE_ATTACH),
        reader_name="reader",
        token="PA-VIS-TOKEN",
        escape="UNKNOWN-ABC",
        replies=Replies([_reply("reader", "PA-VIS-TOKEN leaked")]),
    )
    with pytest.raises(AssertionError, match="leaked"):
        outcome.assert_seed_invisible()
