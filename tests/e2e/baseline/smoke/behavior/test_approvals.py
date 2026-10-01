"""Chat-mediated approval, live, for every coding agent that gates commands on a human.

Each flow compels shell commands whose only effect is writing a fresh marker to a
file, with the adapter in manual approval mode, so each command pauses on a room
prompt. Humans in the room then answer the way real ones do. The guarantees under
test:

* the adapter *recognizes* each reply (sent with the platform's leading ``@handle``
  mention block, as a real user's is) and confirms the outcome in the room;
* the outcome decides the effect -- a marker lands only when its command was
  approved; a decline, a refused approver, or an expired wait never runs it;
* a reply that arrives too late is told so, never "resolved";
* what only some agents offer -- restricting approvers, approving for the session,
  relaying a question -- works end to end where the dialect declares it.

Each adapter is built bespoke (see ``smoke/samples/approvals.py``); ``cell`` supplies
the per-adapter lane and requirements. The agent's closing reply after the last
decision is the barrier before the file check: some adapters release the room while a
decision is pending, so the trigger's ``processed`` status does not mean the command
has settled, and the platform streams no tool events to a user's socket to wait on.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/behavior/test_approvals.py -v -s --no-cov
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from band.core.simple_adapter import SimpleAdapter
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.requires import require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvalroom import ApprovalRoom
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    UNATTENDED_POLICIES,
    AgentSetup,
    Outcome,
    UnattendedPolicy,
    appending_command,
    command_request,
    commands_request,
    marker_command,
    question_request,
    repeat_request,
    written_lines,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


def readback_commands(target: Path) -> frozenset[str]:
    """The one optional read-only follow-up seen from coding agents after a write."""
    return frozenset(
        {
            f"cat {target.name}",
            f'cat "{target.name}"',
            f'Get-Content "{target.name}"',
            f'Get-Content -LiteralPath "{target}"',
        }
    )


TURN_BUDGET_S = BaselineSettings().e2e_timeout
# Sequential live barriers: the request, then the decided turn's closing reply.
BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=2)
# Two requests and the closing reply; or a request, a refusal and the closing reply.
THREE_BARRIERS = slow_turn_budget(TURN_BUDGET_S, barriers=3)
# Short enough to expire promptly, long enough that the request is captured first.
EXPIRING_WAIT_S = 10.0
# Outlasts every barrier of the longest flow (and stays under Cursor's default turn
# timeout, which it must).
PATIENT_WAIT_S = THREE_BARRIERS.deadline_s * 2

REFUSING = tuple(a for a, dialect in DIALECTS.items() if dialect.refusal)
REMEMBERING = tuple(a for a, dialect in DIALECTS.items() if dialect.session_approval)
ASKING = tuple(a for a, dialect in DIALECTS.items() if dialect.question)


@asynccontextmanager
async def approval_room(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    *,
    label: str,
    budget: SlowTurnBudget,
    wait_timeout_s: float = PATIENT_WAIT_S,
    approvers: frozenset[str] | None = None,
    build: Callable[[BaselineSettings, AgentSetup], SimpleAdapter[Any]] | None = None,
) -> AsyncIterator[tuple[ApprovalRoom, Path]]:
    """Run the cell's agent in a fresh room and workdir: in manual approval mode,
    or as ``build`` makes it."""
    dialect = DIALECTS[Adapter(cell.adapter_id)]
    for dep in dialect.extra_deps:
        require_dep(dep, cell.settings)
    with tempfile.TemporaryDirectory(
        prefix="band-e2e-approval-", dir=dialect.workdir_root(cell.settings)
    ) as workdir:
        # Resolved: a symlinked temp root (macOS /var) reads as an outside dir.
        root = Path(workdir).resolve()
        setup = AgentSetup(root, wait_timeout_s, approvers)
        adapter = (build or dialect.build)(cell.settings, setup)
        async with running_provisioned_agent(
            adapter, cell.resources, label=f"approval-{label}"
        ) as agent:
            room_id = await cell.resources.provision_room(
                title=f"e2e-approval-{cell.adapter_id}-{label}",
                participants=[agent.id],
            )
            async with reply_capture(room_id) as capture:
                yield (
                    ApprovalRoom(
                        agent,
                        Adapter(cell.adapter_id),
                        room_id,
                        capture,
                        dialect,
                        user_ops,
                        budget,
                    ),
                    root,
                )


def _wait_timeout_s(outcome: Outcome) -> float:
    """The adapter's approval wait: expiring for TIMEOUT, else outlasting both
    barriers (and still under Cursor's default turn timeout, which it must be)."""
    return EXPIRING_WAIT_S if outcome is Outcome.TIMEOUT else BUDGET.deadline_s * 2


@per_adapter(Adapter.CLAUDE_SDK, Adapter.CODEX, Adapter.CURSOR_ACP, Adapter.OPENCODE)
@pytest.mark.parametrize("outcome", list(Outcome))
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_the_room_reply_decides_whether_the_gated_command_runs(
    cell: AdapterCell,
    outcome: Outcome,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Approve runs the command, decline and an expired wait don't; a reply sent
    after the wait expired is told the ask is gone, and still runs nothing."""
    marker = unique_marker("approval")
    done = unique_marker("closed")
    async with approval_room(
        cell,
        user_ops,
        reply_capture,
        label=str(outcome),
        budget=BUDGET,
        wait_timeout_s=_wait_timeout_s(outcome),
    ) as (room, workdir):
        target = workdir / "approval.txt"
        await room.say(command_request(marker, target, done=done))
        [request] = await room.requests(1)
        after_request = room.capture.messages.snapshot()

        if outcome is not Outcome.TIMEOUT:
            await room.decide(outcome, request)
        else:
            room.expect_timeout(request)
        await room.closed(
            room.dialect.notice(outcome, request),
            since=after_request,
            closing_reply=done,
            allowed_followup_commands=(
                readback_commands(target) if outcome is Outcome.APPROVE else frozenset()
            ),
        )

        if outcome is Outcome.TIMEOUT:
            late = await room.say(room.dialect.reply(Outcome.APPROVE, request))
            await room.shown(room.dialect.late_notice(request).text, since=late)
            approved = room.dialect.notice(Outcome.APPROVE, request).text
            assert not any(approved in said for said in room.said_since(late))

        if outcome is Outcome.APPROVE:
            assert written_lines(target) == [marker]
        else:
            assert not target.exists(), f"{outcome} still ran the gated command"


@per_adapter(Adapter.CLAUDE_SDK, Adapter.CODEX, Adapter.CURSOR_ACP, Adapter.OPENCODE)
@pytest.mark.timeout(extra=THREE_BARRIERS.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_each_of_two_gated_commands_is_decided_on_its_own(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two commands in one turn ask separately: approving one and declining the
    other runs exactly one."""
    async with approval_room(
        cell, user_ops, reply_capture, label="two", budget=THREE_BARRIERS
    ) as (room, workdir):
        markers = {workdir / f"{name}.txt": unique_marker(name) for name in "ab"}
        commands = [
            marker_command(marker, target) for target, marker in markers.items()
        ]
        done = unique_marker("closed")
        start = await room.say(commands_request(*commands, done=done))
        [approved] = await room.requests(1, since=start)
        await room.decide(Outcome.APPROVE, approved)
        declined = (await room.requests(2, since=start))[1]
        await room.decide(Outcome.DECLINE, declined)
        await room.closed(
            room.dialect.notice(Outcome.APPROVE, approved),
            room.dialect.notice(Outcome.DECLINE, declined),
            since=start,
            closing_reply=done,
        )
        # Inside the block: leaving it deletes the workdir.
        landed = {t: m for t, m in markers.items() if t.exists()}
        assert len(landed) == 1, f"expected exactly one command to run, got {landed}"
        [(target, marker)] = landed.items()
        assert written_lines(target) == [marker]


@per_adapter(*REFUSING)
@pytest.mark.timeout(extra=THREE_BARRIERS.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_only_an_authorized_member_decides_an_ask(
    cell: AdapterCell,
    user_ops: UserOps,
    second_user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A room member outside the approver list is refused and the command stays
    parked; the approver's reply then runs it."""
    marker = unique_marker("approver")
    done = unique_marker("closed")
    owner_id = await user_ops.whoami()
    async with (
        approval_room(
            cell,
            user_ops,
            reply_capture,
            label="approver",
            budget=THREE_BARRIERS,
            approvers=frozenset({owner_id}),
        ) as (room, workdir),
        user_ops.contact_with(second_user_ops) as second_user_id,
    ):
        await user_ops.add_participant(room.room_id, second_user_id)
        assert second_user_id in await user_ops.list_participant_ids(room.room_id)
        target = workdir / "approval.txt"
        await room.say(command_request(marker, target, done=done))
        [request] = await room.requests(1)
        approve = room.dialect.reply(Outcome.APPROVE, request)
        resolved = room.dialect.notice(Outcome.APPROVE, request)
        assert room.dialect.refusal is not None  # REFUSING selects on it

        refused = await room.say(approve, sender=second_user_ops)
        await room.shown(room.dialect.refusal.text, since=refused)
        assert not any(resolved.text in said for said in room.said_since(refused))
        assert not target.exists(), "a refused approver's reply ran the command"

        approved = await room.decide(Outcome.APPROVE, request)
        await room.closed(
            resolved,
            since=approved,
            closing_reply=done,
            allowed_followup_commands=readback_commands(target),
        )
        assert written_lines(target) == [marker]


@per_adapter(*REMEMBERING)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_a_session_approval_covers_a_repeat_of_the_same_command(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One session approval runs a command and its exact repeat: the room is asked
    once, and both runs land."""
    marker, done = unique_marker("session"), unique_marker("done")
    async with approval_room(
        cell, user_ops, reply_capture, label="session", budget=BUDGET
    ) as (room, workdir):
        target = workdir / "approval.txt"
        session = room.dialect.session_approval
        assert session is not None  # REMEMBERING selects on it

        start = await room.say(repeat_request(appending_command(marker, target), done))
        [request] = await room.requests(1, since=start)
        await room.say(session.reply(request))
        await room.shown(done, since=start)
        await session.notice(request).assert_shown(room.capture, room.agent.id)

        asked = room.dialect.find_requests(room.capture.messages.since(start))
        assert [match["token"] for match in asked] == [request["token"]]
        assert written_lines(target) == [marker, marker]


@per_adapter(*ASKING)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_a_question_is_answered_in_free_text_from_the_room(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The agent's question reaches the room, a plain-text answer goes back to it,
    and the agent acts on exactly that answer."""
    codeword = unique_marker("codeword")
    async with approval_room(
        cell, user_ops, reply_capture, label="question", budget=BUDGET
    ) as (room, _workdir):
        relay = room.dialect.question
        assert relay is not None  # ASKING selects on it

        await room.say(question_request())
        asked = await room.capture.wait_until(
            lambda msgs: relay.find(msgs) is not None, deadline_s=BUDGET.deadline_s
        )
        question = relay.find(asked)
        assert question is not None  # the predicate guarantees one
        answered = await room.say(codeword)
        notice = relay.answered(question)
        await room.capture.wait_until(
            lambda _msgs: any(
                codeword in said and notice.text not in said
                for said in room.said_since(answered)
            ),
            deadline_s=BUDGET.deadline_s,
        )
        await notice.assert_shown(room.capture, room.agent.id)


@per_adapter(Adapter.CLAUDE_SDK)
@pytest.mark.parametrize("policy", UNATTENDED_POLICIES, ids=lambda p: p.name)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_a_host_config_policy_settles_tool_use_with_nobody_asked(
    cell: AdapterCell,
    policy: UnattendedPolicy,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A host's plain-data config decides alone: auto_accept runs the write and
    auto_decline refuses it, each announcing its decision; dontAsk refuses a
    write the default mode would allow, announcing nothing. No one is asked."""
    marker, done = unique_marker("policy"), unique_marker("closed")
    async with approval_room(
        cell,
        user_ops,
        reply_capture,
        label=policy.name,
        budget=BUDGET,
        build=policy.build,
    ) as (room, workdir):
        target = workdir / "policy.txt"
        start, message_id = await room.post(policy.request(marker, target, done=done))
        await room.capture.wait_for_processed(
            message_id, room.agent.id, deadline_s=BUDGET.deadline_s
        )
        await room.capture.wait_until(
            lambda _msgs: policy.settled(room.said_since(start), done),
            deadline_s=BUDGET.deadline_s,
        )

        # The tool was really attempted, so a missing file is the policy's doing.
        tool_calls = await room.capture.tool_calls(sender_id=room.agent.id)
        tool_calls.assert_fired(policy.tool)
        said = room.said_since(start)
        assert room.dialect.find_requests(room.capture.messages.since(start)) == []
        assert policy.decisions(said) == policy.announced
        if policy.runs:
            assert target.read_text().strip() == marker
        else:
            assert not target.exists(), f"{policy.name} still wrote the file"
