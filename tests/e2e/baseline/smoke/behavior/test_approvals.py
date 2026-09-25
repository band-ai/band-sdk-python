"""Chat-mediated approval, live, for every coding agent that gates commands on a human.

Each cell compels one shell command whose only effect is writing a fresh marker to a
file, with the adapter in manual approval mode, so the command pauses on a room prompt.
The human then approves, declines, or never answers. The guarantees under test:

* the adapter *recognizes* the reply (sent with the platform's leading ``@handle``
  mention block, as a real user's is) and confirms the outcome in the room;
* the outcome decides the effect -- the marker lands only when approved; a decline or
  an expired wait never runs the command.

Each adapter is built bespoke (see ``smoke/samples/approvals.py``); ``cell`` supplies the
per-adapter lane and requirements. The agent's closing reply after the decision is the
barrier before the file check: some adapters release the room while a decision is
pending, so the trigger's ``processed`` status does not mean the command has settled,
and the platform streams no tool events to a user's socket to wait on instead.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/behavior/test_approvals.py -v -s --no-cov
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    Outcome,
    command_request,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Sequential live barriers: the request, then the decided turn's closing reply.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)
# Short enough to expire promptly, long enough that the request is captured first.
EXPIRING_WAIT_S = 10.0


def _wait_timeout_s(outcome: Outcome) -> float:
    """The adapter's approval wait: expiring for TIMEOUT, else outlasting both
    barriers (and still under Cursor's default turn timeout, which it must be)."""
    return EXPIRING_WAIT_S if outcome is Outcome.TIMEOUT else BUDGET.deadline_s * 2


@per_adapter(Adapter.CLAUDE_SDK, Adapter.CODEX, Adapter.CURSOR_ACP, Adapter.OPENCODE)
@pytest.mark.parametrize("outcome", list(Outcome))
@flaky_infra("a live coding-agent turn that must reach a shell tool use can time out")
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_the_room_reply_decides_whether_the_gated_command_runs(
    cell: AdapterCell,
    outcome: Outcome,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    dialect = DIALECTS[Adapter(cell.adapter_id)]
    for dep in dialect.extra_deps:
        require_dep(dep, cell.settings)
    marker = unique_marker("approval")

    with tempfile.TemporaryDirectory(
        prefix="band-e2e-approval-", dir=dialect.workdir_root(cell.settings)
    ) as workdir:
        target = Path(workdir) / "approval.txt"
        adapter = dialect.build(cell.settings, Path(workdir), _wait_timeout_s(outcome))
        async with running_provisioned_agent(
            adapter, cell.resources, label=f"approval-{outcome}"
        ) as agent:
            room_id = await cell.resources.provision_room(
                title=f"e2e-approval-{cell.adapter_id}-{outcome}",
                participants=[agent.id],
            )
            async with reply_capture(room_id) as capture:
                await user_ops.send_message(
                    room_id,
                    command_request(marker, target),
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                asked = await capture.wait_until(
                    lambda msgs: dialect.find_request(msgs) is not None,
                    deadline_s=BUDGET.deadline_s,
                )
                request = dialect.find_request(asked)
                assert request is not None  # the predicate guarantees one
                after_request = capture.messages.snapshot()

                if outcome is not Outcome.TIMEOUT:
                    await user_ops.send_message(
                        room_id,
                        dialect.reply(outcome, request),
                        mention_id=agent.id,
                        mention_name=agent.name,
                    )
                notice = dialect.notice(outcome, request)
                await capture.wait_until(
                    lambda _msgs: dialect.settled(
                        notice, capture.messages.since(after_request)
                    ),
                    deadline_s=BUDGET.deadline_s,
                )
                await notice.assert_shown(capture, agent.id)

        if outcome is Outcome.APPROVE:
            assert target.read_text().strip() == marker
        else:
            assert not target.exists(), f"{outcome} still ran the gated command"
