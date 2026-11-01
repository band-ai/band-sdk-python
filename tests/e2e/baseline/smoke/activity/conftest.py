"""Owned loopback work and native-reader diagnostics for activity qualification."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.workspaces import create_room_workspace_resolver
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import SLOW_TURN_DEADLINE_S
from tests.e2e.baseline.toolkit.nativetasks import NativeTasks
from tests.e2e.baseline.toolkit.resultexchange import ResultExchange

if TYPE_CHECKING:
    from band.adapters.claude_sdk import ClaudeSDKAdapter


@pytest.fixture
def native_claude_adapter(
    baseline_settings: BaselineSettings, tmp_path: Path
) -> Callable[[str], ClaudeSDKAdapter]:
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- optional Claude lane
        ClaudeApprovalOptions,
        ClaudeSDKAdapter,
        ClaudeSDKAdapterConfig,
    )

    def build(prompt: str) -> ClaudeSDKAdapter:
        return ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(
                model=baseline_settings.llm_models.anthropic_model,
                custom_section=prompt,
                approvals=ClaudeApprovalOptions(mode="auto_accept"),
            ),
            workspace_for_room=create_room_workspace_resolver(tmp_path),
            emit=frozenset(),
        )

    return build


@pytest.fixture
async def result_exchange() -> AsyncIterator[ResultExchange]:
    async with ResultExchange(deadline_s=SLOW_TURN_DEADLINE_S).running() as exchange:
        yield exchange


@pytest.fixture
async def claude_native_tasks() -> AsyncIterator[NativeTasks]:
    # Other dependency lanes collect this file without the Claude SDK extra.
    from band.integrations.claude_sdk import activity  # noqa: PLC0415

    capture = NativeTasks(
        task_field=activity.NATIVE_TASK_LIFECYCLE,
        terminal_statuses=activity.TERMINAL_TASK_STATUSES,
        deadline_s=SLOW_TURN_DEADLINE_S,
    )
    with capture.observing(activity):
        yield capture
