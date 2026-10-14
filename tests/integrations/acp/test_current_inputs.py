"""Every ACP prompt carries each current input's own sender, never one requester."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.integrations.acp.client_adapter import SENDER_METADATA_INSTRUCTION
from tests.framework_conformance.batchprobes import (
    ALICE_SECOND,
    BATCH,
    HELPER,
    current_records,
    deliver,
    delivery_input,
)
from tests.framework_conformance.batchprobes.acp import ADAPTERS
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import started_acp_adapter

LATER_TURN = (HELPER, ALICE_SECOND)


@pytest.mark.parametrize("framework_id", sorted(ADAPTERS))
class TestACPCurrentInputs:
    async def test_the_bootstrap_and_a_later_turn_each_show_their_inputs_senders(
        self, framework_id: str
    ) -> None:
        agent = FakeACPAgent().will_say("ok")

        async with started_acp_adapter(ADAPTERS[framework_id](), agent) as session:
            await deliver(session.adapter, await delivery_input(BATCH))
            later = await delivery_input(LATER_TURN)
            await deliver(session.adapter, replace(later, is_session_bootstrap=False))

        bootstrap_prompt, later_prompt = agent.prompt_texts()
        assert current_records(bootstrap_prompt) == [o.record for o in BATCH]
        assert current_records(later_prompt) == [o.record for o in LATER_TURN]

    async def test_a_later_singleton_turn_still_identifies_its_sender(
        self, framework_id: str
    ) -> None:
        agent = FakeACPAgent().will_say("ok")

        async with started_acp_adapter(ADAPTERS[framework_id](), agent) as session:
            await deliver(session.adapter, await delivery_input(BATCH))
            later = await delivery_input((HELPER,))
            await deliver(session.adapter, replace(later, is_session_bootstrap=False))

        assert current_records(agent.prompt_texts()[-1]) == [HELPER.record]

    async def test_the_session_context_points_at_the_inputs_instead_of_one_requester(
        self, framework_id: str
    ) -> None:
        agent = FakeACPAgent().will_say("ok")

        async with started_acp_adapter(ADAPTERS[framework_id](), agent) as session:
            await deliver(session.adapter, await delivery_input(BATCH))
            later = await delivery_input(LATER_TURN)
            await deliver(session.adapter, replace(later, is_session_bootstrap=False))

        bootstrap_prompt, later_prompt = agent.prompt_texts()
        assert bootstrap_prompt.count(SENDER_METADATA_INSTRUCTION) == 1
        assert SENDER_METADATA_INSTRUCTION not in later_prompt
        assert "Current requester" not in bootstrap_prompt + later_prompt
