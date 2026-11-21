"""What CrewAI's model receives from the room's passive roster."""

from __future__ import annotations

import pytest

from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.framework_conformance.batchprobes import FINAL_TEXT, deliver
from tests.framework_conformance.batchprobes.crewai import recording_adapter
from tests.runtime.conftest import make_link_mock
from tests.runtime.helpers import AGENT_ID, ROOM_ID
from tests.runtime.runtimecontrol.kit import never_executes

ROLE = {
    "id": "role",
    "name": "Role",
    "handle": "test/role",
    "type": "Agent",
    "description": "Handles exclusively DESCROLE0DCBBFF7 inquiries",
}
DECOY = {
    "id": "decoy",
    "name": "Decoy",
    "handle": "test/decoy",
    "type": "Agent",
    "description": "Handles exclusively DESCDECOYCB84FDEC inquiries",
}


class TestCrewAIPassiveRoster:
    async def test_each_kickoff_carries_the_exact_current_roster(self) -> None:
        pytest.importorskip("crewai")
        context = ExecutionContext(
            ROOM_ID,
            make_link_mock(participants=[ROLE, DECOY]),
            never_executes,
            agent_id=AGENT_ID,
            config=SessionConfig(
                enable_context_hydration=False, report_turn_failures_to_room=False
            ),
        )
        await context.load_participants()
        adapter, model = await recording_adapter(FINAL_TEXT)
        preprocessor = DefaultPreprocessor()

        async def roster_received(message_id: str) -> list[str]:
            event = make_message_event(room_id=ROOM_ID, msg_id=message_id)
            inp = await preprocessor.process(context, event, AGENT_ID)
            assert inp is not None and inp.commit_context is not None
            model.received.clear()
            await deliver(adapter, inp)
            inp.commit_context()
            return [
                line
                for line in model.provider_input.splitlines()
                if line.startswith("- @")
            ]

        try:
            first_roster = [
                '- @test/role — Role (Agent): "Handles exclusively DESCROLE0DCBBFF7 inquiries"',
                '- @test/decoy — Decoy (Agent): "Handles exclusively DESCDECOYCB84FDEC inquiries"',
            ]
            assert await roster_received("bootstrap") == first_roster
            assert await roster_received("unchanged") == first_roster

            context.add_participant(
                {"id": ROLE["id"], "description": "Handles only lantern repairs"}
            )
            context.remove_participant(DECOY["id"])
            updated_roster = [
                '- @test/role — Role (Agent): "Handles only lantern repairs"'
            ]
            assert await roster_received("updated") == updated_roster
            assert await roster_received("unchanged-after-update") == updated_roster
        finally:
            await adapter.cleanup_all()
