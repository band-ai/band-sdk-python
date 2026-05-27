from __future__ import annotations

import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.event_checker import EventChecker
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

TOOL_PROBES = [
    {
        "message": "List the participants in this chat room.",
        "tool": "thenvoi_get_participants",
        "label": "get_participants",
    },
    {
        "message": "Look up what other agents or users are available on the platform.",
        "tool": "thenvoi_lookup_peers",
        "label": "lookup_peers",
    },
]


class ExecutionEmitScenario(Scenario):
    name = "G: Execution Emit"
    description = "Verify tool_call / tool_result events appear on the platform (Emit.EXECUTION)"

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)
        checker = EventChecker(client)

        known: set[str] = {m["id"] for m in await client.get_messages(room_id)}

        for probe in TOOL_PROBES:
            sent = await client.send_message(room_id, probe["message"], agent)
            if "id" in sent:
                known.add(sent["id"])

            await client.wait_for_agent_activity(
                room_id, agent.agent_id, known, timeout=120.0, settle_time=8.0,
            )

            has_call = await checker.assert_tool_called(room_id, probe["tool"])
            has_result = await checker.assert_tool_result(room_id, probe["tool"])

            result.add_step(
                action=f"Emit check: {probe['label']}",
                expected=f"tool_call + tool_result for {probe['tool']}",
                actual=f"tool_call={has_call}, tool_result={has_result}",
                status=Status.PASS if (has_call and has_result) else Status.FAIL,
            )

        all_events = await checker.get_tool_events(room_id)
        calls = sum(1 for e in all_events if e["message_type"] == "tool_call")
        results = sum(1 for e in all_events if e["message_type"] == "tool_result")
        result.add_step(
            action="Execution event count",
            expected=f">= {len(TOOL_PROBES)} call + {len(TOOL_PROBES)} result events",
            actual=f"tool_call={calls}, tool_result={results}",
            status=Status.PASS if calls >= len(TOOL_PROBES) and results >= len(TOOL_PROBES) else Status.PARTIAL,
        )

        result.finalize()
        return result
