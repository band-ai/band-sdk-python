from __future__ import annotations

import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

CONVERSATION_STEPS = [
    {
        "label": "Greeting",
        "message": "Hello! What can you do? Please introduce yourself.",
        "expect": "Agent responds with capabilities",
        "check": lambda text: len(text) > 20,
    },
    {
        "label": "Domain question",
        "message": "What is the capital of France?",
        "expect": "Agent answers Paris",
        "check": lambda text: "paris" in text.lower(),
    },
    {
        "label": "Follow-up (context)",
        "message": "And what about the second-largest city there?",
        "expect": "Agent references France context",
        "check": lambda text: len(text) > 10,
    },
    {
        "label": "List participants",
        "message": "Can you list who is in this chat room?",
        "expect": "Agent uses thenvoi_get_participants or describes participants",
        "check": lambda text: len(text) > 10,
    },
    {
        "label": "Lookup peers",
        "message": "Can you look up what other agents or users are available on the platform?",
        "expect": "Agent uses thenvoi_lookup_peers or responds about peers",
        "check": lambda text: len(text) > 10,
    },
    {
        "label": "Goodbye",
        "message": "Thanks for the help, goodbye!",
        "expect": "Agent responds with farewell",
        "check": lambda text: len(text) > 5,
    },
]


class BasicConversationScenario(Scenario):
    name = "A: Basic Conversation"
    description = "6-message conversation testing greetings, domain knowledge, context retention, and platform tools"

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
        shared: dict | None = None,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        known_ids: set[str] = set()
        initial_messages = await client.get_messages(room_id)
        known_ids.update(m["id"] for m in initial_messages)

        for step in CONVERSATION_STEPS:
            try:
                send_resp = await client.send_message(room_id, step["message"], agent)
                if "id" in send_resp:
                    known_ids.add(send_resp["id"])

                response = await client.wait_for_agent_activity(
                    room_id, agent.agent_id, known_ids,
                    timeout=120.0, settle_time=8.0,
                )

                if response is None:
                    result.add_step(
                        action=f"Send: {step['label']}",
                        expected=step["expect"],
                        actual="NO RESPONSE (timeout)",
                        status=Status.FAIL,
                    )
                    continue

                known_ids.add(response["id"])
                content = response.get("content", "")
                passed = step["check"](content)

                result.add_step(
                    action=f"Send: {step['label']}",
                    expected=step["expect"],
                    actual=content[:200],
                    status=Status.PASS if passed else Status.FAIL,
                )

            except Exception as e:
                result.add_step(
                    action=f"Send: {step['label']}",
                    expected=step["expect"],
                    actual=f"ERROR: {e}",
                    status=Status.FAIL,
                )

        result.finalize()
        return result
