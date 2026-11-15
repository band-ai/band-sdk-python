"""Real room contexts backed by explicit Fern REST responses."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from band_rest import ChatMessage

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.runtimecontrol.kit import never_executes


@pytest.fixture
def history_context() -> Callable[..., ExecutionContext]:
    """Build a context that hydrates the supplied platform rows for bootstrap."""

    def create(rows: list[dict[str, Any]], *, hydrate: bool = True) -> ExecutionContext:
        rest = SimpleNamespace(
            agent_api_context=SimpleNamespace(
                get_agent_chat_context=AsyncMock(
                    return_value=SimpleNamespace(
                        data=[ChatMessage.model_validate(row) for row in rows]
                    )
                )
            ),
            agent_api_participants=SimpleNamespace(
                list_agent_chat_participants=AsyncMock(
                    return_value=SimpleNamespace(data=[])
                )
            ),
        )
        return ExecutionContext(
            "room-1",
            SimpleNamespace(rest=rest),
            never_executes,
            config=SessionConfig(enable_context_hydration=hydrate),
        )

    return create
