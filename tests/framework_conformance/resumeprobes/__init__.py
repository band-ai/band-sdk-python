"""The shape every resume-record probe shares.

A probe runs one seeding turn (the first prompt of a fresh session) through an
adapter's real ``on_event`` against the ``FakeAgentTools`` it is handed, with
every narration kind silenced (``emit=()``), so the resume record is the only
task event the turn can post. The scripted agent either answers the prompt
with a Band reply or fails it.

Framework imports stay inside each probe function: not every lane's venv has
every framework installed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from band.runtime.sessionresume import ResumeRecord
from band.testing.fake_tools import FakeAgentTools


class SeedOutcome(StrEnum):
    """How the agent answers a fresh session's seeding prompt."""

    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class ResumeProbe:
    """``seed`` runs one seeding turn ending in the given outcome; a failed
    seed raises the error ``seed_failure`` names out of the turn."""

    record: ResumeRecord
    seed: Callable[[SeedOutcome, FakeAgentTools], Awaitable[None]]
    seed_failure: Callable[[], type[BaseException]]
