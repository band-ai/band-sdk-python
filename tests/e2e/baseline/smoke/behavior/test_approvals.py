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

import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    ApprovalDialect,
    Notice,
    Outcome,
    appending_command,
    command_request,
    commands_request,
    marker_command,
    question_request,
    repeat_request,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

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


@dataclass
class ApprovalRoom:
    """One manual-approval agent in its own room, driven by the room's humans."""

    agent: ProvisionedAgent
    room_id: str
    capture: ReplyCapture
    dialect: ApprovalDialect
    user_ops: UserOps
    budget: SlowTurnBudget

    async def say(self, text: str, *, sender: UserOps | None = None) -> int:
        """Post ``text`` to the agent; return a cursor at what came before it."""
        cursor = self.capture.messages.snapshot()
        await (sender or self.user_ops).send_message(
            self.room_id, text, mention_id=self.agent.id, mention_name=self.agent.name
        )
        return cursor

    async def requests(self, count: int, *, since: int = 0) -> list[re.Match[str]]:
        """The first ``count`` approval requests posted after ``since``."""
        asked = await self.capture.wait_until(
            lambda msgs: len(self.dialect.find_requests(msgs[since:])) >= count,
            deadline_s=self.budget.deadline_s,
        )
        return self.dialect.find_requests(asked[since:])[:count]

    async def shown(self, text: str, *, since: int) -> None:
        """Wait until an agent message after ``since`` shows ``text``."""
        await self.capture.wait_until(
            lambda msgs: any(text in (m.content or "") for m in msgs[since:]),
            deadline_s=self.budget.deadline_s,
        )

    async def closed(self, *notices: Notice, since: int) -> None:
        """Wait until every notice is shown and the agent has closed the turn."""
        await self.capture.wait_until(
            lambda _msgs: self.dialect.settled(
                self.capture.messages.since(since), *notices
            ),
            deadline_s=self.budget.deadline_s,
        )
        for notice in notices:
            await notice.assert_shown(self.capture, self.agent.id)

    def said_since(self, since: int) -> list[str]:
        return [m.content or "" for m in self.capture.messages.since(since)]


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
) -> AsyncIterator[tuple[ApprovalRoom, Path]]:
    """Run the cell's agent in manual approval mode in a fresh room and workdir."""
    dialect = DIALECTS[Adapter(cell.adapter_id)]
    for dep in dialect.extra_deps:
        require_dep(dep, cell.settings)
    with tempfile.TemporaryDirectory(
        prefix="band-e2e-approval-", dir=dialect.workdir_root(cell.settings)
    ) as workdir:
        # Resolved: a symlinked temp root (macOS /var) reads as an outside dir.
        root = Path(workdir).resolve()
        setup = AgentSetup(root, wait_timeout_s, approvers)
        adapter = dialect.build(cell.settings, setup)
        async with running_provisioned_agent(
            adapter, cell.resources, label=f"approval-{label}"
        ) as agent:
            room_id = await cell.resources.provision_room(
                title=f"e2e-approval-{cell.adapter_id}-{label}",
                participants=[agent.id],
            )
            async with reply_capture(room_id) as capture:
                yield (
                    ApprovalRoom(agent, room_id, capture, dialect, user_ops, budget),
                    root,
                )


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
    """Approve runs the command, decline and an expired wait don't; a reply sent
    after the wait expired is told the ask is gone, and still runs nothing."""
    marker = unique_marker("approval")
    async with approval_room(
        cell,
        user_ops,
        reply_capture,
        label=str(outcome),
        budget=BUDGET,
        wait_timeout_s=_wait_timeout_s(outcome),
    ) as (room, workdir):
        target = workdir / "approval.txt"
        await room.say(command_request(marker, target))
        [request] = await room.requests(1)
        after_request = room.capture.messages.snapshot()

        if outcome is not Outcome.TIMEOUT:
            await room.say(room.dialect.reply(outcome, request))
        await room.closed(room.dialect.notice(outcome, request), since=after_request)

        if outcome is Outcome.TIMEOUT:
            late = await room.say(room.dialect.reply(Outcome.APPROVE, request))
            await room.shown(room.dialect.late_notice(request).text, since=late)
            approved = room.dialect.notice(Outcome.APPROVE, request).text
            assert not any(approved in said for said in room.said_since(late))

        if outcome is Outcome.APPROVE:
            assert target.read_text().strip() == marker
        else:
            assert not target.exists(), f"{outcome} still ran the gated command"


@per_adapter(Adapter.CLAUDE_SDK, Adapter.CODEX, Adapter.CURSOR_ACP, Adapter.OPENCODE)
@flaky_infra("a live coding-agent turn that must reach a shell tool use can time out")
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
        start = await room.say(commands_request(*commands))
        [approved] = await room.requests(1, since=start)
        await room.say(room.dialect.reply(Outcome.APPROVE, approved))
        declined = (await room.requests(2, since=start))[1]
        await room.say(room.dialect.reply(Outcome.DECLINE, declined))
        await room.closed(
            room.dialect.notice(Outcome.APPROVE, approved),
            room.dialect.notice(Outcome.DECLINE, declined),
            since=start,
        )
        # Inside the block: leaving it deletes the workdir.
        landed = {t: m for t, m in markers.items() if t.exists()}
        assert len(landed) == 1, f"expected exactly one command to run, got {landed}"
        [(target, marker)] = landed.items()
        assert target.read_text().strip() == marker


@per_adapter(*REFUSING)
@flaky_infra("a live coding-agent turn that must reach a shell tool use can time out")
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
    owner_id = await user_ops.whoami()
    async with approval_room(
        cell,
        user_ops,
        reply_capture,
        label="approver",
        budget=THREE_BARRIERS,
        approvers=frozenset({owner_id}),
    ) as (room, workdir):
        await user_ops.add_participant(room.room_id, await second_user_ops.whoami())
        target = workdir / "approval.txt"
        await room.say(command_request(marker, target))
        [request] = await room.requests(1)
        approve = room.dialect.reply(Outcome.APPROVE, request)
        resolved = room.dialect.notice(Outcome.APPROVE, request)
        assert room.dialect.refusal is not None  # REFUSING selects on it

        refused = await room.say(approve, sender=second_user_ops)
        await room.shown(room.dialect.refusal.text, since=refused)
        assert not any(resolved.text in said for said in room.said_since(refused))
        assert not target.exists(), "a refused approver's reply ran the command"

        approved = await room.say(approve)
        await room.closed(resolved, since=approved)
        assert target.read_text().strip() == marker


@per_adapter(*REMEMBERING)
@flaky_infra("a live coding-agent turn that must reach a shell tool use can time out")
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
        assert target.read_text().strip() == marker * 2


@per_adapter(*ASKING)
@flaky_infra("a live coding-agent turn that must reach its question tool can time out")
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
