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

import asyncio
from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.types import Emit
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
    marker_command,
    written_lines,
)
from tests.e2e.baseline.smoke.samples.codexpeer import (
    NATIVE_DEADLINE_S,
    ShellPeer,
    closing_reply,
    native_client,
    required_approval,
    shell_peer,
    start_shell_turn,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    reasoning_joke_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.e2e.baseline.toolkit.builders import codex_config_kwargs
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

PLACEHOLDER_THOUGHTS = ("(reasoning)", "(plan)")


@lane(Lane.BACKENDS)
@requires(Dep.CODEX_CLI)
@pytest.mark.parametrize(
    "outcome", [Outcome.APPROVE, Outcome.DECLINE], ids=["approve", "decline"]
)
@pytest.mark.asyncio(loop_scope="session")
async def test_codex_shell_approval_is_required(
    baseline_settings: BaselineSettings,
    tmp_path: Path,
    outcome: Outcome,
) -> None:
    """A non-escalated write must ask before its effect, even when declined."""
    workdir = tmp_path.resolve()
    marker, done = unique_marker("write"), unique_marker("closed")
    target = workdir / "approval.txt"
    adapter = DIALECTS[Adapter.CODEX].build(
        baseline_settings,
        AgentSetup(workdir, NATIVE_DEADLINE_S),
    )
    assert isinstance(adapter, CodexAdapter)
    peer = ShellPeer(marker_command(marker, target), workdir, done)
    async with (
        shell_peer(peer) as url,
        native_client(adapter.config, workdir / "home", workdir, url) as client,
    ):
        async with asyncio.timeout(NATIVE_DEADLINE_S):
            await start_shell_turn(client, adapter.config, workdir)
            approval = await required_approval(client)
            assert not target.exists(), "Shell write occurred before approval"
            assert approval.id is not None
            decision = "accept" if outcome is Outcome.APPROVE else "decline"
            await client.respond(approval.id, {"decision": decision})
            reply, extra_approvals = await closing_reply(client)
    assert peer.calls == 1
    assert extra_approvals == 0, "One command requested more than one approval"
    assert reply == done
    if outcome is Outcome.APPROVE:
        assert written_lines(target) == [marker]
    else:
        assert not target.exists(), "Declined shell write was executed"


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
    matrix has no per-test hook for ``reasoning_summary``. Reuses the
    builder's own ``codex_config_kwargs`` for cwd/model/command so this test
    can't drift from the matrix's env-driven settings, adding only the one
    field the builder doesn't expose. Codex only returns a reasoning summary
    when one is explicitly requested -- leaving it unset (the matrix default)
    means every summary comes back empty and the adapter correctly drops
    every one rather than posting a placeholder, so this test would fail
    vacuously without requesting one itself.

    A completed reply proves the turn ran. Reasoning summaries are optional even
    when ``reasoning_summary="auto"`` is requested, so any observed thought must
    not carry the literal ``(reasoning)`` / ``(plan)`` placeholders the adapter
    used to emit for empty summaries. The user message uses
    ``reasoning_joke_instruction`` so
    ``name == marker`` and the ask itself invites reasoning (how a joke might be
    badly interpreted).
    """
    name = unique_marker("Sam")
    config_kwargs = codex_config_kwargs(baseline_settings, prompt=REPLY_PROMPT)
    config_kwargs["reasoning_summary"] = "auto"
    adapter = CodexAdapter(
        config=CodexAdapterConfig(**config_kwargs),
        emit=Emit.THOUGHTS,
    )

    identity = await resource_manager.provision_agent("codex-thoughts")
    room_id = await resource_manager.provision_room(
        title="e2e-codex-thoughts", participants=[identity.id]
    )
    async with (
        running_agent(identity, adapter, baseline_settings),
        reply_capture(room_id) as capture,
    ):
        mid = await user_ops.send_message(
            room_id,
            reasoning_joke_instruction(name),
            mention_id=identity.id,
            mention_name=identity.name,
        )
        replies = await capture.wait_for_reply(mid, identity.id)
        thoughts = await capture.thoughts(sender_id=identity.id)

    replies.assert_contains_any([name])
    thoughts.assert_contains_none(PLACEHOLDER_THOUGHTS)
