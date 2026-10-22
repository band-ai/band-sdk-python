"""Generated lifecycle sequences preserve the single owned child."""

from __future__ import annotations

import asyncio

from hypothesis import Phase, given, settings

from tests.contracts.lifecycle.kit import Step, play, sequences


class TestLifecycleModel:
    @settings(max_examples=25, deadline=None, phases=(Phase.generate,))
    @given(steps=sequences)
    def test_generated_sequences_never_detach_a_child(self, steps: list[Step]) -> None:
        asyncio.run(play(steps))
