"""OpenCode showcase smoke — the manual permission-approval room round trip.

The generic matrix runs OpenCode in ``approval_mode="auto_accept"`` (headless
rooms have no approver; see ``toolkit/builders.py``), so the *manual* relay —
OpenCode blocks on a ``permission.asked``, the adapter posts an ``approve <id>``
prompt to the room, a human replies, the turn resumes — is otherwise never
exercised live. That relay is the whole reason ``RoomApprovals`` exists, and the
reply arrives carrying the platform's leading ``@handle`` mention block, so this
is the true end-to-end guard for reading a mentioned reply.

Construction is bespoke (the matrix builder hardcodes auto_accept and its
``prompt``/``features``/``tools`` contract can't express ``approval_mode``), so —
like ``test_copilot_sdk.py`` — there is no ``@with_adapters``/``@per_adapter``
binding; gating is explicit (``@requires(Dep.OPENCODE_SERVER)``) and the home lane
is pinned with ``@lane(Lane.BACKENDS)``.

Requires an OpenCode server whose permission rules gate a shell/edit tool to
``ask`` (so a real ``permission.asked`` fires); the adapter's ``approval_mode``
only decides how the SDK *responds*, not when the server asks.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_opencode.py -v -s --no-cov
"""

from __future__ import annotations

import re
import uuid

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

_APPROVE_ID = re.compile(r"`approve (\S+?)`")


def _manual_opencode_adapter(settings: BaselineSettings):
    """The matrix builder's OpenCode config, but in manual approval mode."""
    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.backends.opencode_base_url,
            provider_id=settings.backends.opencode_provider_id,
            model_id=settings.backends.opencode_model_id,
            custom_section="Keep responses short. Use your shell tool when asked.",
            approval_mode="manual",
        )
    )


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling here
@requires(Dep.OPENCODE_SERVER)
@flaky_infra("real permission round trip plus two live turns can time out transiently")
@pytest.mark.timeout(extra=180)  # ask turn + resumed turn
@pytest.mark.asyncio(loop_scope="session")
async def test_manual_permission_approved_from_room_resumes_the_turn(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A gated tool pauses the turn; the user's ``approve <id>`` reply resumes it.

    The command echoes a high-entropy token the model can't invent, so the
    resumed turn relaying it proves the tool actually ran *after* approval — i.e.
    the mentioned ``approve <id>`` reply was recognized and the session resumed.
    """
    secret = f"tok-{uuid.uuid4().hex[:8]}"
    adapter = _manual_opencode_adapter(baseline_settings)

    async with running_provisioned_agent(
        adapter, resource_manager, label="opencode-manual-approval"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-opencode-manual-approval", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            # Turn 1: the tool use trips a permission ask; the adapter must post
            # an approval-request prompt (and pause), not a final answer.
            mark = capture.messages.snapshot()
            trigger = await user_ops.send_message(
                room_id,
                f"Run the shell command `echo {secret}` and reply with its exact "
                "output. Actually execute it; do not answer from memory.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_reply(
                trigger,
                agent.id,
                since=mark,
                deadline_s=baseline_settings.e2e_timeout * 2,
            )
            approval = next(
                m
                for m in capture.messages
                if "approval requested" in (m.content or "").lower()
            )
            match = _APPROVE_ID.search(approval.content or "")
            assert match, f"no approve <id> in approval prompt: {approval.content!r}"
            request_id = match.group(1)

            # Turn 2: approve it. The reply is delivered with the platform's
            # @handle mention block prepended -- the shape the fix must handle.
            mark = capture.messages.snapshot()
            approve = await user_ops.send_message(
                room_id,
                f"approve {request_id}",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(
                approve,
                agent.id,
                since=mark,
                deadline_s=baseline_settings.e2e_timeout * 2,
            )
            # Only the tool output carries the secret; relaying it proves the
            # turn resumed and the gated command ran after approval.
            replies.assert_contains_any([secret])
