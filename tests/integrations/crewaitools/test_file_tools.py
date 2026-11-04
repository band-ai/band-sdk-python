"""FileTools behavior at the CrewAI tool boundary."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from band.core.types import AdapterFeatures, Capability, Emit
from band.runtime.tools import (
    file_content_placeholder,
    image_block_placeholder,
)
from tests.crewaicontract import tool_result_data


class TestFileTools:
    def test_list_room_files_forwards_cursor(self, builder_mod):

        tools_obj = MagicMock()
        tools_obj.list_room_files = AsyncMock(
            return_value={"data": [{"id": "file-1"}], "next_cursor": None}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        list_room_files = next(t for t in tools if t.name == "band_list_room_files")

        result = tool_result_data(list_room_files._run(cursor="cursor-1"))

        assert result["status"] == "success"
        tools_obj.list_room_files.assert_awaited_once_with("cursor-1")

    def test_list_room_files_default_cursor_is_none(self, builder_mod):

        tools_obj = MagicMock()
        tools_obj.list_room_files = AsyncMock(return_value={"data": []})
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        list_room_files = next(t for t in tools if t.name == "band_list_room_files")

        list_room_files._run()

        tools_obj.list_room_files.assert_awaited_once_with(None)

    def test_read_room_file_forwards_file_id(self, builder_mod):

        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(
            return_value={"name": "report.txt", "text": "hello"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = tool_result_data(read_room_file._run(file_id="file-1"))

        assert result["status"] == "success"
        tools_obj.read_room_file.assert_awaited_once_with("file-1")

    def test_read_room_file_image_result_becomes_vision_sentinel(self, builder_mod):
        """CrewAI's own StepExecutor rewrites a VISION_IMAGE:<media_type>:<b64>
        tool-result string into a real image_url content block -- pin that
        band_read_room_file emits exactly that sentinel for an image result."""

        image_result = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(return_value=image_result)
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = read_room_file._run(file_id="file-1")

        assert result == builder_mod.vision_sentinel(image_result)

    def test_read_room_file_image_result_reports_placeholder_not_base64(
        self, builder_mod
    ):
        """The full base64 sentinel must reach CrewAI's StepExecutor, but the
        platform tool_result event must not carry that same base64 blob."""

        image_result = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(return_value=image_result)
        tools_obj.send_event = AsyncMock()
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        reporter = builder_mod.EmitToolCallsReporter(
            AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=reporter,
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = read_room_file._run(file_id="file-1")

        assert result == builder_mod.vision_sentinel(image_result)
        result_event = tools_obj.send_event.call_args_list[-1].kwargs
        reported_output = tool_result_data(result_event["content"])["output"]
        assert reported_output == image_block_placeholder(1)
        assert "ZmFrZQ==" not in reported_output

    def test_send_room_file_forwards_args_in_protocol_order(self, builder_mod):
        """AgentToolsProtocol.send_room_file wants (content, filename, caption,
        mentions) positionally -- pin the reorder from the tool's own kwargs."""

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {"id": "file-2"}, "message_id": "msg-1"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        result = tool_result_data(
            send_room_file._run(
                content="file body",
                filename="notes.txt",
                mentions=["Alice", "Bob"],
                caption="here's a file",
            )
        )

        assert result["status"] == "success"
        tools_obj.send_room_file.assert_awaited_once_with(
            "file body", "notes.txt", "here's a file", ["Alice", "Bob"]
        )

    def test_send_room_file_mentions_accepts_lenient_string_shape(self, builder_mod):
        """Smaller models emit mentions as a JSON-string or bracketed string,
        same leniency need as band_send_message -- see normalize_mentions_lenient."""

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {}, "message_id": "msg-1"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        send_room_file._run(
            content="body", filename="notes.txt", mentions="@alice, @bob"
        )

        tools_obj.send_room_file.assert_awaited_once_with(
            "body", "notes.txt", "", ["@alice", "@bob"]
        )

    def test_send_room_file_reports_content_placeholder_not_raw_bytes(
        self, builder_mod
    ):
        """The full content must still reach send_room_file, but the
        tool_call event must not carry that same raw payload."""

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {"id": "file-2"}, "message_id": "msg-1"}
        )
        tools_obj.send_event = AsyncMock()
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        reporter = builder_mod.EmitToolCallsReporter(
            AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=reporter,
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        # Multi-byte characters pin that the placeholder reports UTF-8 byte
        # length (what MAX_SEND_CONTENT_BYTES actually measures), not
        # len(content)'s character count.
        content = "raw file body 你好 " * 1000
        send_room_file._run(content=content, filename="notes.txt", mentions=["Alice"])

        tools_obj.send_room_file.assert_awaited_once_with(
            content, "notes.txt", "", ["Alice"]
        )
        call_event = tools_obj.send_event.call_args_list[0].kwargs
        reported_args = tool_result_data(call_event["content"])["args"]
        assert reported_args["content"] == file_content_placeholder(
            len(content.encode("utf-8"))
        )
        assert content not in json.dumps(reported_args)

    def test_send_room_file_failure_returns_error_status(self, builder_mod):

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(side_effect=RuntimeError("upload failed"))
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        result = tool_result_data(
            send_room_file._run(
                content="body", filename="notes.txt", mentions=["Alice"]
            )
        )

        assert result["status"] == "error"
        assert "upload failed" in result["message"]
