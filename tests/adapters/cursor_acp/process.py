"""Drive and observe Cursor decisions through its real subprocess profile."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from band.adapters.cursor_acp import CursorACPAdapter
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.waiting import UNTIL_TIMEOUT_S, until
from tests.framework_conformance.turnprobes import DispatchingFakeTools
from tests.integrations.acp.acp_toolkit.harness import (
    TranscriptTools,
    accepted_work_finished,
    decision_token,
)
from tests.integrations.acp.acp_toolkit.lifecycle import LifecycleChild
from tests.integrations.acp.acp_toolkit.workspacepeer import (
    CURSOR_ALLOW_OPTION,
    CURSOR_JOURNAL,
    CURSOR_QUESTION_ID,
    CursorChoice,
    CursorPeerEvent,
    CursorPeerRecord,
)
from tests.integrations.acp.roomownership.kit import questions


class RecoveryClose(StrEnum):
    SETTLED = "settled-recovery"
    REFUSED_JOIN = "refused-close-joined-by-successor"


class CursorProcessTools(DispatchingFakeTools, TranscriptTools):
    """The peer's MCP tools post through the same transcript as its decisions."""

    def __init__(self, room_id: str) -> None:
        super().__init__()
        self.room_id = room_id


def continue_question(token: str) -> str:
    return (
        f"{ROOM_COMMAND} {CursorCommandWord.ANSWER} {token} "
        f"{CURSOR_QUESTION_ID}={CursorChoice.CONTINUE}"
    )


def allow_write(token: str) -> str:
    return f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {token} {CURSOR_ALLOW_OPTION}"


async def asked_question(tools: CursorProcessTools) -> str:
    await until(
        lambda: bool(questions(tools.reply)), failure="Cursor never asked a question"
    )
    [prompt] = questions(tools.reply)
    return decision_token(prompt)


@dataclass
class CursorProcess:
    host: RetentionHost

    @property
    def adapter(self) -> CursorACPAdapter:
        return cast(CursorACPAdapter, self.host.adapter)

    def workspace(self, room: str = "room-a") -> Path:
        holder = self.adapter._rooms.get(room)
        workspace = (
            holder.workspace
            if holder is not None and holder.workspace is not None
            else self.host.workspace(room)
        )
        return Path(workspace)

    async def send(self, content: str, room: str = "room-a") -> CursorProcessTools:
        tools = CursorProcessTools(room)
        async with asyncio.timeout(UNTIL_TIMEOUT_S):
            await self.host.deliver(room, content, tools)
        return tools

    async def finished(self, room: str = "room-a") -> None:
        async with asyncio.timeout(UNTIL_TIMEOUT_S):
            await accepted_work_finished(self.adapter._rooms[room])

    async def kill_child(self, number: int) -> None:
        child = cast(LifecycleChild, self.host.children[number])
        child.process.kill()
        async with asyncio.timeout(UNTIL_TIMEOUT_S):
            await child.process.wait()

    def observed(self, event: CursorPeerEvent, room: str = "room-a") -> list[str]:
        path = self.workspace(room) / CURSOR_JOURNAL
        if not path.exists():
            return []
        records = [
            CursorPeerRecord.model_validate_json(line)
            for line in path.read_text().splitlines()
        ]
        return [record.value for record in records if record.event == event]
