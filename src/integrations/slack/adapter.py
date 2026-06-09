"""Slack bridge adapter — wrapping shape.

Wraps an inner framework adapter (the agent's brain) and adds Slack
ingress/egress. One process, one Band identity, two transports:

- **Band WS path** (normal): platform delivers ``on_message`` →
  ``SlackAdapter.on_message`` delegates to ``inner.on_message``. If the
  room is bound to a Slack thread, the tools are wrapped so the brain's
  outgoing ``send_message`` is also posted to Slack.
- **Slack webhook path**: ``SlackAdapter`` synthesises a
  ``PlatformMessage`` from the Slack event and invokes
  ``inner.on_message`` directly with REST-backed tools that tee replies
  back to the originating Slack thread.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from band.client.rest import (
    AsyncRestClient,
    ChatEventRequest,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
)
from band.converters.slack import SlackHistoryConverter
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    PlatformMessage,
)
from band.integrations.slack.block_kit import (
    DEFAULT_WRITE_TOOL_NAMES,
    PlanState,
    PlanTask,
    TaskState,
    humanize_tool_name,
    plan_fallback_text,
    render_plan_blocks,
)
from band.integrations.slack.server import build_router
from band.integrations.slack.types import SlackApp, SlackRoomBinding
from band.runtime.tools import AgentTools

if TYPE_CHECKING:
    from slack_sdk.web.async_client import AsyncWebClient
    from starlette.routing import Router

    from band.integrations.slack.socket import SlackSocketListener


SlackTransport = Literal["http", "socket"]

logger = logging.getLogger(__name__)

# Factory signature: takes a SlackApp config and returns the AsyncWebClient
# to use for outbound Slack API calls. Exposed for test injection.
WebClientFactory = Callable[[SlackApp], "AsyncWebClient"]

# Status string shown via ``assistant.threads.setStatus`` while the
# brain is working. Empty string clears the status.
STATUS_THINKING = "is thinking…"

# Name of the Slack-only outbound tool exposed to the brain when the
# room is bound to a Slack thread.
SLACK_SEND_MESSAGE_TOOL_NAME = "slack_send_message"

_SLACK_SEND_MESSAGE_DESCRIPTION = (
    "Send a plain-text reply to the Slack user who triggered this "
    "conversation. The message is posted into the originating Slack thread. "
    "Use this for the final answer and any user-facing updates. Does not "
    "post to the Band room.\n\n"
    "Use band_send_message (which requires @mentions) to talk to other "
    "Band peers in the room."
)

_SLACK_SEND_MESSAGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The plain-text message to send to the Slack user.",
        },
    },
    "required": ["content"],
}

SLACK_CONTEXT_NOTE = (
    "[Slack] This conversation originated from Slack. The triggering user is "
    "not a Band peer and cannot be @-mentioned. To reply to them, call "
    f"{SLACK_SEND_MESSAGE_TOOL_NAME}(content=...). "
    "Use band_send_message(content, mentions=[...]) only to coordinate "
    "with other Band peers in this room."
)


def _merge_context_note(existing: str | None, note: str) -> str:
    """Combine an existing participants_msg (if any) with the Slack note.

    Returns ``note`` if there is no existing message; otherwise prepends
    ``note`` followed by a blank line so the upstream message stays
    readable to the brain.
    """
    if not existing:
        return note
    return f"{note}\n\n{existing}"


class _SlackTeeingTools(AgentTools):
    """``AgentTools`` subclass that adds a Slack-only ``slack_send_message`` tool.

    The brain sees two outbound options:

    - ``band_send_message`` (inherited, unchanged) — real Band message
      to peers in the current room. Still requires ≥1 mention.
    - ``slack_send_message`` (new) — posts directly to the bound Slack thread
      via ``chat.postMessage``. Bypasses Band entirely.

    The brain decides which to use based on intent. The system-prompt
    note (passed via ``participants_msg``) tells it when to pick each.
    """

    def __init__(
        self,
        *,
        wrap: AgentTools,
        slack: AsyncWebClient,
        binding: SlackRoomBinding,
        write_tool_names: frozenset[str] | set[str] | None = None,
        show_tool_progress: bool = True,
    ) -> None:
        super().__init__(
            room_id=wrap.room_id,
            rest=wrap.rest,
            participants=list(wrap.participants),
            hub_room_id=wrap._hub_room_id,
        )
        # Carry ExecutionContext over so any tool methods that lean on
        # it (e.g. lookup_peers) keep working.
        self._ctx = wrap._ctx
        self._slack = slack
        self._binding = binding
        self._write_tool_names: frozenset[str] = (
            frozenset(write_tool_names)
            if write_tool_names is not None
            else DEFAULT_WRITE_TOOL_NAMES
        )
        self._show_tool_progress = show_tool_progress
        self._plan = PlanState()

    async def _upsert_plan_message(self) -> None:
        blocks = render_plan_blocks(self._plan)
        fallback = plan_fallback_text(self._plan)
        try:
            if self._plan.message_ts is None:
                response = await self._slack.chat_postMessage(
                    channel=self._binding.channel,
                    thread_ts=self._binding.thread_ts,
                    blocks=blocks,
                    text=fallback,
                )
                # slack-sdk's SlackResponse is dict-like; AsyncMock returns dict.
                ts: str | None = None
                try:
                    ts = response["ts"]  # type: ignore[index]
                except (KeyError, TypeError):
                    ts = getattr(response, "ts", None)
                if ts:
                    self._plan.message_ts = ts
            else:
                await self._slack.chat_update(
                    channel=self._binding.channel,
                    ts=self._plan.message_ts,
                    blocks=blocks,
                    text=fallback,
                )
        except Exception:
            logger.exception(
                "Failed to upsert Slack plan message (channel=%s thread_ts=%s)",
                self._binding.channel,
                self._binding.thread_ts,
            )

    async def slack_send_message(self, content: str) -> dict[str, Any]:
        """Post a plain-text reply to the bound Slack thread.

        Returns ``{"ok": True}`` on success, ``{"ok": False, "error": ...}``
        on failure — the LLM can inspect the result and react if posting
        failed (e.g. the channel was deleted, the bot was kicked).
        """
        try:
            await self._slack.chat_postMessage(
                channel=self._binding.channel,
                text=content,
                thread_ts=self._binding.thread_ts,
            )
        except Exception as exc:
            logger.exception(
                "slack_send_message failed (channel=%s thread_ts=%s)",
                self._binding.channel,
                self._binding.thread_ts,
            )
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    # ── Schema injection ────────────────────────────────────────────

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]] | list[Any]:
        """Return the base schemas plus our ``slack_send_message`` entry."""
        base = super().get_tool_schemas(
            format,
            include_memory=include_memory,
            include_contacts=include_contacts,
        )
        if format == "openai":
            slack_schema: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": SLACK_SEND_MESSAGE_TOOL_NAME,
                    "description": _SLACK_SEND_MESSAGE_DESCRIPTION,
                    "parameters": _SLACK_SEND_MESSAGE_INPUT_SCHEMA,
                },
            }
        else:  # anthropic
            slack_schema = {
                "name": SLACK_SEND_MESSAGE_TOOL_NAME,
                "description": _SLACK_SEND_MESSAGE_DESCRIPTION,
                "input_schema": _SLACK_SEND_MESSAGE_INPUT_SCHEMA,
            }
        return [*base, slack_schema]

    # ── Dispatch ────────────────────────────────────────────────────

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Route ``slack_send_message`` to our handler; render plan blocks
        in Slack as a side effect of every other tool call.

        Plan rendering is observed directly here (not by intercepting
        ``send_event``) so it's independent of the brain's
        ``Emit.EXECUTION`` setting. The two emit/visibility knobs are
        fully orthogonal:

        - Brain's ``Emit.EXECUTION`` → controls Band-side recording
          of ``tool_call``/``tool_result`` events.
        - ``SlackAdapter.show_tool_progress`` → controls Slack-side
          plan-block rendering. Lives on this object as
          ``_show_tool_progress``.
        """
        # Our own Slack-only tool — not a "progress task" because it IS
        # the user-facing reply, not work-in-progress.
        if tool_name == SLACK_SEND_MESSAGE_TOOL_NAME:
            content = arguments.get("content")
            if not isinstance(content, str) or not content:
                return (
                    f"Error: {SLACK_SEND_MESSAGE_TOOL_NAME} requires a "
                    "non-empty 'content' string."
                )
            return await self.slack_send_message(content)

        # Mark in_progress before executing, mark completed/error after.
        task: PlanTask | None = None
        if self._show_tool_progress:
            task = PlanTask(
                id=str(uuid.uuid4()),
                label=humanize_tool_name(tool_name),
                state=TaskState.IN_PROGRESS,
                is_write=tool_name in self._write_tool_names,
            )
            self._plan.tasks.append(task)
            self._plan.tasks_by_id[task.id] = task
            await self._upsert_plan_message()

        # Use the structured variant so success/failure comes from the
        # ``ok`` flag rather than sniffing the returned string — the base
        # tools have no single ``Error:`` prefix (failures surface as
        # "Invalid arguments for …", "Error executing …", "Unknown tool: …"),
        # so prefix matching would silently mark failed calls ✅.
        try:
            outcome = await super().execute_tool_call_structured(tool_name, arguments)
        except Exception as exc:
            if task is not None:
                task.state = TaskState.ERROR
                task.error_message = str(exc)
                await self._upsert_plan_message()
            raise

        if task is not None:
            if outcome.ok:
                task.state = TaskState.COMPLETED
            else:
                task.state = TaskState.ERROR
                task.error_message = outcome.error_message
            await self._upsert_plan_message()
        return outcome.value


class SlackAdapter(SimpleAdapter[Any]):
    """Wraps an inner framework adapter and adds Slack I/O.

    Example:
        from band import Agent
        from band.adapters import AnthropicAdapter
        from band.integrations.slack import SlackAdapter, SlackApp

        brain = AnthropicAdapter(model="claude-sonnet-4-6")
        slack = SlackAdapter(
            inner=brain,
            apps=[
                SlackApp(
                    slug="dev",
                    signing_secret="...",
                    bot_token="xoxb-...",
                ),
            ],
            api_key="...",
        )
        agent = Agent.create(adapter=slack, agent_id="...", api_key="...")
        # mount slack.router into your ASGI app on the side, e.g.:
        #   starlette_app.mount("/slack", slack.router)
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        *,
        inner: SimpleAdapter[Any],
        apps: list[SlackApp],
        rest_url: str = "https://app.band.ai",
        api_key: str = "",
        port: int = 3000,
        transport: SlackTransport = "http",
        web_client_factory: WebClientFactory | None = None,
        rest_client: AsyncRestClient | None = None,
        features: AdapterFeatures | None = None,
        write_tool_names: frozenset[str] | set[str] | None = None,
        show_tool_progress: bool = True,
        mirror_slack_context: bool = True,
    ) -> None:
        """Initialize the Slack adapter.

        Args:
            inner: The framework adapter that does the actual reasoning
                (e.g. ``AnthropicAdapter``, ``LangGraphAdapter``).
            apps: One or more ``SlackApp`` configurations.
            rest_url: Base URL for the Band REST API.
            api_key: API key for the Band agent (same key passed to
                ``Agent.create``). Used to mirror Slack messages into
                Band rooms.
            port: TCP port for the HTTP server.
            transport: ``"http"`` (default) serves events via a mountable
                Starlette router; the developer points their Slack app's
                Event Subscriptions URL at the bridge. ``"socket"`` opens
                a Socket Mode websocket to Slack per app — no public URL
                or signing secret is needed; each ``SlackApp`` must supply
                ``app_token`` (``xapp-...``).
            web_client_factory: Optional factory for injecting mock
                ``AsyncWebClient`` instances in tests.
            rest_client: Optional ``AsyncRestClient`` injection seam.
            features: Optional override for adapter features. Defaults
                to the inner adapter's features so the brain's
                capabilities flow through unchanged.
            mirror_slack_context: When ``True`` (default), each inbound
                Slack user turn is mirrored into the bound Band room
                as a context-only ``thought`` event so the Band UI
                audit timeline reflects the Slack-side conversation.
                These events are tagged ``slack_mirror`` and never loop
                back into the brain's history or trigger peer replies.
                Set ``False`` to leave bridged rooms holding only the
                bootstrap context event.

        Raises:
            ImportError: If ``slack-sdk`` is not installed.
            ValueError: If ``apps`` is empty, or a per-app token required
                by the chosen transport is missing.
        """
        try:
            import slack_sdk  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "slack-sdk is required for SlackAdapter. "
                "Install with: uv add band-sdk[slack]"
            ) from exc

        if not apps:
            raise ValueError("SlackAdapter requires at least one SlackApp config")

        if transport == "http":
            missing = [a.slug for a in apps if not a.signing_secret or not a.bot_token]
            if missing:
                raise ValueError(
                    "SlackAdapter(transport='http') requires signing_secret "
                    "and bot_token on every SlackApp; missing for: "
                    f"{', '.join(missing)}"
                )
        elif transport == "socket":
            missing = [a.slug for a in apps if not a.app_token or not a.bot_token]
            if missing:
                raise ValueError(
                    "SlackAdapter(transport='socket') requires app_token "
                    "(xapp-...) and bot_token on every SlackApp; missing "
                    f"for: {', '.join(missing)}"
                )
        else:
            raise ValueError(
                f"Unknown transport={transport!r}; expected 'http' or 'socket'"
            )

        # Use the inner adapter's history converter and feature settings
        # so the brain sees its native history type and capabilities.
        super().__init__(
            history_converter=inner.history_converter or SlackHistoryConverter(),
            features=features or inner.features,
        )
        self._inner = inner
        self.apps = apps
        self._rest_url = rest_url
        self._api_key = api_key
        self._port = port
        self._transport: SlackTransport = transport
        self._router: Router | None = None
        self._socket_listeners: list[SlackSocketListener] = []
        self._web_client_factory: WebClientFactory = (
            web_client_factory or self._default_web_client_factory
        )
        self._web_clients: dict[str, AsyncWebClient] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._write_tool_names: frozenset[str] = (
            frozenset(write_tool_names)
            if write_tool_names is not None
            else DEFAULT_WRITE_TOOL_NAMES
        )
        self._show_tool_progress = show_tool_progress
        self._mirror_slack_context = mirror_slack_context

        # Band-side state.
        self._rest: AsyncRestClient | None = rest_client
        self._apps_by_slug: dict[str, SlackApp] = {a.slug: a for a in apps}
        self._thread_to_room: dict[str, str] = {}
        self._room_to_binding: dict[str, SlackRoomBinding] = {}
        # Per-thread locks serialise room creation so two concurrent Slack
        # events for the same thread can't both miss the map and create
        # duplicate rooms. Keyed by the same ``_thread_key`` as
        # ``_thread_to_room``.
        self._room_locks: dict[str, asyncio.Lock] = {}
        # Resolved Slack ``bot_id`` per app (from ``auth.test``), used to
        # distinguish this bridge's own prior replies from other bots /
        # webhooks when backfilling thread history. Resolved lazily.
        self._bot_ids: dict[str, str | None] = {}

        # Best-effort label caches for the context mirror. Slack user/
        # channel identities are stable, so resolve each once and reuse.
        # value: (display_name, handle)
        self._user_label_cache: dict[str, tuple[str, str]] = {}
        # value: channel display label (e.g. "#general" or "DM")
        self._channel_label_cache: dict[str, str] = {}

    @property
    def inner(self) -> SimpleAdapter[Any]:
        """The wrapped framework adapter (the brain)."""
        return self._inner

    @property
    def transport(self) -> SlackTransport:
        """Which inbound transport this adapter was configured with."""
        return self._transport

    @property
    def router(self) -> Router:
        """Starlette router serving the configured Slack apps.

        Only meaningful for ``transport="http"``. In Socket Mode the
        bridge has no HTTP surface to mount, so accessing ``router`` is
        almost certainly a misconfiguration.
        """
        if self._transport != "http":
            raise RuntimeError(
                "SlackAdapter.router is only available for transport='http'; "
                f"this adapter is using transport={self._transport!r}."
            )
        if self._router is None:
            self._router = build_router(self.apps, dispatcher=self._dispatch_event)
        return self._router

    async def wait_idle(self) -> None:
        """Wait for all background event handlers to complete."""
        while self._background_tasks:
            tasks = list(self._background_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build the REST client and start the inner adapter."""
        # We adopt ``inner.features`` (see __init__) and delegate all
        # reasoning to the inner brain, so the inner — not this wrapper — is
        # what actually acts on emit/capability values. Mirror its declared
        # support before the base feature-mismatch check runs, otherwise
        # SimpleAdapter warns "SlackAdapter does not support emit values:
        # execution" even though the brain handles it. The inner's own
        # on_started still performs the authoritative check.
        # type: ignore[read-only] — base declares these ClassVar; shadowing
        # per-instance is intentional since support is the inner's, not ours.
        self.SUPPORTED_EMIT = self._inner.SUPPORTED_EMIT  # type: ignore[read-only]
        self.SUPPORTED_CAPABILITIES = self._inner.SUPPORTED_CAPABILITIES  # type: ignore[read-only]
        await super().on_started(agent_name, agent_description)

        if self._rest is None:
            if not self._api_key:
                raise ValueError(
                    "SlackAdapter requires api_key to reach the Band REST API; "
                    "pass it via SlackAdapter(api_key=...)."
                )
            self._rest = AsyncRestClient(base_url=self._rest_url, api_key=self._api_key)

        # Propagate the agent identity to the inner adapter. ``Agent.start``
        # sets ``_band_agent_id`` on us before calling ``on_started``;
        # the inner adapter needs it too so it can dedup own messages.
        own_id = getattr(self, "_band_agent_id", None)
        if own_id is not None:
            setattr(self._inner, "_band_agent_id", own_id)

        await self._inner.on_started(agent_name, agent_description)

        if self._transport == "socket":
            # Lazy import so HTTP-only installs don't pay the aiohttp /
            # Socket Mode import cost.
            from band.integrations.slack.socket import (
                start_socket_listeners,
            )

            self._socket_listeners = await start_socket_listeners(
                apps=self.apps,
                web_client_factory=self._get_client,
                dispatcher=self._dispatch_event,
            )

        logger.info(
            "Slack adapter started: %s (apps=%d, transport=%s)",
            agent_name,
            len(self.apps),
            self._transport,
        )

    async def close(self) -> None:
        """Tear down transport-owned resources.

        For Socket Mode this disconnects each per-app websocket client.
        For HTTP it's a no-op — the developer's ASGI server owns the
        HTTP lifecycle. Safe to call multiple times.
        """
        if self._socket_listeners:
            listeners = self._socket_listeners
            self._socket_listeners = []
            for listener in listeners:
                try:
                    await listener.stop()
                except Exception:
                    logger.exception(
                        "Failed to stop Slack Socket Mode listener (app=%s)",
                        listener.app.slug,
                    )

    async def on_event(self, inp: AgentInput) -> None:
        """Rehydrate Slack binding on bootstrap, then delegate as normal.

        The wrapped inner adapter (Anthropic, LangGraph, ...) owns its own
        history converter for the brain. To recover the Slack ↔ Band
        thread mapping after a restart we run :class:`SlackHistoryConverter`
        over the same raw history first, fold any recovered binding into
        ``_thread_to_room`` + ``_room_to_binding``, then hand off to
        ``SimpleAdapter.on_event`` so the inner converter still runs and
        ``on_message`` sees the brain's native history type.
        """
        if inp.is_session_bootstrap:
            self._rehydrate_room(inp.room_id, inp.history.raw)
        await super().on_event(inp)

    def _rehydrate_room(self, room_id: str, raw_history: list[dict[str, Any]]) -> None:
        """Restore ``_thread_to_room`` and ``_room_to_binding`` for one room.

        No-op when the history has no Slack bootstrap event (e.g. the
        room was created outside this bridge), and when the recovered
        binding points at an app slug we no longer have configured we
        still record the binding so other diagnostics surface the
        mismatch — :py:meth:`on_message` already gates outbound calls
        on ``_apps_by_slug``.
        """
        state = SlackHistoryConverter().convert(raw_history)
        if state.binding is None:
            return
        if room_id in self._room_to_binding:
            return
        binding = state.binding
        thread_key = self._thread_key(
            binding.app_slug, binding.channel, binding.thread_ts
        )
        # Don't trample an active mapping for the same thread — the
        # live in-memory binding is authoritative.
        self._thread_to_room.setdefault(thread_key, room_id)
        self._room_to_binding[room_id] = binding
        logger.info(
            "Rehydrated Slack binding: room=%s app=%s thread=%s",
            room_id,
            binding.app_slug,
            thread_key,
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Delegate to the inner brain, teeing replies to Slack if bound."""
        binding = self._room_to_binding.get(room_id)
        slack_client: AsyncWebClient | None = None
        if binding is not None and isinstance(tools, AgentTools):
            app = self._apps_by_slug.get(binding.app_slug)
            if app is not None:
                slack_client = self._get_client(app)
                tools = _SlackTeeingTools(
                    wrap=tools,
                    slack=slack_client,
                    binding=binding,
                    write_tool_names=self._write_tool_names,
                    show_tool_progress=self._show_tool_progress,
                )
            else:
                logger.warning(
                    "Room %s bound to unknown app %s; not teeing",
                    room_id,
                    binding.app_slug,
                )

        # Status indicator only meaningful in Slack-bound rooms.
        # Prepend the Slack-context note to participants_msg so the brain
        # knows which outbound tool to use for the user-facing reply.
        if slack_client is not None and binding is not None:
            await self._set_status(
                slack_client, binding.channel, binding.thread_ts, STATUS_THINKING
            )
            participants_msg = _merge_context_note(participants_msg, SLACK_CONTEXT_NOTE)
        try:
            await self._inner.on_message(
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                room_id=room_id,
            )
        finally:
            if slack_client is not None and binding is not None:
                await self._set_status(
                    slack_client, binding.channel, binding.thread_ts, ""
                )

    async def on_cleanup(self, room_id: str) -> None:
        """Drop per-room state and forward to the inner adapter."""
        binding = self._room_to_binding.pop(room_id, None)
        if binding is not None:
            thread_key = self._thread_key(
                binding.app_slug, binding.channel, binding.thread_ts
            )
            self._thread_to_room.pop(thread_key, None)
            self._room_locks.pop(thread_key, None)
        await self._inner.on_cleanup(room_id)

    # ── Slack ingress (HTTP webhook) ──────────────────────────────────

    async def _dispatch_event(self, app: SlackApp, payload: dict[str, Any]) -> None:
        """Schedule a Slack event for background processing and return.

        Slack requires HTTP 200 within 3 seconds; we fire the actual
        work into a task so the route handler can ack immediately.
        """
        task = asyncio.create_task(self._handle_event(app, payload))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    def _on_background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("Slack background event handler crashed", exc_info=exc)

    async def _handle_event(self, app: SlackApp, payload: dict[str, Any]) -> None:
        event = payload.get("event") or {}
        event_type = event.get("type")

        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        if event_type == "app_mention":
            await self._invoke_brain_for_slack_event(app, event)
        elif event_type == "message" and event.get("channel_type") == "im":
            await self._invoke_brain_for_slack_event(app, event)

    async def _invoke_brain_for_slack_event(
        self, app: SlackApp, event: dict[str, Any]
    ) -> None:
        """Synthesize a ``PlatformMessage`` and call ``inner.on_message``."""
        text = event.get("text", "")
        channel = event.get("channel")
        slack_user = event.get("user", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts") or ts

        if not channel or not text or not thread_ts:
            logger.debug(
                "Skipping Slack event for app %s: missing channel/text/thread_ts",
                app.slug,
            )
            return

        room_id, is_new_room = await self._get_or_create_room(
            app=app,
            channel=channel,
            thread_ts=thread_ts,
            slack_user=slack_user,
        )

        binding = self._room_to_binding[room_id]
        assert self._rest is not None
        synthesized = PlatformMessage(
            id=f"slack:{ts}",
            room_id=room_id,
            content=text,
            sender_id=f"slack:{slack_user}",
            sender_type="user",
            sender_name=slack_user or "slack-user",
            message_type="text",
            metadata={
                "slack_app_slug": app.slug,
                "slack_channel_id": channel,
                "slack_thread_ts": thread_ts,
                "slack_user_id": slack_user,
            },
            created_at=datetime.now(timezone.utc),
        )

        # Mirror the user turn into the Band room for audit visibility.
        # Best-effort and tagged context-only; happens before the brain
        # runs so the timeline ordering matches reality.
        if self._mirror_slack_context:
            await self._mirror_user_turn_to_room(
                room_id=room_id,
                app=app,
                channel=channel,
                thread_ts=thread_ts,
                slack_user=slack_user,
                ts=ts,
                text=text,
            )

        slack_client = self._get_client(app)
        real_tools = AgentTools(room_id=room_id, rest=self._rest, participants=[])
        tools = _SlackTeeingTools(
            wrap=real_tools,
            slack=slack_client,
            binding=binding,
            write_tool_names=self._write_tool_names,
            show_tool_progress=self._show_tool_progress,
        )

        # Pull thread context from Slack on every event that lands inside
        # an existing thread (``thread_ts != ts``). Slack does NOT deliver
        # prior turns with the event payload, and its own Bolt JS
        # reference implementation calls ``conversations.replies`` per
        # user message for exactly this reason — see
        # https://docs.slack.dev/tools/bolt-js/tutorials/ai-assistant/.
        # Caching the fetched result across turns would break stateless
        # brains (Anthropic, etc.) on every follow-up message. Top-level
        # mentions and brand-new DMs (``thread_ts == ts``) have no prior
        # context to fetch.
        raw_history: list[dict[str, Any]] = []
        if thread_ts != ts:
            raw_history = await self._fetch_thread_history(
                app=app,
                slack=slack_client,
                channel=channel,
                thread_ts=thread_ts,
                exclude_ts=ts,
            )

        if self._inner.history_converter is not None:
            history = self._inner.history_converter.convert(raw_history)
        else:
            history = None

        await self._set_status(slack_client, channel, thread_ts, STATUS_THINKING)
        try:
            await self._inner.on_message(
                synthesized,
                tools,
                history,
                SLACK_CONTEXT_NOTE,
                None,
                is_session_bootstrap=is_new_room,
                room_id=room_id,
            )
        finally:
            await self._set_status(slack_client, channel, thread_ts, "")

    # ── Room management ──────────────────────────────────────────────

    @staticmethod
    def _thread_key(app_slug: str, channel: str, thread_ts: str) -> str:
        # Include ``app_slug`` so two SlackApps that happen to see the same
        # workspace-scoped ``channel:thread_ts`` tuple map to distinct rooms
        # and never cross-route replies into the wrong app/workspace.
        return f"{app_slug}:{channel}:{thread_ts}"

    async def _get_or_create_room(
        self,
        *,
        app: SlackApp,
        channel: str,
        thread_ts: str,
        slack_user: str,
    ) -> tuple[str, bool]:
        """Return ``(room_id, is_new_room)`` for the given Slack thread."""
        thread_key = self._thread_key(app.slug, channel, thread_ts)
        existing = self._thread_to_room.get(thread_key)
        if existing is not None:
            return existing, False

        # Serialise creation per thread. Every Slack event runs in its own
        # background task, so without this two events for one thread can
        # both miss the map above, both create a room, and race on the
        # write below — leaving one orphaned room with a bootstrap event
        # and no traffic. The lock + re-check collapses that to one room.
        lock = self._room_locks.setdefault(thread_key, asyncio.Lock())
        async with lock:
            existing = self._thread_to_room.get(thread_key)
            if existing is not None:
                return existing, False

            assert self._rest is not None
            response = await self._rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            room_id = response.data.id

            # Persist Slack thread context as a ``task`` event so
            # ``SlackHistoryConverter`` can rehydrate ``thread_to_room`` after
            # the agent restarts.
            await self._emit_context_event(
                room_id=room_id,
                app=app,
                channel=channel,
                thread_ts=thread_ts,
                slack_user=slack_user,
            )

            self._thread_to_room[thread_key] = room_id
            self._room_to_binding[room_id] = SlackRoomBinding(
                app_slug=app.slug,
                channel=channel,
                thread_ts=thread_ts,
            )

        logger.info(
            "Created Band room %s for Slack thread %s (app=%s)",
            room_id,
            thread_key,
            app.slug,
        )
        return room_id, True

    async def _emit_context_event(
        self,
        *,
        room_id: str,
        app: SlackApp,
        channel: str,
        thread_ts: str,
        slack_user: str,
    ) -> None:
        assert self._rest is not None
        await self._rest.agent_api_events.create_agent_chat_event(
            chat_id=room_id,
            event=ChatEventRequest(
                content="Slack thread context",
                message_type="task",
                metadata={
                    "slack_app_slug": app.slug,
                    "slack_channel_id": channel,
                    "slack_thread_ts": thread_ts,
                    "slack_user_id": slack_user,
                    "slack_room_id": room_id,
                },
            ),
        )

    # ── Slack context mirroring (audit timeline) ─────────────────────

    async def _mirror_user_turn_to_room(
        self,
        *,
        room_id: str,
        app: SlackApp,
        channel: str,
        thread_ts: str,
        slack_user: str,
        ts: str,
        text: str,
    ) -> None:
        """Mirror an inbound Slack user turn into the room as a ``thought``.

        Slack-bridged rooms otherwise hold only the bootstrap ``task``
        event, so the Band UI audit timeline is blank while
        real conversations happen on Slack. This posts each triggering
        user message as a ``thought`` event carrying the Slack thread
        identity so the timeline reflects what was actually said.

        Emitted as a ``thought`` (which history converters skip) and
        tagged ``slack_mirror`` in metadata so it never loops back into
        the brain's history or triggers peer replies. Best-effort: any
        failure here is logged and swallowed so it can't break the reply
        path.
        """
        assert self._rest is not None
        slack_client = self._get_client(app)
        display_name, handle = await self._resolve_user_label(slack_client, slack_user)
        channel_label = await self._resolve_channel_label(slack_client, channel)

        # Friendly, human-readable line for the Band audit timeline.
        # Raw ids / thread ts stay in metadata; the visible content shows
        # who said what and where.
        if display_name and handle:
            who = f"{display_name} (@{handle})"
        elif display_name or handle:
            who = display_name or f"@{handle}"
        else:
            who = "Slack user"
        content = f"💬 Slack · {channel_label} — {who}: {text}"

        try:
            await self._rest.agent_api_events.create_agent_chat_event(
                chat_id=room_id,
                event=ChatEventRequest(
                    content=content,
                    message_type="thought",
                    metadata={
                        "slack_mirror": True,
                        "slack_app_slug": app.slug,
                        "slack_channel_id": channel,
                        "slack_channel_label": channel_label,
                        "slack_thread_ts": thread_ts,
                        "slack_user_id": slack_user,
                        "slack_user_handle": handle,
                        "slack_user_name": display_name,
                        "slack_ts": ts,
                    },
                ),
            )
        except Exception:
            logger.exception(
                "Failed to mirror Slack user turn to room %s (channel=%s thread_ts=%s)",
                room_id,
                channel,
                thread_ts,
            )

    async def _resolve_user_label(
        self, slack: AsyncWebClient, user_id: str
    ) -> tuple[str, str]:
        """Return ``(display_name, handle)`` for a Slack user id.

        Cached and best-effort: on any failure (missing scope, unknown
        user) returns empty strings so the caller falls back gracefully.
        """
        if not user_id:
            return "", ""
        cached = self._user_label_cache.get(user_id)
        if cached is not None:
            return cached
        display_name, handle = "", ""
        try:
            resp = await slack.users_info(user=user_id)
            user = resp.get("user", {}) or {}
            profile = user.get("profile", {}) or {}
            display_name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name")
                or ""
            )
            handle = user.get("name", "") or ""
        except Exception as exc:
            logger.debug("users.info failed for %s: %s", user_id, exc)
        label = (display_name, handle)
        self._user_label_cache[user_id] = label
        return label

    async def _resolve_channel_label(
        self, slack: AsyncWebClient, channel_id: str
    ) -> str:
        """Return a friendly channel label (``#name``, or ``DM``).

        Cached and best-effort: on failure falls back to the raw id.
        """
        if not channel_id:
            return "unknown channel"
        cached = self._channel_label_cache.get(channel_id)
        if cached is not None:
            return cached
        label = channel_id
        try:
            resp = await slack.conversations_info(channel=channel_id)
            ch = resp.get("channel", {}) or {}
            if ch.get("is_im"):
                label = "DM"
            elif ch.get("name"):
                label = f"#{ch['name']}"
        except Exception as exc:
            logger.debug("conversations.info failed for %s: %s", channel_id, exc)
        self._channel_label_cache[channel_id] = label
        return label

    # ── Slack thread history backfill ────────────────────────────────

    async def _fetch_thread_history(
        self,
        *,
        app: SlackApp,
        slack: AsyncWebClient,
        channel: str,
        thread_ts: str,
        exclude_ts: str,
    ) -> list[dict[str, Any]]:
        """Pull a Slack thread's prior messages via ``conversations.replies``.

        Returns raw history dicts in the same shape as
        ``format_history_for_llm()`` output, so the inner adapter's
        ``history_converter`` can ingest them with no special-casing.

        Only messages authored by *this app's own bot* (matched on the
        ``bot_id`` resolved via ``auth.test``) are mapped to
        ``sender_type="Agent"`` / ``role="assistant"`` so converters treat
        them as the brain's prior turns. Other bots and webhooks in the
        thread are included as external context with a
        ``slack-bot:<id>`` sender so they can never be mistaken for our
        own assistant turns.

        Requires the Slack ``channels:history`` (public channels) and
        ``groups:history`` (private channels) scopes for non-DM mentions.
        If the call fails (missing scope, deleted channel, etc.) we log
        and return an empty list — backfill is best-effort, not a hard
        requirement for replying to the trigger.
        """
        logger.info(
            "Fetching Slack thread history (channel=%s thread_ts=%s)",
            channel,
            thread_ts,
        )
        try:
            response = await slack.conversations_replies(
                channel=channel,
                ts=thread_ts,
            )
        except Exception as exc:
            # Pull the Slack-level error code out of SlackApiError so the
            # diagnosis ("missing_scope, needed: groups:history") shows up
            # on the headline log line instead of buried in the traceback.
            slack_err: dict[str, Any] = {}
            response_obj = getattr(exc, "response", None)
            if response_obj is not None:
                data = getattr(response_obj, "data", None)
                if isinstance(data, dict):
                    slack_err = {
                        k: data.get(k) for k in ("error", "needed", "provided")
                    }
            logger.exception(
                "conversations.replies FAILED (channel=%s thread_ts=%s) "
                "slack_error=%s — usually means missing scope "
                "(channels:history for public channels, groups:history "
                "for private channels, im:history for DMs, mpim:history "
                "for group DMs) or the bot is not a member of the channel",
                channel,
                thread_ts,
                slack_err or "<unknown>",
            )
            return []

        try:
            messages: list[dict[str, Any]] = list(response["messages"])  # type: ignore[index]
        except (KeyError, TypeError):
            messages = list(getattr(response, "messages", []) or [])
        logger.info(
            "conversations.replies returned %d message(s) for thread %s:%s",
            len(messages),
            channel,
            thread_ts,
        )

        own_bot_id = await self._resolve_bot_id(app, slack)
        raw: list[dict[str, Any]] = []
        agent_name = self.agent_name or "agent"
        for m in messages:
            if m.get("ts") == exclude_ts:
                continue
            text = m.get("text", "") or ""
            if not text:
                continue
            msg_bot_id = m.get("bot_id")
            is_bot = bool(msg_bot_id) or m.get("subtype") == "bot_message"
            # Only this bridge's own bot counts as our prior assistant turns.
            # ``own_bot_id`` is None when auth.test failed — then we can't
            # prove ownership, so treat every bot message as external.
            is_own_bot = is_bot and own_bot_id is not None and msg_bot_id == own_bot_id
            # Skip the bridge's own Block Kit progress/plan/status messages.
            # Those are posted with ``blocks`` and a placeholder fallback
            # ("Working on it…"/"Done"); re-ingesting them would feed the
            # brain fake assistant turns on every follow-up. Real replies
            # (slack_send_message) are plain text with no blocks, so they're
            # kept. Other apps' block messages are dropped too — arbitrary
            # block payloads don't convert to useful plain-text history.
            if is_own_bot and m.get("blocks"):
                continue
            if is_own_bot:
                sender_name = agent_name
                sender_type = "Agent"
                role = "assistant"
            elif is_bot:
                # Another bot/webhook in the thread — external context, never
                # our own assistant turn. Identify it so the brain can tell
                # it apart from human users.
                bot_label = msg_bot_id or m.get("username") or "unknown"
                sender_name = f"slack-bot:{bot_label}"
                sender_type = "user"
                role = "user"
            else:
                slack_user = m.get("user") or "slack-user"
                sender_name = f"slack:{slack_user}"
                sender_type = "user"
                role = "user"
            raw.append(
                {
                    "role": role,
                    "content": text,
                    "sender_name": sender_name,
                    "sender_type": sender_type,
                    "message_type": "text",
                    "metadata": {
                        "slack_channel_id": channel,
                        "slack_thread_ts": thread_ts,
                        "slack_ts": m.get("ts"),
                    },
                }
            )
        logger.info(
            "Slack thread backfill: %d message(s) kept for brain "
            "(channel=%s thread_ts=%s, trigger ts=%s excluded)",
            len(raw),
            channel,
            thread_ts,
            exclude_ts,
        )
        return raw

    # ── Slack assistant-pane status indicators ────────────────────────

    @staticmethod
    async def _set_status(
        slack: AsyncWebClient,
        channel: str,
        thread_ts: str,
        status: str,
    ) -> None:
        """Set or clear the Slack assistant-pane status indicator.

        ``assistant.threads.setStatus`` only takes effect in the Slack
        Agents & AI Apps assistant pane (and requires the
        ``assistant:write`` scope). For DMs/channels outside that
        surface, Slack returns an error which we swallow at DEBUG —
        the bot still works, the user just doesn't see "thinking…".
        """
        try:
            await slack.assistant_threads_setStatus(
                channel_id=channel,
                thread_ts=thread_ts,
                status=status,
            )
        except Exception:
            logger.debug(
                "assistant.threads.setStatus failed (status=%r channel=%s thread_ts=%s)",
                status,
                channel,
                thread_ts,
                exc_info=True,
            )

    # ── Slack web client management ──────────────────────────────────

    @staticmethod
    def _default_web_client_factory(app: SlackApp) -> AsyncWebClient:
        from slack_sdk.web.async_client import AsyncWebClient

        return AsyncWebClient(token=app.bot_token)

    def _get_client(self, app: SlackApp) -> AsyncWebClient:
        client = self._web_clients.get(app.slug)
        if client is None:
            client = self._web_client_factory(app)
            self._web_clients[app.slug] = client
        return client

    async def _resolve_bot_id(self, app: SlackApp, slack: AsyncWebClient) -> str | None:
        """Return this app's own Slack ``bot_id`` (via ``auth.test``).

        Cached per app slug. Used by :meth:`_fetch_thread_history` to tell
        *our* prior replies apart from other bots/webhooks in the thread —
        only messages from this bot id are mapped to assistant history.
        Best-effort: on failure we cache ``None`` so foreign bot messages
        are treated as external context rather than our own turns.
        """
        if app.slug in self._bot_ids:
            return self._bot_ids[app.slug]
        bot_id: str | None = None
        try:
            resp = await slack.auth_test()
            bot_id = resp.get("bot_id") or None
        except Exception as exc:
            logger.debug("auth.test failed for app %s: %s", app.slug, exc)
        self._bot_ids[app.slug] = bot_id
        return bot_id
