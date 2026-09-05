"""Tests for AgentTools' task-board methods (list_tasks, create_task,
get_task, update_task, get_task_history, get_board, set_board).

Mirrors tests/runtime/test_tools.py's TestMemoryTools/TestFileTools pattern:
mock_rest_client is an autospec of the real Fern client, so an assertion
here fails immediately if band-client-rest renames a method or drops a
parameter, rather than passing silently against a hand-rolled fake.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.runtime.tools import AgentTools


class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_tasks_default_passes_none_filters(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_chat_tasks.list_chat_tasks = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.list_tasks()

        mock_rest_client.agent_api_chat_tasks.list_chat_tasks.assert_awaited_once_with(
            chat_id="room-123",
            state=None,
            cursor=None,
            limit=None,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        assert result is response

    @pytest.mark.asyncio
    async def test_list_tasks_passes_filters_through(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_chat_tasks.list_chat_tasks = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_tasks(state=TaskListState.CANCELLED, cursor="cur-1", limit=25)

        mock_rest_client.agent_api_chat_tasks.list_chat_tasks.assert_awaited_once_with(
            chat_id="room-123",
            state=TaskListState.CANCELLED,
            cursor="cur-1",
            limit=25,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_task_omits_unset_optional_fields(
        self, mock_rest_client
    ) -> None:
        """detail/supersedes_id default to Fern's OMIT sentinel server-side --
        passing None explicitly (instead of leaving the key out) would fail
        backend validation, so they must be absent from the call entirely."""
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.create_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.create_task("Write the report")

        mock_rest_client.agent_api_chat_tasks.create_chat_task.assert_awaited_once_with(
            chat_id="room-123",
            subject="Write the report",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_create_task_includes_provided_optional_fields(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.create_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.create_task(
            "Write the report", detail="Cover Q3", supersedes_id="task-old"
        )

        mock_rest_client.agent_api_chat_tasks.create_chat_task.assert_awaited_once_with(
            chat_id="room-123",
            subject="Write the report",
            detail="Cover Q3",
            supersedes_id="task-old",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_create_task_raises_on_empty_response_data(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = None
        mock_rest_client.agent_api_chat_tasks.create_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to create task"):
            await tools.create_task("Write the report")


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_task_passes_id_and_include(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.get_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_task("task-1", include="history")

        mock_rest_client.agent_api_chat_tasks.get_chat_task.assert_awaited_once_with(
            chat_id="room-123",
            id="task-1",
            include="history",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        assert result is response.data

    @pytest.mark.asyncio
    async def test_get_task_raises_on_empty_response_data(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = None
        mock_rest_client.agent_api_chat_tasks.get_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to get task"):
            await tools.get_task("task-1")

    @pytest.mark.asyncio
    async def test_get_task_rejects_an_invalid_include_value(
        self, mock_rest_client
    ) -> None:
        """Guards every caller, not just ones that go through GetTaskInput's
        Literal["history"] typing -- Parlant and pydantic-ai hand this
        through as an unchecked `str` cast instead."""
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="include must be"):
            await tools.get_task("task-1", include="anything")

        mock_rest_client.agent_api_chat_tasks.get_chat_task.assert_not_awaited()


class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_update_task_omits_unset_optional_fields(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.update_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.update_task("task-1", status=TaskAssignmentStatus.IN_PROGRESS)

        mock_rest_client.agent_api_chat_tasks.update_chat_task.assert_awaited_once_with(
            chat_id="room-123",
            id="task-1",
            status=TaskAssignmentStatus.IN_PROGRESS,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_update_task_raises_when_no_fields_are_set(
        self, mock_rest_client
    ) -> None:
        """Guards every caller, not just ones that go through UpdateTaskInput's
        model_validator -- Parlant and pydantic-ai register this as a plain
        function and never construct that model."""
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="At least one of"):
            await tools.update_task("task-1")

        mock_rest_client.agent_api_chat_tasks.update_chat_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_task_includes_provided_fields(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.update_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.update_task(
            "task-1",
            status=TaskAssignmentStatus.IN_PROGRESS,
            active_form="Writing the report",
            comment="Started drafting",
            subject="Write the Q3 report",
            detail="Cover revenue and churn",
            state=TaskLifecycleState.ACTIVE,
        )

        mock_rest_client.agent_api_chat_tasks.update_chat_task.assert_awaited_once_with(
            chat_id="room-123",
            id="task-1",
            status=TaskAssignmentStatus.IN_PROGRESS,
            active_form="Writing the report",
            comment="Started drafting",
            subject="Write the Q3 report",
            detail="Cover revenue and churn",
            state=TaskLifecycleState.ACTIVE,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_update_task_raises_on_empty_response_data(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = None
        mock_rest_client.agent_api_chat_tasks.update_chat_task = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to update task"):
            await tools.update_task("task-1", status=TaskAssignmentStatus.COMPLETED)


class TestGetTaskHistory:
    @pytest.mark.asyncio
    async def test_get_task_history_passes_id_and_pagination(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_chat_tasks.get_chat_task_history = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_task_history("task-1", cursor="cur-1", limit=10)

        mock_rest_client.agent_api_chat_tasks.get_chat_task_history.assert_awaited_once_with(
            chat_id="room-123",
            id="task-1",
            cursor="cur-1",
            limit=10,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        assert result is response


class TestGetBoard:
    @pytest.mark.asyncio
    async def test_get_board_passes_include(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.get_chat_board = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_board(include="history")

        mock_rest_client.agent_api_chat_tasks.get_chat_board.assert_awaited_once_with(
            chat_id="room-123",
            include="history",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        assert result is response.data

    @pytest.mark.asyncio
    async def test_get_board_raises_on_empty_response_data(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = None
        mock_rest_client.agent_api_chat_tasks.get_chat_board = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to get board"):
            await tools.get_board()

    @pytest.mark.asyncio
    async def test_get_board_rejects_an_invalid_include_value(
        self, mock_rest_client
    ) -> None:
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="include must be"):
            await tools.get_board(include="anything")

        mock_rest_client.agent_api_chat_tasks.get_chat_board.assert_not_awaited()


class TestSetBoard:
    @pytest.mark.asyncio
    async def test_set_board_omits_unset_optional_fields(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.put_chat_board = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.set_board(goal_title="Ship v2")

        mock_rest_client.agent_api_chat_tasks.put_chat_board.assert_awaited_once_with(
            chat_id="room-123",
            goal_title="Ship v2",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_set_board_raises_when_no_fields_are_set(
        self, mock_rest_client
    ) -> None:
        """Guards every caller, not just ones that go through SetBoardInput's
        model_validator -- Parlant and pydantic-ai register this as a plain
        function and never construct that model."""
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="At least one of"):
            await tools.set_board()

        mock_rest_client.agent_api_chat_tasks.put_chat_board.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_board_includes_provided_fields(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_chat_tasks.put_chat_board = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.set_board(goal_title="Ship v2", goal_summary="Ship v2 by Q3")

        mock_rest_client.agent_api_chat_tasks.put_chat_board.assert_awaited_once_with(
            chat_id="room-123",
            goal_title="Ship v2",
            goal_summary="Ship v2 by Q3",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_set_board_raises_on_empty_response_data(
        self, mock_rest_client
    ) -> None:
        response = MagicMock()
        response.data = None
        mock_rest_client.agent_api_chat_tasks.put_chat_board = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to set board"):
            await tools.set_board(goal_title="Ship v2")
