from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)


class MultiParticipantScenario(Scenario):
    name = "D: Multi-Participant"
    description = "Test with two agents in the same room"

    def __init__(
        self,
        second_agent_id: str,
        second_example_file: str,
        second_runner: AgentRunner | None = None,
    ) -> None:
        self.second_agent_id = second_agent_id
        self.second_example_file = second_example_file
        self.second_runner = second_runner

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        try:
            new_room_id = await client.create_room()
            result.add_step(
                action="Create multi-participant room",
                expected="Room created",
                actual=f"room_id={new_room_id}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Failed to create room: {e}")
            return result

        try:
            agent1_info = await client.add_participant(new_room_id, agent.agent_id)
            result.add_step(
                action="Add first agent",
                expected="Agent 1 added",
                actual=f"Added {agent1_info.name}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Failed to add first agent: {e}")
            return result

        try:
            agent2_info = await client.add_participant(new_room_id, self.second_agent_id)
            result.add_step(
                action="Add second agent",
                expected="Agent 2 added",
                actual=f"Added {agent2_info.name}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Failed to add second agent: {e}")
            return result

        await asyncio.sleep(3.0)

        known_ids: set[str] = set()
        messages = await client.get_messages(new_room_id)
        known_ids.update(m["id"] for m in messages)

        agent1_name = agent1_info.name or "Agent1"
        agent2_name = agent2_info.name or "Agent2"

        try:
            mention1 = {"id": agent1_info.agent_id}
            if agent1_info.handle:
                mention1["handle"] = agent1_info.handle
            if agent1_info.name:
                mention1["name"] = agent1_info.name

            mention2 = {"id": agent2_info.agent_id}
            if agent2_info.handle:
                mention2["handle"] = agent2_info.handle
            if agent2_info.name:
                mention2["name"] = agent2_info.name

            content = f"@{agent1_name} please say hello, and @{agent2_name} please introduce yourself."
            resp = await client._client.post(
                f"/api/v1/me/chats/{new_room_id}/messages",
                json={
                    "message": {
                        "content": content,
                        "mentions": [mention1, mention2],
                    }
                },
            )
            resp.raise_for_status()
            send_data = resp.json()["data"]
            if "id" in send_data:
                known_ids.add(send_data["id"])

            got_agent1 = False
            got_agent2 = False
            for _ in range(60):
                await asyncio.sleep(2.0)
                msgs = await client.get_messages(new_room_id)
                for m in msgs:
                    if m["id"] not in known_ids and m.get("sender_type") == "Agent":
                        known_ids.add(m["id"])
                        if m.get("sender_id") == agent1_info.agent_id:
                            got_agent1 = True
                        if m.get("sender_id") == agent2_info.agent_id:
                            got_agent2 = True
                if got_agent1 and got_agent2:
                    break

            result.add_step(
                action="Both agents respond to multi-mention",
                expected="Both agents respond",
                actual=f"Agent1={got_agent1}, Agent2={got_agent2}",
                status=Status.PASS if (got_agent1 and got_agent2) else Status.PARTIAL,
            )
        except Exception as e:
            result.add_step(
                action="Multi-participant message",
                expected="Both agents respond",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        result.finalize()
        return result
