"""History visibility outcome types for mention-scoped context probes.

Band scopes an agent's context to its own conversation: delivery is
mention-only, and the context endpoint rehydrates only what the agent said
or what was said to it — the designed privacy boundary. Turns between the
user and other agents are outside that scope. Probes that plant an
unaddressed seed and ask the reader to echo a token or declare blindness use
these outcome types; the full choreography lives in the test (or a harness-
specific driver) that owns attach timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tests.e2e.baseline.toolkit.observations import Replies


class SeedAuthor(Enum):
    """Who authors the token turn: the driver user (addressed to the peer,
    ambient to the reader) or the peer agent itself."""

    USER = "user"
    PEER = "peer"


class Timing(Enum):
    """When the seed lands relative to the reader's attach."""

    LIVE = "live"  # the reader was already attached — an ambient turn
    PRE_ATTACH = "pre-attach"  # before attach — visible only via rehydration


@dataclass(frozen=True)
class Seed:
    """One declared cell of a visibility matrix."""

    author: SeedAuthor
    planted: Timing

    def __str__(self) -> str:
        return f"{self.author.value}-{self.planted.value}"


@dataclass(frozen=True)
class VisibilityOutcome:
    """The reader's own account of what it can see, plus the transcript.

    Exactly one of the three predicates holds for a completed probe:
    ``saw_seed`` (the unaddressed turn leaked into the reader's context),
    ``declared_blind`` (the reader processed the probe and the boundary held),
    or neither (silence or an off-script reply — a delivery or
    prompt-compliance problem, which is not a visibility verdict).
    """

    seed: Seed
    reader_name: str
    token: str
    escape: str
    replies: Replies

    @property
    def saw_seed(self) -> bool:
        return any(self.token in (m.content or "") for m in self.replies)

    @property
    def declared_blind(self) -> bool:
        return not self.saw_seed and any(
            self.escape in (m.content or "") for m in self.replies
        )

    def assert_seed_invisible(self) -> None:
        """The mention-scoped context boundary held: the reader processed the
        probe and declared blindness. A token echo is a leak; a missing or
        off-script reply proves nothing and fails as no-verdict."""
        cell = f"{self.seed} seed for reader {self.reader_name}"
        if self.saw_seed:
            raise AssertionError(
                f"{cell}: the reader echoed the token from a turn that was "
                f"never addressed to it — the mention-scoped context boundary "
                f"leaked; it said: "
                + " | ".join((m.content or "")[:120] for m in self.replies)
            )
        if not self.replies:
            raise AssertionError(f"{cell}: the reader never replied to the probe")
        assert self.declared_blind, (
            f"{cell}: the reader replied without the token or the escape "
            f"marker — no visibility verdict; it said: "
            + " | ".join((m.content or "")[:120] for m in self.replies)
        )
