"""Prompt narration and results preserve typed content and report failures."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpPromptError, OmpProtocolError, OmpRpcError
from band.integrations.ompwire import (
    OMP_NATIVE_PROTOCOL_VERSION,
    OmpCommandName,
    OmpEvent,
    OmpEventType,
    OmpState,
)
from tests.integrations.omppeer import (
    ADMITTED_PROMPT_ERROR,
    COMPLETE,
    LATE_PROMPT_ERROR,
)
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    checkpoint_frames,
    owned_rpc_process,
    prompt_task,
    second_prompt,
)


class TestPromptNarrationAndResults:
    async def test_prompt_delivers_typed_narration_and_ignores_other_events(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        observer = Observer()
        async with asyncio.timeout(5):
            await prompt_task(client, observer)
        assert observer.outline == [
            OmpEventType.AGENT_START,
            OmpEventType.TOOL_START,
            OmpEventType.TOOL_END,
            OmpEventType.MESSAGE_END,
            OmpEventType.AGENT_END,
        ]
        tool_start, tool_end, message_end = observer.events[1:4]
        assert (tool_start.toolCallId, tool_start.toolName, tool_start.args) == (
            "native-call",
            "read",
            {"path": "note.txt"},
        )
        assert tool_end.result is not None
        assert tool_end.result.content[0].text == "note"
        assert tool_end.result.details == {"lines": 1}
        assert tool_end.isError is False
        message = message_end.message
        assert message is not None
        assert message.role == "assistant"
        assert isinstance(message.content, list)
        assert [
            (content.type, content.text, content.thinking)
            for content in message.content
        ] == [
            ("thinking", None, "Considering the result"),
            ("text", COMPLETE, None),
        ]
        assert message.usage is not None
        assert (
            message.usage.input,
            message.usage.output,
            message.usage.totalTokens,
        ) == (
            7,
            3,
            13,
        )
        assert message.usage.cost is not None
        assert message.usage.cost.total == 0.33
        state = OmpState.model_validate(await client.request("get_state"))
        assert (state.sessionId, state.sessionFile, state.isSettled) == (
            "session-local",
            "/local/session.jsonl",
            True,
        )
        assert state.model is not None
        assert (state.model.provider, state.model.id) == ("test-provider", "test-model")
        await second_prompt(client)

    async def test_rejected_prompt_does_not_wait_for_a_result_that_is_not_owed(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("rejected")
        async with asyncio.timeout(5):
            with pytest.raises(OmpRpcError, match="Prompt rejected") as rejected:
                await prompt_task(client, Observer())
        assert (rejected.value.command, rejected.value.code) == ("prompt", "rejected")
        await second_prompt(client)

    async def test_command_error_after_admission_keeps_the_process_usable(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(LATE_PROMPT_ERROR)
        process = owned_rpc_process(client)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request("release")
            with pytest.raises(OmpRpcError) as failed:
                await running
            assert failed.value.command == OmpCommandName.PROMPT
            assert process.returncode is None
            await second_prompt(client)
            assert owned_rpc_process(client) is process
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_a_late_response_for_the_wrong_command_retires_the_transport(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(LATE_PROMPT_ERROR)
        process = owned_rpc_process(client)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            with pytest.raises(OmpProtocolError):
                await client.request("release", failed_command=OmpCommandName.GET_STATE)
            with pytest.raises(OmpProtocolError):
                await running
            assert process.returncode is not None
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_a_late_prompt_error_does_not_reap_the_process(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(ADMITTED_PROMPT_ERROR)
        process = owned_rpc_process(client)
        async with asyncio.timeout(5):
            with pytest.raises(OmpRpcError) as late:
                await prompt_task(client, Observer())
        assert (late.value.command, late.value.code) == (
            OmpCommandName.PROMPT,
            "post_admit",
        )
        assert process.returncode is None
        await second_prompt(client)

    @pytest.mark.parametrize(
        ("scenario", "match"),
        [
            ("initial-error", "Initial provider failed"),
            ("initial-aborted", "OMP prompt aborted"),
        ],
    )
    async def test_failed_initial_result_is_reported(
        self, client_factory: ClientFactory, scenario: str, match: str
    ) -> None:
        client = await client_factory(scenario)
        process = owned_rpc_process(client)
        async with asyncio.timeout(5):
            with pytest.raises(OmpPromptError, match=match):
                await prompt_task(client, Observer())
        assert process.returncode is None
        await second_prompt(client)

    async def test_narration_failure_does_not_leave_the_turn_or_process_waiting(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        process = owned_rpc_process(client)
        observer = Observer()

        async def on_event(event: OmpEvent) -> None:
            raise ValueError("Narration failed")

        async with asyncio.timeout(6):
            with pytest.raises(ValueError, match="Narration failed"):
                await client.prompt(
                    "first",
                    on_event=on_event,
                    on_tool=observer.on_tool,
                    on_ui=observer.on_ui,
                )
        assert process.returncode is not None

    async def test_native_v2_preserves_large_inline_image_frames(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("large-image")
        observer = Observer()
        async with asyncio.timeout(5):
            await prompt_task(client, observer)
        message = next(
            event.message
            for event in observer.events
            if event.type == OmpEventType.MESSAGE_END
        )
        assert message is not None and isinstance(message.content, list)
        image = next(content for content in message.content if content.type == "image")
        assert image.mimeType == "image/png"
        assert image.data == "YWJj" * 300_000
        negotiations = [
            frame
            for frame in await checkpoint_frames(client)
            if frame["type"] == OmpCommandName.NEGOTIATE_PROTOCOL
        ]
        assert len(negotiations) == 1
        assert negotiations[0]["protocolVersion"] == OMP_NATIVE_PROTOCOL_VERSION
