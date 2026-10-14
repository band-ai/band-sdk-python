"""BuiltinToolResults behavior for Pydantic AI."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import BinaryContent, ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.core.types import Capability, MessageType, ToolEventKey
from band.runtime.tools import BandTool, band_tool_errored
from band.testing import FakeAgentTools, events_of_type
from tests.adapters.pydanticai.kit import (
    _call_tool,
    _started_adapter,
    _streamed_tool_calls,
    _tool_returns,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.testing.support import seeded_peer


class TestBuiltinToolResults:
    """What a finished built-in tool call hands back to the model."""

    @pytest.mark.asyncio
    async def test_add_participant_result_event_reads_as_plain_data(self):
        """The tool_result event renders the result with str(), so a status
        must read as its value, not as an enum member's repr."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"))
        await adapter.on_started("Probe", "probe")
        adapter._agent.model = _streamed_tool_calls(
            (BandTool.ADD_PARTICIPANT, {"identifier": "bob"})
        )
        tools = FakeAgentTools(
            room_id=ROOM_ID, peers=[seeded_peer("bob-id", handle="bob", name="Bob")]
        )

        await adapter.on_event(turn_input(tools))

        [event] = events_of_type(tools, MessageType.TOOL_RESULT)
        output = json.loads(event["content"])[ToolEventKey.OUTPUT]
        assert "'status': 'added'" in output

    @pytest.mark.asyncio
    async def test_pydantic_results_are_serialized(self):
        class Peer(BaseModel):
            id: str
            handle: str

        deps = MagicMock()
        deps.lookup_peers = AsyncMock(return_value=Peer(id="p-1", handle="@ann"))
        adapter = await _started_adapter()

        result = await _call_tool(adapter, deps, BandTool.LOOKUP_PEERS, {})

        assert _tool_returns(result) == [{"id": "p-1", "handle": "@ann"}]

    @pytest.mark.asyncio
    async def test_backend_failure_becomes_an_llm_readable_error(self):
        deps = MagicMock()
        deps.send_message = AsyncMock(side_effect=RuntimeError("backend down"))
        adapter = await _started_adapter()

        result = await _call_tool(
            adapter, deps, BandTool.SEND_MESSAGE, {"content": "hi", "mentions": []}
        )

        (content,) = _tool_returns(result)
        assert "backend down" in content
        # The wording is a contract with band_tool_errored, which the adapter
        # reads to tell a failed Band tool from productive work.
        assert band_tool_errored(BandTool.SEND_MESSAGE, content)

    @pytest.mark.asyncio
    async def test_read_room_file_image_becomes_binary_content(self):
        deps = MagicMock()
        deps.read_room_file = AsyncMock(
            return_value={
                "content": [
                    {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}
                ]
            }
        )
        adapter = await _started_adapter(capabilities=Capability.FILES)

        result = await _call_tool(
            adapter, deps, BandTool.READ_ROOM_FILE, {"file_id": "file-1"}
        )

        ((binary,),) = _tool_returns(result)
        assert isinstance(binary, BinaryContent)
        assert binary.data == b"fake"
        assert binary.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_malformed_image_block_becomes_an_llm_readable_error(self):
        """A block that fails to decode is a tool error, not a crashed run."""
        deps = MagicMock()
        deps.read_room_file = AsyncMock(
            return_value={
                "content": [
                    {
                        "type": "image",
                        "data": "not-valid-base64!!",
                        "mimeType": "image/png",
                    }
                ]
            }
        )
        adapter = await _started_adapter(capabilities=Capability.FILES)

        result = await _call_tool(
            adapter, deps, BandTool.READ_ROOM_FILE, {"file_id": "file-1"}
        )

        (content,) = _tool_returns(result)
        assert band_tool_errored(BandTool.READ_ROOM_FILE, content)

    @pytest.mark.asyncio
    async def test_non_image_file_result_passes_through(self):
        deps = MagicMock()
        deps.read_room_file = AsyncMock(
            return_value={"name": "report.txt", "text": "hello world"}
        )
        adapter = await _started_adapter(capabilities=Capability.FILES)

        result = await _call_tool(
            adapter, deps, BandTool.READ_ROOM_FILE, {"file_id": "file-1"}
        )

        assert _tool_returns(result) == [{"name": "report.txt", "text": "hello world"}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "room_event_error",
        [None, RuntimeError("room offline")],
        ids=["room_notified", "room_event_also_fails"],
    )
    async def test_contact_request_failure_reaches_the_room(self, room_event_error):
        """A contact request the agent failed to answer is invisible in the room
        otherwise — it looks exactly like one that never arrived. Reporting it
        is best-effort, so a failed room event must not change the tool's own
        error output."""
        deps = MagicMock()
        deps.respond_contact_request = AsyncMock(side_effect=RuntimeError("nope"))
        deps.send_event = AsyncMock(side_effect=room_event_error)
        adapter = await _started_adapter(capabilities=Capability.CONTACTS)

        result = await _call_tool(
            adapter,
            deps,
            BandTool.RESPOND_CONTACT_REQUEST,
            {"action": "approve", "handle": "@ann"},
        )

        (content,) = _tool_returns(result)
        assert "nope" in content
        deps.send_event.assert_called_once_with(content, MessageType.ERROR)

    @pytest.mark.asyncio
    async def test_contact_request_success_does_not_report_a_normalization_failure(
        self,
    ):
        """A response that succeeded must not be reported to the room as
        failed just because shaping its result afterward blew up."""
        deps = MagicMock()
        approved = MagicMock()
        approved.model_dump.side_effect = TypeError("boom during serialization")
        deps.respond_contact_request = AsyncMock(return_value=approved)
        deps.send_event = AsyncMock(return_value={"status": "sent"})
        adapter = await _started_adapter(capabilities=Capability.CONTACTS)

        result = await _call_tool(
            adapter,
            deps,
            BandTool.RESPOND_CONTACT_REQUEST,
            {"action": "approve", "handle": "@ann"},
        )

        deps.respond_contact_request.assert_called_once_with(
            action="approve", handle="@ann"
        )
        (content,) = _tool_returns(result)
        assert "boom during serialization" in content
        deps.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_normalization_failure_counts_as_completed_work(
        self, sample_message
    ):
        """A posted message is terminal even when its response cannot serialize."""
        calls_pending = True

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            nonlocal calls_pending
            if calls_pending:
                calls_pending = False
                yield {
                    0: DeltaToolCall(
                        name=BandTool.SEND_MESSAGE,
                        json_args=json.dumps({"content": "hi", "mentions": []}),
                        tool_call_id="call-1",
                    )
                }
            else:
                yield "done"

        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"), emit=())
        await adapter.on_started("Probe", "probe")
        adapter._agent.model = FunctionModel(stream_function=stream)

        deps = MagicMock()
        result = MagicMock()
        result.model_dump.side_effect = TypeError("boom during serialization")
        deps.send_message = AsyncMock(return_value=result)
        deps.send_event = AsyncMock()

        await adapter.on_message(
            msg=sample_message,
            tools=deps,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        deps.send_message.assert_called_once_with(content="hi", mentions=[])
        deps.send_event.assert_not_called()
