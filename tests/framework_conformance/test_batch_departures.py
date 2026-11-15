"""Automatic adapter replies use the admitted audience and current membership."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from band.testing import FakeAgentTools, failure_reports
from tests.framework_conformance.batchprobes import (
    BATCH,
    FINAL_TEXT,
    HELPER,
    delivery_input,
    posted_messages,
)
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES
from tests.runtime.helpers import AGENT_ID, ROOM_ID

MIGRATED = [
    name
    for name in ("letta", "copilot_sdk", "opencode", "parlant")
    if name in BATCH_PROBES
]
AGENT_MEMBER = {
    "id": AGENT_ID,
    "name": "SDK Agent",
    "type": "Agent",
    "role": "member",
    "status": "active",
}
HELPER_MEMBER = {
    "id": HELPER.sender_id,
    "name": HELPER.sender_name,
    "type": HELPER.sender_type,
    "role": "member",
    "status": "active",
}


class TestBatchDepartures:
    @pytest.mark.parametrize("framework_id", MIGRATED)
    async def test_only_the_remaining_original_sender_receives_final_text(
        self, framework_id: str
    ) -> None:
        inp = replace(
            await delivery_input(BATCH),
            tools=FakeAgentTools(
                room_id=ROOM_ID, participants=[HELPER_MEMBER, AGENT_MEMBER]
            ),
        )
        await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)
        assert [post["mentions"] for post in posted_messages(inp)] == [
            [{"id": HELPER.sender_id}]
        ]
        assert failure_reports(cast(FakeAgentTools, inp.tools)) == []

    @pytest.mark.parametrize("framework_id", MIGRATED)
    @pytest.mark.parametrize(
        "final_text", [FINAL_TEXT, ""], ids=["visible-final", "silent-final"]
    )
    async def test_successful_provider_completion_settles_when_every_sender_left(
        self, framework_id: str, final_text: str
    ) -> None:
        tools = FakeAgentTools(room_id=ROOM_ID, participants=[AGENT_MEMBER])
        inp = replace(await delivery_input(BATCH), tools=tools)
        await BATCH_PROBES[framework_id].run(inp, final_text)
        assert posted_messages(inp) == []
        assert tools.turn.complete
        assert not tools.turn.replied
        assert failure_reports(tools) == []
