"""Observation fixtures: the reply/event capture and the LLM judge, each with its
plumbing (WS observer, deadline; the judge client + model) pre-bound."""

from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from anthropic import AsyncAnthropic

from tests.e2e.baseline.requires import Dep, require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.capture import reply_capture as _reply_capture
from tests.e2e.baseline.toolkit.judge import Verdict
from tests.e2e.baseline.toolkit.judge import judge as _judge
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

__all__ = ["judge", "reply_capture"]


@pytest.fixture
async def judge(
    baseline_settings: BaselineSettings,
) -> AsyncGenerator[Callable[..., Awaitable[Verdict]], None]:
    """LLM judge with the client + model pre-bound; call with criteria/transcript.

    Self-gates on its provider key so any test using it fails with the
    requirement reason when the key is absent — the requirement travels with the
    fixture. The Anthropic client is built once here (and closed on teardown)
    rather than per verdict.

    Usage::

        verdict = await judge(criteria="...", transcript="...")
    """
    require_dep(Dep.ANTHROPIC, baseline_settings)
    async with AsyncAnthropic(
        api_key=baseline_settings.llm_credentials.anthropic_api_key
    ) as client:
        yield functools.partial(
            _judge, client=client, model=baseline_settings.llm_models.judge_model
        )


@pytest.fixture
def reply_capture(
    baseline_ws: TrackingWebSocketClient,
    baseline_settings: BaselineSettings,
    user_ops: UserOps,
) -> CaptureFactory:
    """Subscribe-before-send capture with the WS observer + E2E_TIMEOUT pre-bound.

    Hides ``baseline_ws`` from tests; use as ``async with reply_capture(room_id)``.
    The capture's default wait deadline comes from E2E_TIMEOUT. ``user_ops`` and
    ``settings`` are pre-bound so ``capture.tool_calls()`` / ``events()`` /
    ``memory(agent)`` can read persisted events and agent-scoped memory.
    """
    return functools.partial(
        _reply_capture,
        baseline_ws,
        user_ops=user_ops,
        settings=baseline_settings,
        deadline_s=baseline_settings.e2e_timeout,
    )
