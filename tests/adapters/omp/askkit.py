"""Turn drivers and wire observations for native room asks."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEvent,
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.testing import FakeAgentTools
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.kit import checkpoint_frames


async def ask_turn(
    adapter: OmpAdapter,
    tools: FakeAgentTools,
    *,
    bootstrap: bool = True,
    content: str = "Ask me which database to use",
) -> None:
    await adapter.run_judged_turn(
        msg=make_platform_message(content),
        tools=tools,
        history=OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id="room-123",
    )


async def ask_frames(
    adapter: OmpAdapter, *frame_types: OmpCommandName | OmpFrameType
) -> list[dict[str, JsonValue]]:
    frames = await checkpoint_frames(adapter._rooms["room-123"].client)
    return [frame for frame in frames if frame["type"] in frame_types]


async def ask_session_changes(adapter: OmpAdapter) -> list[dict[str, JsonValue]]:
    return await ask_frames(
        adapter, OmpCommandName.NEW_SESSION, OmpCommandName.SWITCH_SESSION
    )


def room_ask_adapter(tmp_path: Path, scenario: str = "ask") -> OmpAdapter:
    return OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.integrations.ompaskpeer",
                scenario,
            ),
        ),
        emit=(),
    )


class AskStartupObserver:
    """Observe real native startup, prompt admission, and process retirement."""

    def __init__(self) -> None:
        self.processes: list[asyncio.subprocess.Process] = []
        self.prompted = False

    def __call__(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> OmpRpcClient:
        return ObservedAskClient(command, env=env, observer=self)

    async def create_process(
        self, *command: str, **kwargs: Any
    ) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(*command, **kwargs)
        self.processes.append(process)
        return process

    async def assert_released(self) -> None:
        assert self.processes
        for process in self.processes:
            assert process.returncode is not None
            assert await process.wait() == process.returncode


class ObservedAskClient(OmpRpcClient):
    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None,
        observer: AskStartupObserver,
    ) -> None:
        super().__init__(command, env=env, create_process=observer.create_process)
        self.observer = observer

    async def prompt(
        self,
        message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        self.observer.prompted = True
        await super().prompt(message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)
