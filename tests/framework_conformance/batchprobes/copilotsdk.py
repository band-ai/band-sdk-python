"""Batch-input probe for the Copilot SDK adapter; see ``batchprobes``.

What reaches the provider is every prompt sent to the room's Copilot session.
"""

from __future__ import annotations

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.core.types import AgentInput
from tests.adapters.copilot_sdk.fakes import FakeCopilotClient, make_started_adapter
from tests.framework_conformance.batchprobes import BatchProbe, deliver


async def run_copilot_sdk(inp: AgentInput, final_text: str) -> str:
    client = FakeCopilotClient(reply_content=final_text)
    adapter = await make_started_adapter(client)
    await deliver(adapter, inp)
    return "\n".join(
        prompt for session in client.sessions for prompt in session.prompts
    )


PROBES: dict[str, BatchProbe] = (
    {"copilot_sdk": BatchProbe(run=run_copilot_sdk, relays=True)}
    if _COPILOT_SDK_AVAILABLE
    else {}
)
