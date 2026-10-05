"""Matrix scenario: a turn that did nothing is reported once and marked FAILED.

The agent has no ``band_send_message`` or ``band_no_reply`` and a prompt that
makes it read the roster and stop, so its turn can neither reply nor decline.
The SDK reports the missing reply as exactly one ``error`` event carrying
band-sdk-core's text, and the delivery ends FAILED instead of a false
PROCESSED.
"""

from __future__ import annotations

import band_sdk_core
import pytest

from band.client.streaming import DeliveryStatus
from band.core.types import AdapterFeatures, MessageType
from band.runtime.tools import BandTool
from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import silent_turn_prompt
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The model could still decline through band_no_reply, so the turn can't be
# made silent; the verdict rows in test_turn_outcome.py cover these adapters.
IGNORES_EXCLUDE_TOOLS = "ignores exclude_tools, so band_no_reply stays callable"


@per_adapter(
    runs_tool_loop=True,
    prompt=silent_turn_prompt(),
    features=AdapterFeatures(exclude_tools={BandTool.SEND_MESSAGE, BandTool.NO_REPLY}),
    exclude=[
        ExcludedAdapter(
            Adapter.COPILOT_SDK,
            "relays the model's final text, so an empty turn can't be scripted reliably",
        ),
        *(
            ExcludedAdapter(adapter, IGNORES_EXCLUDE_TOOLS)
            for adapter in (
                Adapter.ANTHROPIC,
                Adapter.CLAUDE_SDK,
                Adapter.GEMINI,
                Adapter.GOOGLE_ADK,
            )
        ),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_a_turn_that_did_nothing_is_reported_and_failed(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    room_id = await resource_manager.provision_room(
        title=f"e2e-missing-reply-{agent.adapter_id}", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "Run the silent-turn check.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        reached = await capture.wait_for_delivery(
            mid, agent.id, until={DeliveryStatus.FAILED, DeliveryStatus.PROCESSED}
        )
        errors = await capture.errors(sender_id=agent.id)
        messages = await capture.events(MessageType.TEXT, sender_id=agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    assert reached is DeliveryStatus.FAILED, (
        "expected a silent turn, but the agent completed it with "
        f"tools {[call.name for call in calls]} and messages "
        f"{[message.content for message in messages]}"
    )
    assert [error.content for error in errors] == [
        band_sdk_core.missing_reply_message()
    ]
    messages.assert_none()
