"""Claude SDK showcase smoke — the manual chat-based approval round trip.

The generic matrix runs claude_sdk with no ``approval_mode`` set (the SDK's own
``permission_mode`` governs instead; see ``toolkit/builders.py``), so the chat-based
manual relay -- Claude Code's native ``Bash``/``Write``/``Edit`` tools gated through
``can_use_tool``, the adapter posting an ``/approve <token>`` prompt to the room, a
human replying, the turn resuming -- is otherwise never exercised live. Band's own
MCP tools (and any caller ``additional_tools``) are swept into ``allowed_tools`` and
so bypass ``can_use_tool`` entirely; only the CLI's native tools are actually gated,
which is why this smoke compels a ``Bash`` use specifically.

Construction is bespoke (the matrix builder never sets ``approval_mode`` and its
``prompt``/``features``/``tools`` contract can't express it either), so — like
``test_copilot_sdk.py`` -- there is no ``@with_adapters``/``@per_adapter`` binding;
gating is explicit (``@requires``) and the home lane is pinned with
``@lane(Lane.CORE)`` (claude_sdk is core-lane, not backends).

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=core uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_claude_sdk.py -v -s --no-cov
"""

from __future__ import annotations

import re
import tempfile

import pytest

from band.adapters.claude_sdk import APPROVAL_RESOLVED_TEMPLATE, ClaudeSDKAdapter
from band.client.streaming import MessageCreatedPayload
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Anchored on the literal text wrapping "{token}" in APPROVAL_REQUESTED_TEMPLATE
# ("Token: `{token}`.") -- not derived from the template itself (same tradeoff
# test_opencode.py accepts for its room-command syntax), so a reworded prompt
# fails this test loudly rather than silently drifting.
TOKEN_RE = re.compile(r"Token: `(\S+?)`\.")

# Two sequential live turns: the gated tool use, then the resumed turn.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


def _requested_token(messages: list[MessageCreatedPayload]) -> str | None:
    """The approval token from the adapter's request prompt, if it posted one."""
    matches = (TOKEN_RE.search(m.content or "") for m in messages)
    return next((m.group(1) for m in matches if m), None)


def _resolved(messages: list[MessageCreatedPayload], token: str) -> bool:
    """Whether the adapter confirmed ``token`` was resolved as accepted."""
    expected = APPROVAL_RESOLVED_TEMPLATE.format(token=token, decision="accept")
    return any(expected in (m.content or "") for m in messages)


@lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
@requires(Dep.ANTHROPIC)
@flaky_infra("one live Claude Code turn to trigger a Bash tool use can time out")
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_manual_bash_approval_resolved_from_a_mentioned_reply(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A gated ``Bash`` use pauses the turn; a mentioned ``/approve <token>`` resolves it.

    ``approval_mode="manual"`` routes every native-tool ``can_use_tool`` call
    through the chat relay, so compelling a ``Bash`` use raises a real approval
    request and the adapter posts ``APPROVAL_REQUESTED_TEMPLATE``. The reply is
    delivered with the platform's leading ``@handle`` mention block, and
    ``_extract_command`` strips that before matching ``/approve`` -- so the
    adapter actually *recognizing* the reply and posting
    ``APPROVAL_RESOLVED_TEMPLATE`` is the end-to-end guard, not just that a
    message was sent.

    The resumed tool output is deliberately not asserted: whether the model
    re-runs the command and relays it is model-dependent, whereas recognizing
    the reply and resolving the approval is the relay's actual guarantee (same
    rationale as ``test_opencode.py``'s manual-approval smoke).
    """
    with tempfile.TemporaryDirectory(
        prefix="band-e2e-claude-sdk-manual-approval-"
    ) as sandbox:
        adapter = ClaudeSDKAdapter(
            model=baseline_settings.llm_models.anthropic_model,
            custom_section="Keep responses short. Use your Bash tool when asked.",
            cwd=sandbox,
            approval_mode="manual",
        )

        async with running_provisioned_agent(
            adapter, resource_manager, label="claude-sdk-manual-approval"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-claude-sdk-manual-approval", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                # Turn 1: compel a Bash tool use -> gated by can_use_tool -> approval prompt.
                await user_ops.send_message(
                    room_id,
                    "Use your Bash tool to run exactly `echo ok`. You must execute "
                    "it with the tool, not answer from memory.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                asked = await capture.wait_until(
                    lambda msgs: _requested_token(msgs) is not None,
                    deadline_s=BUDGET.deadline_s,
                )
                token = _requested_token(asked)
                assert token is not None  # the predicate guarantees one

                # Turn 2: the mentioned `/approve <token>` reply must be RECOGNIZED.
                await user_ops.send_message(
                    room_id,
                    f"/approve {token}",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                await capture.wait_until(
                    lambda msgs: _resolved(msgs, token),
                    deadline_s=BUDGET.deadline_s,
                )
