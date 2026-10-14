"""Fixtures for the Cursor ACP adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from band.adapters.cursor_acp import (
    CursorACPAdapter,
    CursorACPAdapterConfig,
)
from band.core.types import Capability
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools.types import BandTool
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    cursor_custom_tools,
    echo,
)
from tests.adapters.cursor_acp.process import CursorProcess
from tests.adapters.lifecycle.kit import hosted
from tests.baseline.adapter import Adapter
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import pair_in_process
from tests.integrations.acp.lifecycleprofile import PROFILES


@pytest_asyncio.fixture(loop_scope="function")
async def cursor_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[CursorProcess]:
    profile = next(
        profile for profile in PROFILES if profile.name == Adapter.CURSOR_ACP
    )
    async with hosted(profile, tmp_path, monkeypatch) as host:
        host.shared = False
        yield CursorProcess(host)


# The adapter's ACP runtime lives on the loop that started it, so setup, the
# test and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def cursor_room() -> AsyncIterator[Callable[..., Awaitable[CursorRoom]]]:
    adapters: list[CursorACPAdapter] = []

    async def open_room(
        agent: FakeACPAgent,
        *,
        capabilities: set[Capability] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **config: Any,
    ) -> CursorRoom:
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(**config),
            capabilities=capabilities,
            additional_tools=additional_tools,
        )
        pair_in_process(adapter, agent)
        await adapter.on_started("Cursor", "Cursor agent under test")
        adapters.append(adapter)
        return CursorRoom(adapter, agent)

    yield open_room
    for adapter in adapters:
        await adapter.stop()


@pytest.fixture
def cursor_approval_room(
    cursor_room: Callable[..., Awaitable[CursorRoom]],
) -> Callable[[str, str], Awaitable[CursorRoom]]:
    async def open_room(title: str, approval_mode: str) -> CursorRoom:
        return await cursor_room(
            FakeACPAgent()
            .will_ask_permission(title=title)
            .will_call_mcp_tool(
                "reply",
                BandTool.SEND_MESSAGE,
                arguments={"content": "finished", "mentions": ["@user-1"]},
            ),
            approval_mode=approval_mode,
            capabilities={Capability.MEMORY},
            additional_tools=cursor_custom_tools(echo),
            decision_timeout_s=0.1,
        )

    return open_room
