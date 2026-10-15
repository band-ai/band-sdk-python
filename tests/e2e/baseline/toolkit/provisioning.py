"""Dynamic provisioning (provision/reap) for live E2E tests.

Provisions fresh platform resources per run so tests never depend on a static,
pre-configured agent: register an agent (getting its own credentials), create
rooms, and force-delete everything on teardown. A prefix-guarded orphan sweep
reaps leftovers from crashed prior runs.

Provisioned agents are named by :func:`agent_name` under ``NAME_PREFIX`` so the
sweep can recognise its own resources by prefix and never touch a non-test agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from band_rest import (
    AgentRegisterRequest,
    AsyncRestClient,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    NotFoundError,
)

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter
from band.runtime.agentconfig import AgentConfig
from band.workspaces import WorkspaceResolver
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import build_adapter
from tests.e2e.baseline.toolkit.restclient import baseline_rest_client
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    # Annotation-only (PEP 563): kept out of runtime imports so this module never
    # pulls the framework/registry graph and can't form an import cycle.
    from band.core.types import AdapterFeatures
    from tests.e2e.baseline.toolkit.tools import ToolSpec

logger = logging.getLogger(__name__)

# All provisioned agent names start with this; the orphan sweep matches on it.
NAME_PREFIX = "e2e-band-"

# Between the token and the label. Both name budgets subtract this same separator.
TOKEN_LABEL_SEPARATOR = "-"

# The platform truncates @mention handles beyond this many characters. A peer that a
# scenario provisions and later asserts by @mention (a roster reply names it as
# ``@handle``) only surfaces its full name if that name fits the cap; beyond it the
# handle truncates (``…-invitable`` -> ``…-invita``) and the self-sourced roster
# assertions in test_identity_and_roster would fail. The agent-under-test is exempt:
# it reports its own name as plain text (not a handle), so its adapter-id label
# (e.g. "pydantic_ai") may push its name past the cap harmlessly.
MENTION_HANDLE_CAP = 24

# Longest label a scenario both provisions and asserts by @mention
# ("invitable"/"nonmember"). The run-id length is derived from it rather than
# hand-picked; the guard test_mentioned_peer_names_fit_handle_cap enforces the
# budget so a longer peer label (or a wider prefix) fails loudly instead of
# silently truncating a handle.
MAX_MENTIONED_LABEL_LEN = len("invitable")  # 9

# Platform ``validate_length(:name, max: 100)`` on the agent row. A longer
# register is rejected for length, which is a different 422 from name-taken.
AGENT_NAME_MAX = 100

# Same cap on the API key created in that register. The key name is longer
# than the agent name; see :func:`credential_name`.
API_KEY_NAME_MAX = 100

# Leading characters of the agent id appended to that key's name.
CREDENTIAL_ID_SLICE = 8

# ``uuid4().hex`` width. Labels that may exceed the mention cap take the whole
# uuid so a leftover row cannot collide with the next provision of that label.
NAME_TOKEN_HEX = 32


def name_budget(limit: int, label_len: int) -> int:
    """Characters left for the token once the prefix, separator, and label are counted."""
    return limit - len(NAME_PREFIX) - len(TOKEN_LABEL_SEPARATOR) - label_len


def run_id_len() -> int:
    """Hex length of a mentionable provision token.

    The longest label a scenario can @mention stays inside the handle cap at
    this width. :func:`name_token` uses it for those labels.
    """
    return name_budget(MENTION_HANDLE_CAP, MAX_MENTIONED_LABEL_LEN)


def normalized_label(label: str) -> str:
    return label.replace("_", "-")


def agent_name(*, token: str, label: str) -> str:
    """The provisioned name for ``label`` and ``token``.

    Underscored adapter ids become the hyphenated names models echo.
    """
    return f"{NAME_PREFIX}{token}{TOKEN_LABEL_SEPARATOR}{normalized_label(label)}"


def credential_name(name: str, agent_id: str) -> str:
    """Name the platform stores on the API key created with the agent.

    The agent row and the key are one transaction. The key name has its own
    ``API_KEY_NAME_MAX`` cap, so an agent name inside ``AGENT_NAME_MAX`` still
    rolls the registration back when this string does not fit.
    """
    return f"{name} ({agent_id[:CREDENTIAL_ID_SLICE]})"


# Longest agent name whose key name still fits. Measured with an id of
# ``CREDENTIAL_ID_SLICE`` characters; every registered id is at least that long.
REGISTRATION_NAME_MAX = min(
    AGENT_NAME_MAX,
    API_KEY_NAME_MAX - len(credential_name("", "0" * CREDENTIAL_ID_SLICE)),
)


def name_token(label: str) -> str:
    """A fresh hex token for one provision of ``label``.

    A label a scenario can @mention gets :func:`run_id_len` characters, so the
    longest such name stays inside ``MENTION_HANDLE_CAP``. A longer label is not
    a mention handle and gets a full uuid slice, clamped so the name stays
    inside ``REGISTRATION_NAME_MAX``. A fresh token per provision is what keeps a
    leftover row from colliding with the next register of the same label.
    """
    normalized = normalized_label(label)
    if len(normalized) <= MAX_MENTIONED_LABEL_LEN:
        return uuid.uuid4().hex[: run_id_len()]
    width = min(NAME_TOKEN_HEX, name_budget(REGISTRATION_NAME_MAX, len(normalized)))
    if width < run_id_len():
        raise ValueError(
            f"label {normalized!r} leaves {width} characters for a unique name "
            f"token; need at least {run_id_len()} within the "
            f"{REGISTRATION_NAME_MAX}-character registration name limit"
        )
    return uuid.uuid4().hex[:width]


def new_run_id() -> str:
    """Short token the session fixture draws once for the orphan sweep's self-skip.

    Provisions do not embed it; they draw :func:`name_token`. The sweep skips
    names that still contain this token. Per-provision names rely on the age
    gate, the same protection another job's agents have.
    """
    return uuid.uuid4().hex[: run_id_len()]


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
    api_key: str = field(repr=False)
    name: str
    adapter_id: str | None = None
    description: str = ""


def user_rest_client(
    settings: BaselineSettings, *, second: bool = False
) -> AsyncRestClient:
    """A user-authenticated REST client — the test-driver/observer identity, or
    with ``second`` the optional second human (``BAND_API_KEY_USER_2``).

    The one construction of the user client, shared by the pytest fixtures
    (``baseline_user_client``, ``second_user_ops``) and pytest-free callers (e.g.
    the sandbox staging smoke's ``probe.py``), so they can never drift. Like
    ``agent_rest_client`` below, the Fern client wraps an httpx pool with no
    public close hook and is left to be reclaimed at event-loop teardown.
    """
    credentials = settings.credentials
    api_key, name = (
        (credentials.api_key_user_2, "BAND_API_KEY_USER_2")
        if second
        else (credentials.api_key_user, "BAND_API_KEY_USER")
    )
    if not api_key:
        raise ValueError(f"{name} is required for the user REST client")
    return baseline_rest_client(api_key=api_key, base_url=settings.endpoints.rest_url)


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
    return baseline_rest_client(
        api_key=agent.api_key, base_url=settings.endpoints.rest_url
    )


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

    async def send_file(
        self,
        room_id: str,
        body: bytes,
        *,
        filename: str,
        content_type: str,
        caption: str,
        mention_id: str,
        mention_name: str,
    ) -> str:
        """Upload ``body`` as an attachment and post it as this peer; return the
        message id.

        Uploading is Agent-API-only (there is no ``human_api_files``), so a peer
        agent — never ``UserOps`` — has to play the uploader.
        """
        try:
            upload = await self._client.agent_api_files.upload_agent_chat_file(
                chat_id=room_id,
                request=body,
                request_options={
                    "additional_headers": {
                        "x-file-name": filename,
                        "x-file-sha256": hashlib.sha256(body).hexdigest(),
                        "content-type": content_type,
                    }
                },
            )
        except NotFoundError as error:
            raise RuntimeError(
                f"Uploading a file to room {room_id} as peer {self._peer.name} "
                "got 404 Not Found. Either the room doesn't exist, or this "
                "deployment has ff_file_transfer off (an on-prem-only flag, "
                "off on SaaS today) — see the local-platform-testing skill to "
                "stand up a deployment with it on."
            ) from error
        response = await self._client.agent_api_messages.create_agent_chat_message(
            room_id,
            message=ChatMessageRequest(
                content=caption,
                mentions=[
                    ChatMessageRequestMentionsItem(id=mention_id, name=mention_name)
                ],
                attachment_ids=[upload.data.id],
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

    async def provision_agent(
        self, label: str, *, description: str | None = None
    ) -> ProvisionedAgent:
        """Register a fresh agent and return its id + own API key.

        ``description`` is the Band agent description registered with the platform
        (default ``E2E baseline test agent ({label})``). Tests that assert on
        passive-roster description surfacing pass a self-sourced marker here.
        """
        name = agent_name(token=name_token(label), label=label)
        agent_description = description or f"E2E baseline test agent ({label})"
        response = await self._client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(
                name=name,
                description=agent_description,
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
        return ProvisionedAgent(
            id=agent.id,
            api_key=credentials.api_key,
            name=name,
            description=agent_description,
        )

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

    async def reap_all(self, *, strict: bool = False) -> None:
        """Attempt every owned deletion; strict acceptance reports all failures."""
        failures: list[Exception] = []
        for kind, ids, reap in (
            ("room", list(self._provisioned_room_ids), self.reap_room),
            ("agent", list(self._provisioned_agent_ids), self.reap_agent),
        ):
            for resource_id in ids:
                logger.info("Reaping %s %s", kind, resource_id)
                try:
                    await reap(resource_id)
                except Exception as error:
                    error.add_note(f"Cleanup incomplete for {kind} {resource_id}")
                    failures.append(error)
                    logger.warning(
                        "Failed to reap %s %s", kind, resource_id, exc_info=True
                    )
        if strict and failures:
            raise ExceptionGroup("Platform resource cleanup incomplete", failures)

    @asynccontextmanager
    async def reap_on_exit(self, *, enabled: bool = True) -> AsyncGenerator[None, None]:
        """Reap everything provisioned in this block when it exits.

        ``enabled`` is false when ``BAND_E2E_AUTOCLEAN`` is off, so a debug run
        keeps the resources. ``reap_all`` logs a delete failure and continues, so
        a teardown error does not replace an exception from the block. A row
        whose id was never returned is not tracked and is not deleted here; the
        next provision draws a new name, so that leftover cannot collide.
        """
        try:
            yield
        finally:
            if enabled:
                await self.reap_all()

    async def sweep_orphans(self, *, keep_ids: Sequence[str] = ()) -> int:
        """Force-delete stale test agents left by crashed prior runs.

        Only touches agents whose name carries ``NAME_PREFIX`` and that are
        older than ``orphan_max_age_minutes``. Names that still embed this
        session's run id are skipped, and so are ``keep_ids``: a per-provision
        name does not embed that id, so a caller that still owns an agent (the
        staging probe's recorded one, across a resume longer than the age gate)
        passes its id here. A concurrent run's fresh agents stay because they
        are younger than the age gate. Returns the number of agents reaped.
        """
        max_age = timedelta(minutes=self._settings.run.orphan_max_age_minutes)
        cutoff = datetime.now(UTC) - max_age

        # Collect candidates across all pages FIRST, then delete — deleting while
        # paginating would shrink the list and skip agents past a page boundary.
        # Cursor pagination (the SDK's preferred path; offset `page`/`page_size`
        # is deprecated): advance via metadata.next_cursor until has_more is
        # false. The iteration cap bounds a best-effort sweep.
        owned = set(keep_ids)
        orphans: list[str] = []
        cursor: str | None = None
        for _ in range(20):
            response = await self._client.human_api_agents.list_my_agents(
                name=NAME_PREFIX, limit=100, cursor=cursor
            )
            for agent in response.data or []:
                if not agent.name.startswith(NAME_PREFIX):
                    continue  # name filter is a contains-match; re-check the prefix
                if (
                    agent.id in owned
                    or f"{TOKEN_LABEL_SEPARATOR}{self._run_id}{TOKEN_LABEL_SEPARATOR}"
                    in agent.name
                ):
                    continue
                # inserted_at may be tz-naive depending on serialization; treat
                # naive as UTC so the comparison never raises (see the codebase's
                # _coerce_inserted_at). A naive>aware compare would TypeError and
                # abort the autouse session fixture.
                inserted = agent.inserted_at
                if inserted.tzinfo is None:
                    inserted = inserted.replace(tzinfo=UTC)
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
async def running_agent_with_handle(
    provisioned: ProvisionedAgent,
    adapter: SimpleAdapter[Any],
    settings: BaselineSettings,
    config: AgentConfig | None = None,
) -> AsyncGenerator[Agent, None]:
    """Like ``running_agent``, but yields the live ``Agent`` itself.

    ``running_agent`` only yields the identity because no caller has needed the
    object itself — reconnect-behavior tests do, to reach the running agent's
    transport via ``agent.runtime.link`` (both already-public properties).
    """
    endpoints = settings.endpoints
    agent = Agent.create(
        adapter=adapter,
        agent_id=provisioned.id,
        api_key=provisioned.api_key,
        ws_url=endpoints.ws_url,
        rest_url=endpoints.rest_url,
        config=config,
    )
    async with agent:
        yield agent


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
    async with running_agent_with_handle(provisioned, adapter, settings):
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
        workspace_for_room: WorkspaceResolver | None = None,
    ) -> SimpleAdapter[Any]:
        """Construct (do not run) this cell's adapter; arguments override cell defaults."""
        # Overrides use None-means-"cell default" (not a sentinel): no test needs to
        # clear a default back to "no prompt", so the sentinel would be dead machinery.
        kwargs = (
            {}
            if workspace_for_room is None
            else {"workspace_for_room": workspace_for_room}
        )
        return build_adapter(
            self.adapter_id,
            self.settings,
            prompt=self.prompt if prompt is None else prompt,
            features=self.features if features is None else features,
            tools=self.tools if tools is None else tools,
            **kwargs,
        )

    async def provision(self, *, label: str | None = None) -> ProvisionedAgent:
        """Register an identity for this cell (tracked + reaped by the manager); no run.

        ``label`` defaults to the adapter id (a readable provisioned name). Each
        call draws its own name token, so the same label can be registered again
        in one test without colliding with an agent this run already created.
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
    async def run_as_with_handle(
        self,
        identity: ProvisionedAgent,
        *,
        prompt: str | None = None,
        features: AdapterFeatures | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncGenerator[Agent, None]:
        """Like :meth:`run_as`, but yields the live ``Agent`` itself.

        For tests that need to reach the running agent's transport (e.g.
        ``band.testing.transport.force_transport_disconnect`` for reconnect
        coverage) via ``agent.runtime.link``.
        """
        adapter = self.build(prompt=prompt, features=features, tools=tools)
        with self.resources.track_running(identity.id):
            async with running_agent_with_handle(
                identity, adapter, self.settings
            ) as agent:
                yield agent

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
        ``count`` (one label per member). ``prompt`` / ``features`` /
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
