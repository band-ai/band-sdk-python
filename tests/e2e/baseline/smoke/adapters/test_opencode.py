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
binding; gating is explicit (``@requires``) and the home lane is pinned with
``@lane(Lane.BACKENDS)``.

The hard prerequisite is a serve whose permission rules gate a shell/edit tool to
``ask`` (so a real ``permission.asked`` fires); the adapter's ``approval_mode``
only decides how the SDK *responds*, not when the server asks. That is gated by
``Dep.OPENCODE_BASH_ASKS`` so a differently-configured serve fails naming the
reason instead of stalling every barrier.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_opencode.py -v -s --no-cov
"""

from __future__ import annotations

import re

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_REQUESTED_PREFIX,
)
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

# The adapter's approval narration comes from ``RoomApprovals`` itself, so a reworded
# prompt or confirmation cannot silently turn this smoke into a deadline stall: the
# prompt is anchored on the adapter's own prefix, and the confirmation is matched as
# the exact line its template renders. Only the ``approve <id>`` fragment is local --
# it is the room command vocabulary, not the narration.
APPROVAL_ASKED = re.compile(
    re.escape(APPROVAL_REQUESTED_PREFIX) + r" `bash`.*?`approve (\S+?)`", re.S
)

# Two sequential live turns: the gated tool use, then the resumed turn.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


def _asked_id(messages: list[MessageCreatedPayload]) -> str | None:
    """The request id from the adapter's approval prompt, if it posted one."""
    matches = (APPROVAL_ASKED.search(m.content or "") for m in messages)
    return next((m.group(1) for m in matches if m), None)


def _handled(messages: list[MessageCreatedPayload], request_id: str) -> bool:
    """Whether the adapter confirmed it answered ``request_id`` with ``once``."""
    expected = APPROVAL_HANDLED_TEMPLATE.format(request_id=request_id, reply="once")
    return any(expected in (m.content or "") for m in messages)


def _manual_opencode_adapter(settings: BaselineSettings):
    """The matrix builder's OpenCode config, but in manual approval mode."""
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
@requires(Dep.OPENCODE_SERVER, Dep.OPENCODE_BASH_ASKS)
@flaky_infra("one free-model round trip to trigger the bash tool can time out")
@pytest.mark.timeout(extra=BUDGET.extra_s)
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
            asked = await capture.wait_until(
                lambda msgs: _asked_id(msgs) is not None,
                deadline_s=BUDGET.deadline_s,
            )
            request_id = _asked_id(asked)
            assert request_id is not None  # the predicate guarantees one

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
                lambda msgs: _handled(msgs, request_id),
                deadline_s=BUDGET.deadline_s,
            )
