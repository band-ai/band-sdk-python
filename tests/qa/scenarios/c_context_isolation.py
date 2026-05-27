from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)


class ContextIsolationScenario(Scenario):
    name = "C: Context Isolation"
    description = "Create a new room, verify no context leaks from previous conversations"

    # Topics from Scenario A that should NOT appear in the new room.
    # The Scenario B secret word is added dynamically via shared context.
    _LEAK_MARKERS = ["france", "paris", "marseille"]

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
        shared: dict | None = None,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        # Collect leak markers: Scenario B's secret word + well-known A topics
        leak_markers = list(self._LEAK_MARKERS)
        b_secret = (shared or {}).get("b_secret")
        if b_secret and b_secret.lower() not in leak_markers:
            leak_markers.append(b_secret.lower())

        try:
            new_room_id = await client.create_room()
            result.add_step(
                action="Create new chat room",
                expected="New room created",
                actual=f"room_id={new_room_id}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Failed to create new room: {e}")
            return result

        try:
            agent_info = await client.add_participant(new_room_id, agent.agent_id)
            result.add_step(
                action="Add agent to new room",
                expected="Agent added as participant",
                actual=f"Added {agent_info.name or agent_info.agent_id}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Failed to add agent: {e}")
            return result

        await asyncio.sleep(2.0)

        known_ids: set[str] = set()
        messages = await client.get_messages(new_room_id)
        known_ids.update(m["id"] for m in messages)

        # Ask the agent to recall prior conversations.  A properly
        # isolated agent has no cross-room memory and should say so.
        # If the response contains any leak marker (Scenario A/B
        # content), that's a context isolation failure.
        try:
            send_resp = await client.send_message(
                new_room_id,
                "Summarize everything we've discussed so far.",
                agent,
            )
            if "id" in send_resp:
                known_ids.add(send_resp["id"])

            response = await client.wait_for_agent_activity(
                new_room_id, agent.agent_id, known_ids,
                timeout=120.0, settle_time=8.0,
            )

            if response:
                known_ids.add(response["id"])
                content = response.get("content", "").lower()
                leaked = [w for w in leak_markers if w in content]
                if leaked:
                    result.add_step(
                        action="Ask about previous conversation",
                        expected="No content from other rooms",
                        actual=f"LEAK detected: {', '.join(leaked)} | {response.get('content', '')[:150]}",
                        status=Status.FAIL,
                    )
                else:
                    result.add_step(
                        action="Ask about previous conversation",
                        expected="No content from other rooms",
                        actual=response.get("content", "")[:200],
                        status=Status.PASS,
                    )
            else:
                result.add_step(
                    action="Ask about previous conversation",
                    expected="Agent responds with no prior context",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )
        except Exception as e:
            result.add_step(
                action="Ask about previous conversation",
                expected="Agent responds",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        try:
            send_resp = await client.send_message(
                new_room_id,
                "What is 2 + 2?",
                agent,
            )
            if "id" in send_resp:
                known_ids.add(send_resp["id"])

            response = await client.wait_for_agent_activity(
                new_room_id, agent.agent_id, known_ids,
                timeout=120.0, settle_time=8.0,
            )

            if response:
                known_ids.add(response["id"])
                content = response.get("content", "").lower()
                has_4 = "4" in content or "four" in content
                result.add_step(
                    action="Normal question in new room",
                    expected="Agent answers correctly (4)",
                    actual=response.get("content", "")[:200],
                    status=Status.PASS if has_4 else Status.FAIL,
                )
            else:
                result.add_step(
                    action="Normal question in new room",
                    expected="Agent answers",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )
        except Exception as e:
            result.add_step(
                action="Normal question in new room",
                expected="Agent answers",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        result.finalize()
        return result
