"""Verify generated sign-in, review, duplicate and sign-out sequences."""

from __future__ import annotations

from typing import Any

from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

from tests.teams.lifecycle import Lifecycle


class TestLifecycle:
    def test_generated_sequences_preserve_confirmation_outcomes(
        self, tmp_path: Any, person: Any, origin: Any
    ) -> None:
        run_state_machine_as_test(
            lambda: Lifecycle(tmp_path, person, origin),
            settings=settings(max_examples=30, stateful_step_count=25, deadline=None),
        )
