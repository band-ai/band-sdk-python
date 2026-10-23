"""Public adapter construction and observations for native terminal declines."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import create_model

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName, OmpFrameType
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools import BandTool, ToolCallOutcome
from band.testing import FakeAgentTools
from tests.adapters.omp.declinepeer import DECLINE_TRANSCRIPT, DeclineScenario
from tests.paths import REPO_ROOT

CustomDeclineInput = create_model(f"{BandTool.NO_REPLY}Input")


def custom_decline() -> str:
    return "Custom observation completed"


class ConcurrentDeclineTools(FakeAgentTools):
    def __init__(self) -> None:
        super().__init__()
        self.started = 0
        self.both_started = asyncio.Event()

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        self.started += 1
        if self.started == 2:
            self.both_started.set()
        await self.both_started.wait()
        return await super().execute_tool_call_structured(tool_name, arguments)


class DeclineTools(FakeAgentTools):
    def __init__(self, *, fail_decline: bool = False) -> None:
        super().__init__()
        self.fail_decline = fail_decline

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        if tool_name == BandTool.NO_REPLY and self.fail_decline:
            return ToolCallOutcome(value="Decline rejected", ok=False)
        outcome = await super().execute_tool_call_structured(tool_name, arguments)
        if tool_name == BandTool.SEND_MESSAGE:
            await self.send_message(**arguments)
        return outcome


@asynccontextmanager
async def declining_adapter(
    tmp_path: Path,
    scenario: DeclineScenario,
    *,
    additional_tools: list[CustomToolDef] | None = None,
    exclude_tools: tuple[str, ...] = (),
) -> AsyncIterator[OmpAdapter]:
    adapter = OmpAdapter(
        OmpAdapterConfig(
            command=(
                sys.executable,
                "-u",
                str(REPO_ROOT / "tests" / "adapters" / "omp" / "declinepeer.py"),
                scenario,
                str(tmp_path / DECLINE_TRANSCRIPT),
            ),
            env={"PYTHONPATH": str(REPO_ROOT)},
            cwd=str(tmp_path),
        ),
        additional_tools=additional_tools,
        exclude_tools=exclude_tools,
    )
    try:
        yield adapter
    finally:
        await adapter.cleanup_all()


def tool_results(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        frame
        for frame in wire_frames(tmp_path)
        if frame["type"] == OmpFrameType.HOST_TOOL_RESULT
    ]


def stop_commands(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        frame
        for frame in wire_frames(tmp_path)
        if frame["type"] == OmpCommandName.ABORT
    ]


def wire_frames(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (tmp_path / DECLINE_TRANSCRIPT).read_text().splitlines()
    ]
