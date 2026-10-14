"""Tests for the Parlant task board tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.parlant.tools import set_session_tools
from band.testing import FakeAgentTools

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


class TestTaskTools:
    """Drives the real task tools built by ``create_parlant_tools``."""

    @pytest.mark.parametrize(
        "name,args,field",
        [
            ("band_get_task", {"id": "#"}, "id"),
            ("band_get_task", {"id": "1", "include": "invalid"}, "include"),
            ("band_update_task", {"id": "1"}, "At least one"),
            ("band_update_task", {"id": "1", "status": "invalid"}, "status"),
            ("band_update_task", {"id": "1", "state": "invalid"}, "state"),
            ("band_get_task_history", {"id": "1", "limit": 0}, "limit"),
            ("band_get_task_history", {"id": "1", "limit": 101}, "limit"),
            ("band_get_task_history", {"id": "id\n"}, "id"),
        ],
    )
    @pytest.mark.asyncio
    async def test_path_tools_reject_before_execution(
        self,
        parlant_tools: dict[str, Any],
        mock_tools: MagicMock,
        mock_context: SimpleNamespace,
        name: str,
        args: dict[str, object],
        field: str,
    ) -> None:
        method = name.removeprefix("band_")
        setattr(mock_tools, method, AsyncMock())
        set_session_tools(mock_context.session_id, mock_tools)
        result = await parlant_tools[name](mock_context, **args)
        assert f"Invalid arguments for {name}" in result.data
        assert field in result.data
        getattr(mock_tools, method).assert_not_awaited()

    @pytest.mark.parametrize(
        "name,args",
        [
            ("band_get_task", {"id": "#001"}),
            (
                "band_get_task",
                {"id": "#ABCDEF01-2345-6789-ABCD-EF0123456789", "include": "history"},
            ),
            ("band_update_task", {"id": "#1", "status": "completed"}),
            ("band_get_task_history", {"id": "001", "limit": 100}),
        ],
    )
    @pytest.mark.asyncio
    async def test_path_tools_preserve_validated_options(
        self,
        parlant_tools: dict[str, Any],
        mock_tools: MagicMock,
        mock_context: SimpleNamespace,
        name: str,
        args: dict[str, object],
    ) -> None:
        method = name.removeprefix("band_")
        target = AsyncMock(return_value={"id": args["id"], "subject": "task"})
        setattr(mock_tools, method, target)
        set_session_tools(mock_context.session_id, mock_tools)
        result = await parlant_tools[name](mock_context, **args)
        assert "task" in result.data
        assert target.await_count == 1
        assert target.call_args.kwargs == args


async def test_created_task_and_board_goal_read_back_as_json(
    parlant_tools: dict[str, Any], mock_context: SimpleNamespace
) -> None:
    set_session_tools(mock_context.session_id, FakeAgentTools())

    created = await parlant_tools["band_create_task"](mock_context, "Paint the wall")
    listed = await parlant_tools["band_list_tasks"](mock_context)
    await parlant_tools["band_set_board"](mock_context, goal_title="Ship it")
    board = await parlant_tools["band_get_board"](mock_context)

    task = json.loads(created.data)
    assert [t["id"] for t in json.loads(listed.data)["data"]] == [task["id"]]
    assert task["subject"] == "Paint the wall"
    assert json.loads(board.data)["goal_title"] == "Ship it"
