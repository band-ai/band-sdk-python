from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.core.types import Emit
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_message_updated_with_tokens,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)
from tests.adapters.usage_events import recorded_usage_payloads


class TestUsage:
    async def test_emits_turn_usage_folding_reasoning_into_output(
        self, make_adapter, tools
    ) -> None:
        """Emit.USAGE aggregates the assistant message's ``tokens``, folding
        OpenCode's disjoint ``reasoning`` count into ``output_tokens``."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated_with_tokens(
                        "sess-1",
                        "msg-1",
                        {
                            "input": 10,
                            "output": 5,
                            "reasoning": 3,
                            "cache": {"read": 1, "write": 2},
                        },
                    ),
                    event_text_part("sess-1", "msg-1", "done"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
            emit=Emit.USAGE,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert recorded_usage_payloads(tools) == [
            {
                "input_tokens": 10,
                "output_tokens": 8,
                "cache_read_tokens": 1,
                "cache_write_tokens": 2,
            }
        ]
