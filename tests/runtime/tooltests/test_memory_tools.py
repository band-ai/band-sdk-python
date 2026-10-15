from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from band.client.rest import DEFAULT_REQUEST_OPTIONS, UnprocessableEntityError
from band.core.exceptions import BandToolError
from band.core.memory_types import ORGANIZATION_SCOPE_REJECTED_CODE
from band.runtime.tools import AgentTools, StoreMemoryInput


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_list_memories_omits_none_filters(
        self, mock_rest_client: MagicMock
    ) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_memories(page_size=25)

        mock_rest_client.agent_api_memories.list_agent_memories.assert_awaited_once_with(
            page_size=25,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_store_memory_omits_none_fields(
        self, mock_rest_client: MagicMock
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.store_memory(
            content="remember this",
            system="working",
            type="semantic",
            segment="user",
            thought="useful later",
            scope="organization",
        )

        call_kwargs = (
            mock_rest_client.agent_api_memories.create_agent_memory.call_args.kwargs
        )
        memory_payload = call_kwargs["memory"].model_dump(exclude_unset=True)
        assert "subject_id" not in memory_payload
        assert "metadata" not in memory_payload
        assert call_kwargs["request_options"] is DEFAULT_REQUEST_OPTIONS

    @pytest.mark.asyncio
    async def test_store_memory_agent_scope_sent_without_subject_id(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Agent scope round-trips to the REST payload untouched and needs no
        subject_id -- the scope value real agents fall back to when their owner
        has no organization."""
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.store_memory(
            content="remember this",
            system="working",
            type="semantic",
            segment="user",
            thought="useful later",
            scope="agent",
        )

        call_kwargs = (
            mock_rest_client.agent_api_memories.create_agent_memory.call_args.kwargs
        )
        memory_payload = call_kwargs["memory"].model_dump(exclude_unset=True)
        assert memory_payload["scope"] == "agent"
        assert "subject_id" not in memory_payload

    @pytest.mark.asyncio
    async def test_list_memories_agent_scope_sent_untouched(
        self, mock_rest_client: MagicMock
    ) -> None:
        """`scope="agent"` passes straight through to the list filter."""
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_memories(scope="agent")

        mock_rest_client.agent_api_memories.list_agent_memories.assert_awaited_once_with(
            page_size=50,
            scope="agent",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_store_memory_organization_scope_rejection_becomes_actionable(
        self, mock_rest_client: MagicMock
    ) -> None:
        """The platform's 422 for an orgless agent's `scope="organization"`
        write must reach the caller as guidance to retry with `scope="agent"`,
        not as the raw `headers: ..., status_code: 422, body: ...` dump the
        generic exception handler would otherwise produce."""
        rejection_body = {
            "error": {
                "code": ORGANIZATION_SCOPE_REJECTED_CODE,
                "details": {"organization_id": "must be present"},
            }
        }
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            side_effect=UnprocessableEntityError(body=rejection_body)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match='scope="agent"'):
            await tools.store_memory(
                content="remember this",
                system="working",
                type="semantic",
                segment="user",
                thought="useful later",
                scope="organization",
            )

    @pytest.mark.asyncio
    async def test_store_memory_unrelated_422_is_not_mistaken_for_scope_rejection(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A 422 for any other reason must propagate as-is -- rewriting it to
        the scope-retry message would mislead the caller about the actual
        failure."""
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            side_effect=UnprocessableEntityError(
                body={"error": {"code": "some_other_validation_failure"}}
            )
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(UnprocessableEntityError):
            await tools.store_memory(
                content="remember this",
                system="working",
                type="semantic",
                segment="user",
                thought="useful later",
                scope="organization",
            )

    @pytest.mark.asyncio
    async def test_list_memories_organization_scope_rejection_becomes_actionable(
        self, mock_rest_client: MagicMock
    ) -> None:
        """The read-side twin of the store-side test above."""
        rejection_body = {"error": {"code": ORGANIZATION_SCOPE_REJECTED_CODE}}
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            side_effect=UnprocessableEntityError(body=rejection_body)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match='scope="agent"'):
            await tools.list_memories(scope="organization")

    @pytest.mark.asyncio
    async def test_store_memory_rejects_subject_scope_without_subject_id(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Reject subject-scoped writes before they reach the API."""
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock()
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="requires a subject_id"):
            await tools.store_memory(
                content="remember this",
                system="working",
                type="semantic",
                segment="user",
                thought="useful later",
                scope="subject",
            )

        mock_rest_client.agent_api_memories.create_agent_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_memory_rejects_type_for_wrong_system(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Reject a system/type mismatch before it reaches the API -- the raw
        tool method's ValueError comes straight from band_sdk_core, unlike
        StoreMemoryInput's model_validate, which wraps it in a
        pydantic.ValidationError."""
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock()
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(
            ValueError, match="type `semantic` is not valid for system `sensory`"
        ):
            await tools.store_memory(
                content="remember this",
                system="sensory",
                type="semantic",
                segment="user",
                thought="useful later",
                scope="agent",
            )

        mock_rest_client.agent_api_memories.create_agent_memory.assert_not_called()

    def test_store_memory_input_rejects_subject_scope_without_subject_id(self) -> None:
        """Validate tool input rejects subject scope without subject_id."""
        with pytest.raises(ValidationError, match="requires a subject_id"):
            StoreMemoryInput.model_validate(
                {
                    "content": "remember this",
                    "system": "working",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "subject",
                }
            )

    def test_store_memory_input_rejects_type_for_wrong_system(self) -> None:
        """Validate memory type matches the chosen system."""
        with pytest.raises(
            ValidationError, match="type `semantic` is not valid for system `sensory`"
        ):
            StoreMemoryInput.model_validate(
                {
                    "content": "remember this",
                    "system": "sensory",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "organization",
                }
            )

    @pytest.mark.parametrize(
        ("tool_method", "rest_method"),
        [
            ("get_memory", "get_agent_memory"),
            ("supersede_memory", "supersede_agent_memory"),
            ("archive_memory", "archive_agent_memory"),
        ],
    )
    @pytest.mark.asyncio
    async def test_memory_mutation_calls_use_default_request_options(
        self,
        mock_rest_client: MagicMock,
        tool_method: str,
        rest_method: str,
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        rest_call = AsyncMock(return_value=response)
        setattr(mock_rest_client.agent_api_memories, rest_method, rest_call)
        tools = AgentTools("room-123", mock_rest_client)

        await getattr(tools, tool_method)("mem-123")

        rest_call.assert_awaited_once_with(
            id="mem-123",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
