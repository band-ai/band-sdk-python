from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

SECRETS = ["ALPHA", "BRAVO", "CHARLIE"]


class ConcurrentRoomsScenario(Scenario):
    name = "I: Concurrent Rooms"
    description = "3 rooms with different secrets — verify no cross-room leakage"

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        rooms: list[tuple[str, AgentInfo]] = []
        for i in range(3):
            rid = await client.create_room()
            info = await client.add_participant(rid, agent.agent_id)
            rooms.append((rid, info))
            result.add_step(
                action=f"Create room {i + 1}",
                expected="Room created and agent added",
                actual=f"room_id={rid}",
                status=Status.PASS,
            )

        await asyncio.sleep(3.0)

        for (rid, info), secret in zip(rooms, SECRETS):
            await client.send_message(
                rid,
                f"Remember this — the secret word for this room is: {secret}",
                info,
            )
            await asyncio.sleep(0.3)

        await asyncio.sleep(15.0)

        for i, ((rid, info), secret) in enumerate(zip(rooms, SECRETS)):
            known: set[str] = {m["id"] for m in await client.get_messages(rid)}

            sent = await client.send_message(
                rid, "What is the secret word for this room?", info,
            )
            if "id" in sent:
                known.add(sent["id"])

            resp = await client.wait_for_agent_activity(
                rid, info.agent_id, known, timeout=120.0, settle_time=8.0,
            )

            if resp:
                content = resp.get("content", "")
                upper = content.upper()
                correct = secret in upper
                leaked = [s for s in SECRETS if s != secret and s in upper]
                if correct and not leaked:
                    status = Status.PASS
                elif correct and leaked:
                    status = Status.PARTIAL
                else:
                    status = Status.FAIL
                result.add_step(
                    action=f"Room {i + 1}: recall secret",
                    expected=f"Contains {secret}, no others",
                    actual=content[:200],
                    status=status,
                )
            else:
                result.add_step(
                    action=f"Room {i + 1}: recall secret",
                    expected=f"Contains {secret}",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )

        result.finalize()
        return result
