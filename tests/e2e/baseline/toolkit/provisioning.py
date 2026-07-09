"""Dynamic provisioning (provision/reap) for live E2E tests.

Provisions fresh platform resources per run so tests never depend on a static,
pre-configured agent: register an agent (getting its own credentials), create
rooms, and force-delete everything on teardown. A prefix-guarded orphan sweep
reaps leftovers from crashed prior runs.

Provisioned agents are named ``e2e-band-{run_id}-{label}`` so the sweep can
recognise its own resources by prefix and never touch a non-test agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from band_rest import (
    AgentRegisterRequest,
    AsyncRestClient,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
)

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    # Annotation-only (PEP 563): kept out of runtime imports so this module never
    # pulls the framework/registry graph and can't form an import cycle.
    from band.core.types import AdapterFeatures
    from tests.e2e.baseline.toolkit.tools import ToolSpec

logger = logging.getLogger(__name__)

# All provisioned agent names start with this; the orphan sweep matches on it.
NAME_PREFIX = "e2e-band-"


def new_run_id() -> str:
    """Short token identifying a single test session's provisioned resources.

    Kept to 5 hex chars so a provisioned agent name — ``{NAME_PREFIX}{run_id}-{label}``
    = ``e2e-band-{5}-{label}`` — stays within the platform's 24-char mention-handle
    cap for the asserted labels (``member``/``invitable``/``nonmember``, ≤9 chars):
    9 + 5 + 1 + 9 = 24. Beyond that the handle truncates (``…-invitable`` -> ``…-invita``),
    so a peer referenced by @mention would never surface its full name and the
    self-sourced roster assertions in test_identity_and_roster would fail. Five hex
    (~1M) is ample entropy given the run-id + age guards in ``sweep_orphans``.
    """
    return uuid.uuid4().hex[:5]


@dataclass(frozen=True)
class ProvisionedAgent:
    """A freshly registered agent and its own credentials.

    ``adapter_id`` records which registered adapter this identity was built from
    when it comes from the matrix (``@per_adapter``) or a ``@with_adapters`` slot,
    so a test reads ``agent.adapter_id`` instead of threading a separate fixture.
    ``None`` for identities provisioned directly (e.g. a bystander), which have no
    adapter behind them.
    """

    id: str
    api_key: str
    name: str
    adapter_id: str | None = None


def agent_rest_client(
    agent: ProvisionedAgent, settings: BaselineSettings
) -> AsyncRestClient:
    """An agent-authenticated REST client for reads scoped to the agent itself.

    Memories are agent-scoped, not room-scoped, so reading them back uses the
    agent's *own* key (not the user/observer client the rest of the toolkit
    uses). Built like ``conftest.baseline_user_client``: the Fern client wraps an
    httpx pool with no public close hook, so (like the session user client) it is
    left to be reclaimed at event-loop teardown rather than closed explicitly.
    ``ReplyCapture`` reuses one client per agent to bound how many are opened.
    """
    return AsyncRestClient(api_key=agent.api_key, base_url=settings.endpoints.rest_url)


class PeerActor:
    """Drive a provisioned peer agent — the agent-side twin of ``UserOps``.

    ``UserOps`` acts as the test *user* (Human API); ``PeerActor`` acts as a peer
    *agent* (Agent API), so a scenario can have a second participant say something
    deterministically without running a full framework adapter. The canonical use
    is the L0/L4 ``Echo`` peer: provision an identity, invite it, then post one
    ``ECHO: {body}`` bounce. Built from the peer's own key via ``agent_rest_client``.

    The peer must already be a participant of the room (an agent can only post to a
    room it is in); membership stays with ``ResourceManager``/``UserOps`` or the
    agent under test, not here.
    """

    def __init__(self, peer: ProvisionedAgent, settings: BaselineSettings) -> None:
        self._peer = peer
        self._client = agent_rest_client(peer, settings)

    async def send_message(
        self, room_id: str, content: str, *, mention_id: str, mention_name: str
    ) -> str:
        """Post one message as this peer; return the message id.

        Mirrors ``UserOps.send_message`` (mention required, returns the id) so a
        test can barrier on the peer's message with ``wait_for_processed``.
        """
        response = await self._client.agent_api_messages.create_agent_chat_message(
            room_id,
            message=ChatMessageRequest(
                content=content,
                mentions=[
                    ChatMessageRequestMentionsItem(id=mention_id, name=mention_name)
                ],
            ),
        )
        return response.data.id


class ResourceManager:
    """Provisions and reaps platform resources for one test run.

    Tracks everything it creates so ``reap_all`` can force-delete on teardown.
    Room operations are delegated to ``UserOps`` so the direct-REST delete path
    lives in exactly one place.
    """

    def __init__(
        self,
        *,
        user_client: AsyncRestClient,
        settings: BaselineSettings,
        run_id: str,
    ) -> None:
        self._client = user_client
        self._settings = settings
        self._run_id = run_id
        self._user_ops = UserOps(user_client)
        self._provisioned_agent_ids: list[str] = []
        self._provisioned_room_ids: list[str] = []
        self._running_agent_ids: set[str] = set()
        # One PeerActor (and its REST client) per agent id, reused across calls so
        # repeated peer() calls don't open a fresh httpx pool each time (mirrors
        # ReplyCapture's per-agent client reuse).
        self._peer_actors: dict[str, PeerActor] = {}

    @contextmanager
    def track_running(self, agent_id: str) -> Iterator[None]:
        """Mark ``agent_id`` running for the block; raise if it already is.

        Guards the reboot/rejoin footgun: running one identity twice concurrently
        (overlapping/nested runs) instead of sequentially. Releases in ``finally``,
        so a run that fails *during startup* never wedges the id and blocks a retry.
        """
        if agent_id in self._running_agent_ids:
            raise RuntimeError(
                f"agent {agent_id} is already running — overlapping runs of one "
                "identity are unsupported; run reboot/rejoin sequences sequentially"
            )
        self._running_agent_ids.add(agent_id)
        try:
            yield
        finally:
            self._running_agent_ids.discard(agent_id)

    @property
    def settings(self) -> BaselineSettings:
        return self._settings

    @property
    def client(self) -> AsyncRestClient:
        """The user-authenticated REST client, for platform state reads."""
        return self._client

    @property
    def user_ops(self) -> UserOps:
        return self._user_ops

    def _agent_name(self, label: str) -> str:
        return f"{NAME_PREFIX}{self._run_id}-{label}"

    async def provision_agent(self, label: str) -> ProvisionedAgent:
        """Register a fresh agent and return its id + own API key."""
        name = self._agent_name(label)
        response = await self._client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(
                name=name,
                description=f"E2E baseline test agent ({label})",
            )
        )
        agent = response.data.agent
        credentials = response.data.credentials
        assert agent is not None and agent.id, "register_my_agent returned no agent id"
        assert credentials is not None and credentials.api_key, (
            "register_my_agent returned no credentials"
        )
        self._provisioned_agent_ids.append(agent.id)
        logger.info("Provisioned agent %s (%s)", agent.id, name)
        return ProvisionedAgent(id=agent.id, api_key=credentials.api_key, name=name)

    def peer(self, agent: ProvisionedAgent) -> PeerActor:
        """A ``PeerActor`` to drive ``agent`` as a peer (e.g. the ``Echo`` bounce).

        The manager already holds the settings and provisioned the identity, so a
        test needs neither a separate fixture nor to thread ``settings``. Cached
        per agent id so repeated calls reuse one REST client.
        """
        actor = self._peer_actors.get(agent.id)
        if actor is None:
            actor = PeerActor(agent, self._settings)
            self._peer_actors[agent.id] = actor
        return actor

    async def provision_room(
        self, *, title: str | None = None, participants: list[str] | None = None
    ) -> str:
        """Create a room as the user; optionally add participants. Returns id."""
        room_id = await self._user_ops.create_room(title=title)
        self._provisioned_room_ids.append(room_id)
        # Independent REST adds to the same room — run concurrently so setup
        # latency doesn't scale linearly with participant count.
        await asyncio.gather(
            *(
                self._user_ops.add_participant(room_id, pid)
                for pid in participants or []
            )
        )
        logger.info("Provisioned room %s", room_id)
        return room_id

    def adopt_room(self, room_id: str) -> None:
        """Track a room this run did not create via :meth:`provision_room` — e.g. one an
        agent created itself through ``band_create_chatroom`` — so it is reaped on teardown
        like any provisioned room. Idempotent. Reaping goes through the same
        ``user_ops.delete_room`` path, which the platform authorizes because the test user
        owns the agent that owns the room (agent-owner delete authz).
        """
        if room_id not in self._provisioned_room_ids:
            self._provisioned_room_ids.append(room_id)

    async def agent_room_ids(self, agent: ProvisionedAgent) -> set[str]:
        """The ids of the chat rooms ``agent`` participates in, read with the agent's own key.

        A before/after snapshot around a turn surfaces a room the agent created *itself*
        (via ``band_create_chatroom``) — the only handle to it, since that tool takes no
        title and adds no human participant, so the user/observer client never sees it.
        Pair the new id with :meth:`adopt_room` so it is reaped on teardown like any
        provisioned room. Uses the agent's Agent-API client (like the memory reads).
        """
        client = agent_rest_client(agent, self._settings)
        response = await client.agent_api_chats.list_agent_chats()
        return {room.id for room in (response.data or [])}

    async def reap_agent(self, agent_id: str) -> None:
        """Force-delete an agent."""
        await self._client.human_api_agents.delete_my_agent(agent_id, force=True)
        if agent_id in self._provisioned_agent_ids:
            self._provisioned_agent_ids.remove(agent_id)

    async def reap_room(self, room_id: str) -> None:
        await self._user_ops.delete_room(room_id)
        if room_id in self._provisioned_room_ids:
            self._provisioned_room_ids.remove(room_id)

    async def reap_all(self) -> None:
        """Best-effort teardown of everything provisioned this run.

        Logs ids before deleting so they stay recoverable from logs, and keeps
        going past individual failures (rooms first, then agents).
        """
        for room_id in list(self._provisioned_room_ids):
            logger.info("Reaping room %s", room_id)
            try:
                await self.reap_room(room_id)
            except Exception:
                logger.warning("Failed to reap room %s", room_id, exc_info=True)
        for agent_id in list(self._provisioned_agent_ids):
            logger.info("Reaping agent %s", agent_id)
            try:
                await self.reap_agent(agent_id)
            except Exception:
                logger.warning("Failed to reap agent %s", agent_id, exc_info=True)

    async def sweep_orphans(self) -> int:
        """Force-delete stale test agents left by crashed prior runs.

        Only touches agents whose name carries ``NAME_PREFIX``, belongs to a
        *different* run, and is older than ``orphan_max_age_minutes`` — so a
        concurrent run on the same shared platform is never deleted mid-flight.
        Returns the number of agents reaped.
        """
        max_age = timedelta(minutes=self._settings.run.orphan_max_age_minutes)
        cutoff = datetime.now(timezone.utc) - max_age

        # Collect candidates across all pages FIRST, then delete — deleting while
        # paginating would shrink the list and skip agents past a page boundary.
        # Cursor pagination (the SDK's preferred path; offset `page`/`page_size`
        # is deprecated): advance via metadata.next_cursor until has_more is
        # false. The iteration cap bounds a best-effort sweep.
        orphans: list[str] = []
        cursor: str | None = None
        for _ in range(20):
            response = await self._client.human_api_agents.list_my_agents(
                name=NAME_PREFIX, limit=100, cursor=cursor
            )
            for agent in response.data or []:
                if not agent.name.startswith(NAME_PREFIX):
                    continue  # name filter is a contains-match; re-check the prefix
                if f"-{self._run_id}-" in agent.name:
                    continue  # never reap our own run
                # inserted_at may be tz-naive depending on serialization; treat
                # naive as UTC so the comparison never raises (see the codebase's
                # _coerce_inserted_at). A naive>aware compare would TypeError and
                # abort the autouse session fixture.
                inserted = agent.inserted_at
                if inserted.tzinfo is None:
                    inserted = inserted.replace(tzinfo=timezone.utc)
                if inserted > cutoff:
                    continue  # too fresh — could be a concurrent run
                orphans.append(agent.id)
            cursor = response.metadata.next_cursor
            if not response.metadata.has_more or not cursor:
                break

        reaped = 0
        for agent_id in orphans:
            logger.info("Sweeping orphan agent %s", agent_id)
            try:
                await self._client.human_api_agents.delete_my_agent(
                    agent_id, force=True
                )
                reaped += 1
            except Exception:
                logger.warning(
                    "Failed to sweep orphan agent %s", agent_id, exc_info=True
                )
        if reaped:
            logger.info("Orphan sweep reaped %d agent(s)", reaped)
        return reaped


@asynccontextmanager
async def running_agent(
    provisioned: ProvisionedAgent,
    adapter: SimpleAdapter[Any],
    settings: BaselineSettings,
) -> AsyncGenerator[ProvisionedAgent, None]:
    """Run ``adapter`` as an *already-provisioned* identity for the block.

    The run half of ``running_provisioned_agent`` (which adds provisioning): this
    owns only the run lifecycle, leaving provision + reap to the resource manager.
    Yields the same ``provisioned`` back for symmetry with its sibling.

    Enter it twice against one ``provisioned`` identity — each time with a *fresh*
    adapter — to exercise a stop→rejoin: the second run starts with no in-memory
    adapter state, so anything the agent then recalls must have come from the
    platform rehydrating the room's history on bootstrap (``/context``), which is
    exactly what a rejoin scenario asserts.
    """
    endpoints = settings.endpoints
    agent = Agent.create(
        adapter=adapter,
        agent_id=provisioned.id,
        api_key=provisioned.api_key,
        ws_url=endpoints.ws_url,
        rest_url=endpoints.rest_url,
    )
    async with agent:
        yield provisioned


@asynccontextmanager
async def running_provisioned_agent(
    adapter: SimpleAdapter[Any],
    resources: ResourceManager,
    *,
    label: str = "aut",
) -> AsyncGenerator[ProvisionedAgent, None]:
    """Provision an agent and run ``adapter`` as it for the duration of the block.

    Yields the ``ProvisionedAgent`` record (id, name, api_key) — the only thing
    callers need to mention/observe the agent. The running ``Agent`` object itself
    is managed internally (kept alive for the block, via ``running_agent``) and is
    not exposed, since no caller uses it. Reaping is owned by the resource manager's
    teardown (the agent is tracked at provision time), so this only manages the run
    lifecycle. (Matrix / group agents come from ``AdapterCell``, which stamps
    ``adapter_id`` itself; this bespoke primitive leaves it unset.)
    """
    provisioned = await resources.provision_agent(label)
    async with running_agent(provisioned, adapter, resources.settings) as running:
        yield running


@asynccontextmanager
async def running_members(
    members: Sequence[AbstractAsyncContextManager[ProvisionedAgent]],
) -> AsyncGenerator[list[ProvisionedAgent], None]:
    """Enter several per-member run contexts **concurrently**; yield the running identities.

    The shared co-residency machinery behind both the ``@with_adapters`` group fixture
    and ``AdapterCell.run_many``: each runs several agents in one room, and both must
    start them concurrently — a serial start would mask the port / lock-file collisions
    a co-residency test exists to catch. Keeping it here means neither caller re-rolls it.

    Each member gets its own ``AsyncExitStack`` (that type isn't concurrency-safe),
    registered on the outer stack up front so teardown unwinds every member that entered
    even if another fails to start. A ``TaskGroup`` enters them concurrently and cancels +
    awaits the siblings if any member raises. Results come back in member order.
    """
    async with AsyncExitStack() as stack:
        member_stacks = [AsyncExitStack() for _ in members]
        for member_stack in member_stacks:
            await stack.enter_async_context(member_stack)
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(member_stacks[index].enter_async_context(member))
                for index, member in enumerate(members)
            ]
        yield [task.result() for task in tasks]


@dataclass(frozen=True)
class AdapterCell:
    """The adapter under test for one matrix cell — build / provision / run it yourself.

    The ``@per_adapter`` counterpart to the managed ``agent`` fixture: request ``cell``
    when a test owns the agent's lifecycle (construction checks, and reboot / restart /
    rehydration scenarios that stop and re-run under one identity). ``agent`` is just
    sugar over :meth:`running`.

    Steering placed on the decorator (``@per_adapter(prompt=…, features=…, tools=…)``)
    is carried here as the cell's defaults, so a test sets it once on the decorator; a
    method argument overrides the default when given (``None`` means "use the default").
    """

    adapter_id: str
    settings: BaselineSettings
    resources: ResourceManager
    prompt: str | None = None
    features: AdapterFeatures | None = None
    tools: list[ToolSpec] | None = None

    def build(
        self,
        *,
        prompt: str | None = None,
        features: AdapterFeatures | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> SimpleAdapter[Any]:
        """Construct (do not run) this cell's adapter; arguments override cell defaults.

        ``build_adapter`` is imported lazily so this module never pulls the adapter
        registry (and its optional framework deps) at import time.
        """
        # Overrides use None-means-"cell default" (not a sentinel): no test needs to
        # clear a default back to "no prompt", so the sentinel would be dead machinery.
        from tests.e2e.baseline.toolkit.adapters import build_adapter

        return build_adapter(
            self.adapter_id,
            self.settings,
            prompt=self.prompt if prompt is None else prompt,
            features=self.features if features is None else features,
            tools=self.tools if tools is None else tools,
        )

    async def provision(self, *, label: str | None = None) -> ProvisionedAgent:
        """Register an identity for this cell (tracked + reaped by the manager); no run.

        ``label`` defaults to the adapter id (a readable provisioned name). Pass a
        distinct label to register more than one identity of the same cell in a single
        test, else the generated names collide.
        """
        provisioned = await self.resources.provision_agent(label or self.adapter_id)
        return replace(provisioned, adapter_id=self.adapter_id)

    @asynccontextmanager
    async def run_as(
        self,
        identity: ProvisionedAgent,
        *,
        prompt: str | None = None,
        features: AdapterFeatures | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncGenerator[ProvisionedAgent, None]:
        """Run a *fresh* adapter under an existing ``identity`` for the block.

        Enter twice against one identity to exercise a stop→reboot: the second run
        starts with no in-memory state, so a correct recall proves platform
        rehydration. Guarded (via ``track_running``) against overlapping runs of the
        same identity.
        """
        adapter = self.build(prompt=prompt, features=features, tools=tools)
        with self.resources.track_running(identity.id):
            async with running_agent(identity, adapter, self.settings):
                yield identity

    @asynccontextmanager
    async def running(
        self,
        *,
        label: str | None = None,
        prompt: str | None = None,
        features: AdapterFeatures | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncGenerator[ProvisionedAgent, None]:
        """Provision an identity and run this cell's adapter as it for the block.

        Provision + :meth:`run_as` in one step — what the ``agent`` fixture uses.
        """
        identity = await self.provision(label=label)
        async with self.run_as(
            identity, prompt=prompt, features=features, tools=tools
        ) as running:
            yield running

    @asynccontextmanager
    async def run_many(
        self,
        count: int,
        *,
        labels: list[str] | None = None,
        prompt: str | None = None,
        features: AdapterFeatures | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncGenerator[list[ProvisionedAgent], None]:
        """Provision ``count`` distinct identities of this cell and run one fresh adapter
        each, **concurrently**, for the block — the co-residency counterpart to :meth:`running`.

        Where ``running`` starts a single agent, this stands up ``count`` instances of the
        *same* adapter under distinct identities (so ``track_running`` never conflicts) and
        yields the running list. It starts them concurrently — via ``running_members``, the
        same helper the ``@with_adapters`` group uses — so a real port / lock-file collision
        between instances races rather than being masked by a serial start, which is exactly
        what a same-adapter co-residency gate must probe.

        ``labels`` default to ``{adapter_id}-{index}``; an explicit list must be length
        ``count`` so the provisioned names don't collide. ``prompt`` / ``features`` /
        ``tools`` pass through to each :meth:`run_as`, preserving the cell defaults when
        omitted.
        """
        if count <= 0:
            raise ValueError(f"run_many count must be positive, got {count}")
        if labels is not None and len(labels) != count:
            raise ValueError(
                f"run_many labels length ({len(labels)}) must match count ({count})"
            )
        # Each member provisions *and* runs (``running``), entered concurrently by
        # ``running_members`` — so provisioning is concurrent too, exactly like the
        # ``@with_adapters`` group path (``_running_group_member`` → ``cell.running``),
        # not a serial provision loop before the concurrent run.
        members = [
            self.running(
                label=labels[index] if labels else f"{self.adapter_id}-{index}",
                prompt=prompt,
                features=features,
                tools=tools,
            )
            for index in range(count)
        ]
        async with running_members(members) as running:
            yield running
