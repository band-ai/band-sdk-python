"""A full project workflow across human gates and rejoin."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.core.types import Capability
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME, mcp_tool_spelling
from band.runtime.tools.types import BandTool
from band.testing import failure_reports
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    EchoInput,
    cursor_custom_tools,
    permission_requests,
    said,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import decision_token
from tests.mcpclient import STORE_MEMORY_ARGS


class TestProjectWorkflow:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("approval_mode", "first_edit", "second_edit"),
        [
            ("manual", False, True),
            ("auto_accept", True, True),
            ("auto_decline", False, False),
        ],
    )
    async def test_project_workflow_keeps_band_tools_available_across_human_gates_and_rejoin(
        self,
        cursor_room: Callable[..., Awaitable[CursorRoom]],
        tmp_path: Path,
        approval_mode: str,
        first_edit: bool,
        second_edit: bool,
    ) -> None:
        project = tmp_path / "project.txt"
        project.write_text("original")
        audit = tmp_path / "audit.txt"
        marker = "project workflow completed"

        def record_work(arguments: EchoInput) -> str:
            with audit.open("a") as stream:
                stream.write(arguments.text + "\n")
            return arguments.text

        async def edit_project(agent: FakeACPAgent, session_id: str) -> None:
            await agent.emit(session_id, start_tool_call("native-edit", "shell"))
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
                str(project),
                "repaired",
            )
            if await process.wait() != 0:
                raise RuntimeError("project edit failed")
            await agent.emit(
                session_id,
                update_tool_call("native-edit", raw_output="project repaired"),
            )

        agent = (
            FakeACPAgent()
            .will_ask_permission(title="band-echo: echo")
            .will_call_mcp_tool(
                "custom-work",
                "echo",
                arguments={"text": marker},
                requires_approval=True,
            )
        )
        agent.will_ask_permission(title="shell").will_execute_if_approved(edit_project)
        for tool, arguments in [
            (BandTool.STORE_MEMORY, {**STORE_MEMORY_ARGS, "content": marker}),
            (BandTool.SEND_MESSAGE, {"content": marker, "mentions": ["@user-1"]}),
        ]:
            agent.will_ask_permission(
                title=f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, tool)}: {tool}"
            ).will_call_mcp_tool(
                tool, tool, arguments=arguments, requires_approval=True
            )
        agent.will_say("I already posted the result.")
        room = await cursor_room(
            agent,
            approval_mode=approval_mode,
            capabilities={Capability.MEMORY},
            additional_tools=cursor_custom_tools(record_work),
        )

        for attempt, edited in enumerate((first_edit, second_edit)):
            project.write_text("original")
            turn = await room.send("repair the project and record the result")
            if approval_mode == "manual":
                assert project.read_text() == "original"
                [request] = permission_requests(turn)
                assert "shell" in request
                command = "deny" if attempt == 0 else "select"
                option = "" if attempt == 0 else " allow-1"
                decision = await room.send(
                    f"/cursor {command} {decision_token(request)}{option}"
                )
                assert failure_reports(decision) == []
            await room.turns_finished()

            assert project.read_text() == ("repaired" if edited else "original")
            assert audit.read_text().splitlines() == [marker] * (attempt + 1)
            assert turn.memory_contents == [marker]
            assert said(turn) == [*permission_requests(turn), marker]
            assert turn.turn.replied
            assert failure_reports(turn) == []
            await room.adapter.on_cleanup("room-1")
