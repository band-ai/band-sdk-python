"""Batch-input probe for the OpenCode adapter; see ``batchprobes``.

The provider here is ``opencode serve``: the probe reads what the adapter
submitted to it, the per-turn system prompt and the user prompt parts.
"""

from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.core.types import AgentInput
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
    event_text_part,
)
from tests.framework_conformance.batchprobes import BatchProbe, deliver
from tests.mcpbackends import backends_created_by
from tests.runtime.helpers import ROOM_ID

SESSION_ID = "sess-1"
MESSAGE_ID = "msg-1"


def submitted_text(client: FakeOpencodeClient) -> str:
    """Everything the server was sent for the turn, system prompt first."""
    (call,) = client.prompt_calls
    return "\n".join([call["system"] or "", *(part["text"] for part in call["parts"])])


async def run_opencode(inp: AgentInput, final_text: str) -> str:
    client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated(SESSION_ID, MESSAGE_ID),
                event_text_part(SESSION_ID, MESSAGE_ID, final_text),
                event_session_idle(SESSION_ID),
            ]
        ]
    )
    with backends_created_by():
        adapter = OpencodeAdapter(client_factory=lambda _config: client)
        await adapter.on_started("OpenCode Agent", "A coding agent")
        try:
            await deliver(adapter, inp)
        finally:
            await adapter.on_cleanup(ROOM_ID)
    return submitted_text(client)


PROBES: dict[str, BatchProbe] = {"opencode": BatchProbe(run=run_opencode, relays=True)}
