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
@flaky_infra("one free-model round trip to trigger the bash tool can time out")
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_manual_bash_permission_approved_from_a_mentioned_reply(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A gated ``bash`` use pauses the turn; a mentioned ``approve <id>`` is recognized.

    The server gates ``bash`` to ``ask`` (setup-opencode.sh), so a shell tool use
    raises a real ``permission.asked`` and the adapter relays an ``approve <id>``
    prompt. The user's reply is delivered with the platform's leading ``@handle``
    mention block, so the adapter *recognizing* it -- posting ``approval `<id>`
    handled with `once``` -- is the end-to-end guard for ``strip_leading_mentions``:
    pre-fix the mention block hid the command and the reply was silently forwarded
    as a new prompt, so no confirmation would ever appear. The id echoed back in the
    confirmation also proves the (mixed-case) request id parsed intact.

    The resumed tool output is deliberately not asserted: whether the free model
    re-runs the command and relays it is model-dependent, whereas recognizing the
    reply and answering the permission is the fix's actual guarantee.
    """
    adapter = _manual_opencode_adapter(baseline_settings)
    deadline = baseline_settings.e2e_timeout * 2

    async with running_provisioned_agent(
        adapter, resource_manager, label="opencode-manual-approval"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-opencode-manual-approval", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            # Turn 1: compel a shell tool use -> gated to `ask` -> approval prompt.
            await user_ops.send_message(
                room_id,
                "Use your bash/shell tool to run exactly `echo ok`. You must "
                "execute it with the shell tool, not answer from memory.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_until(
                lambda msgs: any(
                    "approval requested for `bash`" in (m.content or "").lower()
                    for m in msgs
                ),
                deadline_s=deadline,
            )
            approval = next(
                m
                for m in capture.messages
                if "approval requested for `bash`" in (m.content or "").lower()
            )
            match = _APPROVE_ID.search(approval.content or "")
            assert match, f"no approve <id> in approval prompt: {approval.content!r}"
            request_id = match.group(1)

            # Turn 2: the mentioned `approve <id>` reply must be RECOGNIZED. The
            # adapter's `handled with once` confirmation echoing the parsed id is
            # the guard -- pre-fix the mention block hid the command entirely.
            await user_ops.send_message(
                room_id,
                f"approve {request_id}",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_until(
                lambda msgs: any(
                    request_id in (m.content or "")
                    and "handled with `once`" in (m.content or "").lower()
                    for m in msgs
                ),
                deadline_s=deadline,
            )
