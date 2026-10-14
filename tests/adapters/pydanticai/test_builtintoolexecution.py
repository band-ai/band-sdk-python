"""BuiltinToolExecution behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import RetryPromptPart

from band.core.types import Capability
from band.runtime.tools import BandTool
from tests.adapters.pydanticai.kit import _call_tool, _parts, _started_adapter


class TestBuiltinToolExecution:
    """Argument handling through pydantic-ai's real tool-calling loop.

    The integration supplies pydantic-ai with the master schema and a validator
    that preserves its normalized kwargs and retry behavior.
    """

    @pytest.mark.asyncio
    async def test_invalid_arguments_retry_without_dispatching(self):
        deps = MagicMock()
        deps.send_message = AsyncMock(return_value={"status": "sent"})
        adapter = await _started_adapter()

        result = await _call_tool(
            adapter,
            deps,
            BandTool.SEND_MESSAGE,
            {"content": "hi", "mentions": "alice"},
        )

        (retry,) = _parts(result, RetryPromptPart)
        assert "Invalid arguments for band_send_message" in str(retry.content)
        assert "mentions" in str(retry.content)
        deps.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_arguments_retry_without_dispatching(self):
        """The schema-driven tool boundary keeps native tools' extra rejection."""
        deps = MagicMock()
        deps.send_message = AsyncMock(return_value={"status": "sent"})
        adapter = await _started_adapter()

        result = await _call_tool(
            adapter,
            deps,
            BandTool.SEND_MESSAGE,
            {"content": "hi", "mentions": [], "unexpected": "value"},
        )

        (retry,) = _parts(result, RetryPromptPart)
        assert "unexpected" in str(retry.content)
        deps.send_message.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "arguments", [["hi"], '"hi"', 123], ids=["list", "string", "number"]
    )
    async def test_non_object_arguments_retry_without_dispatching(self, arguments):
        """Valid JSON with a non-object shape is a model retry, not a crash."""
        deps = MagicMock()
        deps.send_message = AsyncMock(return_value={"status": "sent"})
        adapter = await _started_adapter()

        result = await _call_tool(
            adapter,
            deps,
            BandTool.SEND_MESSAGE,
            arguments,
        )

        (_retry,) = _parts(result, RetryPromptPart)
        deps.send_message.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "method_name", "expected_kwargs"),
        [
            (
                BandTool.ADD_PARTICIPANT,
                "add_participant",
                {"identifier": "alice", "role": "member"},
            ),
            (
                BandTool.REMOVE_PARTICIPANT,
                "remove_participant",
                {"identifier": "alice"},
            ),
        ],
        ids=["add_participant", "remove_participant"],
    )
    async def test_validation_alias_argument_is_not_rejected_as_unknown(
        self, tool, method_name, expected_kwargs
    ):
        """A field's ``validation_alias`` secondary name is a real accepted
        argument, not an unrecognized one, even though it's absent from the
        model's advertised JSON schema (which only lists the primary alias)."""
        deps = MagicMock()
        method = AsyncMock(return_value={"id": "user-1"})
        setattr(deps, method_name, method)
        adapter = await _started_adapter()

        await _call_tool(adapter, deps, tool, {"name": "alice"})

        method.assert_called_once_with(**expected_kwargs)

    @pytest.mark.asyncio
    async def test_coercible_arguments_reach_the_dependency_normalized(self):
        deps = MagicMock()
        deps.list_tasks = AsyncMock(return_value={"tasks": []})
        adapter = await _started_adapter(capabilities=Capability.TASKS)

        await _call_tool(adapter, deps, BandTool.LIST_TASKS, {"limit": "5"})

        # limit coerced to int; the two omitted optionals are not passed at all
        # rather than handed over as None.
        deps.list_tasks.assert_called_once_with(limit=5)

    @pytest.mark.asyncio
    async def test_explicit_nulls_are_dropped_before_dispatch(self):
        deps = MagicMock()
        deps.send_event = AsyncMock(return_value={"status": "sent"})
        adapter = await _started_adapter()

        await _call_tool(
            adapter,
            deps,
            BandTool.SEND_EVENT,
            {"content": "thinking", "message_type": "thought", "metadata": None},
        )

        deps.send_event.assert_called_once_with(
            content="thinking", message_type="thought"
        )

    @pytest.mark.asyncio
    async def test_dependency_is_called_with_master_field_names(self):
        """The master model's field names are the dependency's keyword names.

        Keyword dispatch is what removes a whole class of bug the hand-written
        wrappers had: ``send_room_file`` takes (content, filename, caption,
        mentions) while the model lists them in another order.
        """
        deps = MagicMock()
        deps.send_room_file = AsyncMock(return_value={"message_id": "msg-1"})
        adapter = await _started_adapter(capabilities=Capability.FILES)

        await _call_tool(
            adapter,
            deps,
            BandTool.SEND_ROOM_FILE,
            {
                "content": "file body",
                "filename": "notes.txt",
                "caption": "here's a file",
                "mentions": ["Alice", "Bob"],
            },
        )

        deps.send_room_file.assert_called_once_with(
            content="file body",
            filename="notes.txt",
            caption="here's a file",
            mentions=["Alice", "Bob"],
        )

    @pytest.mark.asyncio
    async def test_missing_backend_method_raises_an_actionable_error(self):
        """A stale/typo'd registry method name is a registry bug -- it should
        surface with the tool and method name, not a bare AttributeError."""
        deps = MagicMock(spec=[])
        adapter = await _started_adapter()

        with pytest.raises(RuntimeError, match="send_message"):
            await _call_tool(
                adapter, deps, BandTool.SEND_MESSAGE, {"content": "hi", "mentions": []}
            )
