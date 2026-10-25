"""Drive real adapter callbacks and project the native mailbox transcript."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import JsonValue

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
    OmpPermissionRequest,
    PermissionResolver,
)
from band.core.model_catalog import ModelSelection
from band.integrations.omp import OMP_FORM_APPROVE
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import OmpCommandName, OmpFrameType
from band.runtime.mailbox import RoomMailbox
from band.runtime.tools import BandTool, ToolCallOutcome
from band.testing import FakeAgentTools
from tests.adapters.hold import Hold
from tests.adapters.omp.mailboxpeer import (
    CALLBACK_MODEL,
    FIRST_CALLBACK,
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    SECOND_MODEL,
)
from tests.integrations.omprpc.kit import checkpoint_frames
from tests.paths import REPO_ROOT

Result = TypeVar("Result")


class MailboxTools(FakeAgentTools):
    """Deliver native send-message calls through the observable room tools."""

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        outcome = await super().execute_tool_call_structured(tool_name, arguments)
        if tool_name == BandTool.SEND_MESSAGE:
            await self.send_message(**arguments)
        return outcome


def mailbox_adapter(
    tmp_path: Path,
    resolver: PermissionResolver | None = None,
    *,
    callback_turn: int = 1,
    callback_count: int = 1,
    transcript: Path | None = None,
    initial_tool: bool = False,
) -> OmpAdapter:
    return OmpAdapter(
        OmpAdapterConfig(
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.adapters.omp.mailboxpeer",
                str(callback_turn),
                str(callback_count),
                str(transcript) if transcript is not None else "",
                OmpFrameType.HOST_TOOL_CALL
                if initial_tool
                else OmpFrameType.UI_REQUEST,
            ),
            env={"PYTHONPATH": str(REPO_ROOT)},
            cwd=str(tmp_path),
            model=f"{MODEL_PROVIDER}/{ORIGINAL_MODEL}",
        ),
        resolve_permission=resolver,
        emit=(),
    )


def room_client(adapter: OmpAdapter, room_id: str = "room-123") -> OmpRpcClient:
    return adapter._rooms[room_id].client


async def peer_frames(
    adapter: OmpAdapter, room_id: str = "room-123"
) -> list[dict[str, JsonValue]]:
    return await checkpoint_frames(room_client(adapter, room_id))


def received_ui_responses(transcript: Path) -> list[str]:
    """Read permission responses received by a particular native connection."""
    return received_callback_responses(transcript, OmpFrameType.UI_RESPONSE)


def received_callback_responses(transcript: Path, kind: OmpFrameType) -> list[str]:
    """Read responses received by this process, including after cancellation."""
    frames = [json.loads(line) for line in transcript.read_text().splitlines()]
    return [frame["id"] for frame in frames if frame["type"] == kind]


def selection_order(frames: list[dict[str, JsonValue]]) -> list[str]:
    return [
        str(
            frame["modelId"]
            if frame["type"] == OmpCommandName.SET_MODEL
            else frame["id"]
        )
        for frame in frames
        if frame["type"] in (OmpCommandName.SET_MODEL, OmpFrameType.UI_RESPONSE)
    ]


class CallbackResolver:
    """Hold a permission before selecting through the adapter's public API."""

    def __init__(self, *, held: bool = False) -> None:
        self.adapter: OmpAdapter | None = None
        self.first = Hold()
        if not held:
            self.first.released.set()
        self.calls: list[str] = []

    async def __call__(self, request: OmpPermissionRequest) -> str:
        self.calls.append(request.title)
        if request.title == FIRST_CALLBACK:
            await self.first.wait()
        assert self.adapter is not None
        model = CALLBACK_MODEL if request.title == FIRST_CALLBACK else SECOND_MODEL
        await self.adapter.apply_model_selection(
            ModelSelection(model=f"{MODEL_PROVIDER}/{model}"), room_id=request.room_id
        )
        return OMP_FORM_APPROVE


class AdmissionMailbox(RoomMailbox):
    """Expose admitted production jobs without replacing their scheduler."""

    def __init__(self) -> None:
        super().__init__()
        self.admitted: asyncio.Queue[str] = asyncio.Queue()

    async def ask(self, room_id: str, run: Callable[[], Awaitable[Result]]) -> Result:
        self.admitted.put_nowait(room_id)
        return await super().ask(room_id, run)

    def discard_admissions(self) -> None:
        while not self.admitted.empty():
            self.admitted.get_nowait()
