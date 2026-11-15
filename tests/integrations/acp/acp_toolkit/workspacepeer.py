"""A real stdio ACP agent that answers the common Room lifecycle suite's
commands in its workspace."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

from acp import RequestError, run_agent
from acp.schema import (
    AllowedOutcome,
    InitializeResponse,
    NewSessionResponse,
    ToolCallUpdate,
)
from pydantic import BaseModel

from band.integrations.omp import OMP_CWD_FLAG
from band.runtime.tools import BandTool
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME
from tests.adapters.roompeer import (
    FAIL_STARTUP_ARG,
    HOLD_POLL_S,
    HOLD_STARTUP_ARG,
    STARTUP_FAILURE,
    WORKSPACE_NOTE,
    PeerCommand,
    begin_startup,
    file_request,
    perform,
    reply_text,
    startup_held,
    startup_refused,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent, allow_or_reject

CURSOR_WORKFLOW_PREFIX = "CURSOR_WORKFLOW="
CURSOR_WORKFLOW_PATTERN = re.compile(
    rf"{re.escape(CURSOR_WORKFLOW_PREFIX)}([a-zA-Z0-9-]+)"
)
CURSOR_QUESTION_ID = "proceed"
CURSOR_ALLOW_OPTION = "allow-workspace-write"
CURSOR_JOURNAL = "cursor-journal.jsonl"
CURSOR_SETUP_HOLD = "hold-session-setup"
CURSOR_SETUP_READY = "session-allocated"


class CursorChoice(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"


class CursorPeerEvent(StrEnum):
    SESSION_CREATED = "session-created"
    PROMPT_STARTED = "prompt-started"
    QUESTION_ANSWERED = "question-answered"
    FILE_WRITTEN = "file-written"


class CursorPeerRecord(BaseModel):
    event: CursorPeerEvent
    value: str


def record_cursor(event: CursorPeerEvent, value: str) -> None:
    with Path(CURSOR_JOURNAL).open("a") as stream:
        stream.write(
            CursorPeerRecord(event=event, value=value).model_dump_json() + "\n"
        )


def cursor_workflow(marker: str) -> str:
    return CURSOR_WORKFLOW_PREFIX + marker


async def wait_for_release(hold: Path | None) -> None:
    while startup_held(hold):
        await asyncio.sleep(HOLD_POLL_S)


class WorkspacePeer(FakeACPAgent):
    """``--hold-startup`` holds ``initialize`` while its file exists and
    ``--fail-startup`` refuses it, as the suite's other peers do."""

    def __init__(self, failure: Path | None, hold: Path | None) -> None:
        super().__init__(supports_session_load=True)
        self.failure = failure
        self.hold = hold
        self.on_prompt(self.answer)

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        begin_startup()
        await wait_for_release(self.hold)
        if startup_refused(self.failure):
            raise RequestError(-32000, STARTUP_FAILURE)
        return await super().initialize(protocol_version, client_capabilities, **kwargs)

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        session = await super().new_session(cwd, mcp_servers, **kwargs)
        record_cursor(CursorPeerEvent.SESSION_CREATED, session.session_id)
        hold = Path(CURSOR_SETUP_HOLD)
        if hold.exists():
            Path(CURSOR_SETUP_READY).touch()
            await wait_for_release(hold)
        return session

    async def answer(self, peer: FakeACPAgent, session_id: str) -> None:
        content = peer.prompt_texts()[-1]
        record_cursor(CursorPeerEvent.PROMPT_STARTED, content)
        if PeerCommand.EXIT_PROCESS in content:
            os._exit(0)
        if PeerCommand.LOSE_TRANSPORT in content:
            # A frame that is not JSON-RPC faults the client's reader while
            # this process stays alive.
            sys.stdout.write("[]\n")
            sys.stdout.flush()
            await asyncio.Future[None]()
        if workflow := CURSOR_WORKFLOW_PATTERN.search(content):
            await self.cursor_workflow(session_id, workflow.group(1))
            return
        request = file_request(content)
        result = perform(request["tool_name"], request["input"])
        await peer.call_mcp_tool(
            session_id=session_id,
            server=BAND_MCP_SERVER_NAME,
            tool_name=BandTool.SEND_MESSAGE,
            arguments={
                "content": reply_text(request["tool_name"], result),
                "mentions": ["@alice"],
            },
        )

    async def cursor_workflow(self, session_id: str, marker: str) -> None:
        """A native edit waits for both a Cursor question and shell permission."""
        await self.update_cursor_todos(
            session_id,
            f"{marker}: waiting",
            sessionless=True,
            merge=True,
            todo_prefix=marker,
        )
        result = await self.ask_cursor_question(
            session_id, CURSOR_QUESTION_ID, *CursorChoice
        )
        answers = result.get("outcome", {}).get("answers", [])
        selected = [
            option for answer in answers for option in answer["selectedOptionIds"]
        ]
        if selected != [CursorChoice.CONTINUE]:
            return
        record_cursor(CursorPeerEvent.QUESTION_ANSWERED, selected[0])
        permission = await self.ask_permission(
            session_id,
            ToolCallUpdate(tool_call_id="workspace-write", title="shell"),
            allow_or_reject(CURSOR_ALLOW_OPTION),
        )
        if not isinstance(permission.outcome, AllowedOutcome):
            return
        Path(WORKSPACE_NOTE).write_text(marker)
        record_cursor(CursorPeerEvent.FILE_WRITTEN, marker)
        await self.update_cursor_todos(
            session_id,
            f"{marker}: completed",
            sessionless=True,
            merge=True,
            todo_prefix=marker,
        )
        await self.call_mcp_tool(
            session_id=session_id,
            server=BAND_MCP_SERVER_NAME,
            tool_name=BandTool.SEND_MESSAGE,
            arguments={"content": marker, "mentions": ["@alice"]},
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(FAIL_STARTUP_ARG, type=Path)
    parser.add_argument(HOLD_STARTUP_ARG, type=Path)
    # OMP passes its room's workspace as a flag instead of the launch cwd.
    parser.add_argument(OMP_CWD_FLAG)
    # Each profile adds its own launch flags and subcommand.
    options, _ = parser.parse_known_args()
    if options.cwd is not None:
        os.chdir(options.cwd)
    await run_agent(WorkspacePeer(options.fail_startup, options.hold_startup))


if __name__ == "__main__":
    asyncio.run(main())
