"""Codex showcase smokes — adapter-native thought emission on the live backends lane.

The generic matrix runs Codex without ``Emit.THOUGHTS`` (builder features default
to ``None``, so only config-boolean ``TASK_EVENTS`` lands). These smokes turn
thoughts on and assert the room never receives placeholder noise from empty
reasoning/plan items — the live symptom of empty-summary fallbacks posting
``(reasoning)`` / ``(plan)``.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_codex.py -v -s --no-cov
"""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.types import AdapterFeatures, Emit

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    reasoning_joke_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

PLACEHOLDER_THOUGHTS = ("(reasoning)", "(plan)")


@lane(Lane.BACKENDS)  # bespoke config exposes no framework; pin scheduling to backends
@requires(Dep.CODEX_CLI, Dep.CODEX_CWD)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.timeout(extra=180)  # Codex cold start + one reasoning turn
@pytest.mark.asyncio(loop_scope="session")
async def test_codex_thoughts_are_not_placeholders(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """With ``Emit.THOUGHTS`` on and a reasoning summary actually requested, empty
    reasoning/plan items must not spam the room.

    Bespoke construction (bypassing the shared registry builder) because the
    matrix has no per-test hook for ``reasoning_summary``: the generic matrix
    builder only derives ``CodexAdapterConfig`` from env-driven settings
    (``codex_cwd``/``codex_model``/``codex_command``), and Codex only returns a
    reasoning summary when one is explicitly requested -- leaving it unset (the
    matrix default) means every summary comes back empty and the adapter
    correctly drops every one rather than posting a placeholder, so this test
    would fail vacuously without requesting one itself.

    A completed reply proves the turn ran. At least one thought event must have
    landed — otherwise the placeholder assertion below would pass vacuously
    without ever exercising the fix — and none of them may carry the literal
    ``(reasoning)`` / ``(plan)`` placeholders the adapter used to emit for empty
    summaries. The user message uses ``reasoning_joke_instruction`` so
    ``name == marker`` and the ask itself invites reasoning (how a joke might be
    badly interpreted).
    """
    name = unique_marker("Sam")
    config_kwargs: dict[str, Any] = {
        "cwd": baseline_settings.backends.codex_cwd,
        "custom_section": REPLY_PROMPT,
        "reasoning_summary": "auto",
    }
    if baseline_settings.backends.codex_model.strip():
        config_kwargs["model"] = baseline_settings.backends.codex_model
    if baseline_settings.backends.codex_command.strip():
        config_kwargs["codex_command"] = tuple(
            baseline_settings.backends.codex_command.split()
        )
    adapter = CodexAdapter(
        config=CodexAdapterConfig(**config_kwargs),
        features=AdapterFeatures(emit={Emit.THOUGHTS}),
    )

    identity = await resource_manager.provision_agent("codex-thoughts")
    room_id = await resource_manager.provision_room(
        title="e2e-codex-thoughts", participants=[identity.id]
    )
    async with running_agent(identity, adapter, baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                reasoning_joke_instruction(name),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(mid, identity.id)
            thoughts = await capture.thoughts(sender_id=identity.id)

    replies.assert_contains_any([name])
    thoughts.assert_at_least(1)
    thoughts.assert_contains_none(PLACEHOLDER_THOUGHTS)
