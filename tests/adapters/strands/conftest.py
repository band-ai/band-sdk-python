"""Strands fixtures load their optional framework only when requested."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

import pytest

from band.testing.fake_tools import FakeAgentTools

if TYPE_CHECKING or find_spec("strands") is not None:
    from band.adapters.strands import StrandsAdapter
    from band.testing.strands import ScriptedStrandsModel, ScriptedTurn
    from tests.adapters.strands.kit import ROOM


@pytest.fixture
def tools() -> FakeAgentTools:
    return FakeAgentTools(room_id=ROOM)


@pytest.fixture
def scripted() -> Callable[..., Awaitable[StrandsAdapter]]:
    async def build(
        *turns: ScriptedTurn,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **adapter_args: Any,
    ) -> StrandsAdapter:
        adapter = StrandsAdapter(
            llm=ScriptedStrandsModel(
                turns, input_tokens=input_tokens, output_tokens=output_tokens
            ),
            **adapter_args,
        )
        await adapter.on_started("Bot", "A bot")
        return adapter

    return build
