from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tests.runtime.decisionanswering.lifecycle import AnsweringRoom, Step


class TestDecisionAnswerLifetime:
    @given(
        steps=st.lists(
            st.tuples(st.sampled_from(Step), st.sampled_from(("first", "second"))),
            min_size=1,
            max_size=20,
        )
    )
    async def test_claims_remain_owned_until_delivery_finishes(
        self, steps: list[tuple[Step, str]]
    ) -> None:
        room = AnsweringRoom()
        try:
            for step, token in steps:
                await room.take(step, token)
        finally:
            await room.close()
