from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
    StrandsAdapterConfig,
)
from band.testing import (
    ScriptedStrandsModel,
)
from tests.adapters.strands.kit import SEND_TURN, _turn_agent


class TestModelSource:
    @pytest.mark.asyncio
    async def test_turn_agent_runs_the_configured_model_id(self):
        adapter = StrandsAdapter(StrandsAdapterConfig(model="my-bedrock-id"))

        agent = await _turn_agent(adapter)

        assert agent.model.config["model_id"] == "my-bedrock-id"

    @pytest.mark.asyncio
    async def test_turn_agent_runs_the_live_llm(self):
        llm = ScriptedStrandsModel([SEND_TURN])
        adapter = StrandsAdapter(llm=llm)

        agent = await _turn_agent(adapter)

        assert agent.model is llm

    @pytest.mark.parametrize(
        ("config", "llm"),
        [
            (None, None),
            (StrandsAdapterConfig(model="m"), ScriptedStrandsModel([SEND_TURN])),
        ],
        ids=["neither", "both"],
    )
    def test_requires_exactly_one_model_source(self, config, llm):
        with pytest.raises(ValueError, match="Set exactly one of config.model or llm"):
            StrandsAdapter(config, llm=llm)
