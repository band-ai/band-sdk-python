"""One-shot event handler for request/response hosting.

The SDK's normal flow (``Agent.run()``) assumes a long-lived process that holds
a Thenvoi WebSocket subscription. Some hosts can't run that shape — Bedrock
AgentCore Runtime, AWS Lambda, Cloud Run, etc. invoke a container per event and
discard it. For those, a sibling component (the bridge) holds the WS and
forwards events over HTTP.

:class:`OneShotInvoker` is the SDK-side counterpart: one forwarded event in,
one adapter execution out, no per-room state across calls. It mirrors what
:class:`thenvoi.runtime.execution.ExecutionContext` does in the long-running
path — self-filter, claim, hydrate, run, mark processed/failed, drain — but
without the asyncio queue + process-loop machinery that would fight a
request/response host.

Bridge envelope shape (see ``bridge_core.bridge.AgentRunner._serialize_event``)::

    {
      "event_type": "message_created" | "room_added" | ...,
      "agent_id": "<recipient agent id>",
      "room_id": "<chat room id or null>",
      "payload": {...},
      "raw": {...},
      "forwarded_at": "ISO-8601"
    }

Example usage::

    link = ThenvoiLink(agent_id=..., api_key=..., ws_url=..., rest_url=...)
    adapter = AnthropicAdapter(...)
    invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=...)

    await invoker.startup()
    try:
        result = await invoker.handle_event(forwarded_body)
    finally:
        await invoker.shutdown()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.core.protocols import FrameworkAdapter
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AgentInput, HistoryProvider, PlatformMessage
from thenvoi.platform.link import ThenvoiLink
from thenvoi.runtime._context_serialization import context_item_to_dict
from thenvoi.runtime.formatters import format_history_for_llm
from thenvoi.runtime.tools import AgentTools

logger = logging.getLogger(__name__)


# Defensive cap on the drain loop. The platform shouldn't backlog dozens of
# messages for a single agent in normal operation; if it does, surface it via
# ``drain_truncated`` rather than draining indefinitely.
DEFAULT_DRAIN_CAP = 50


class OneShotEnvelopeError(ValueError):
    """Raised when the forwarded event envelope is missing required fields."""


class OneShotInvoker:
    """Handles single-shot event invocations driven by a bridge.

    Owns the lifecycle dance (claim → run → mark processed → drain) so hosts
    can stay thin transports. The same in-band claim/process semantics the
    long-running ``Agent`` uses, reshaped for one-event-per-HTTP-call hosts.

    Args:
        link: A :class:`ThenvoiLink`. Only its REST client and message
            lifecycle markers are used; the WebSocket side is never connected.
        adapter: Framework adapter to run on each invocation. Must already be
            constructed; ``startup()`` calls ``on_started`` on it.
        agent_id: This container's Thenvoi agent identity (used for
            self-message filtering and the adapter's runtime identity).
        drain_cap: Defensive ceiling on the drain loop. Default 50.
    """

    def __init__(
        self,
        *,
        link: ThenvoiLink,
        adapter: FrameworkAdapter | SimpleAdapter,
        agent_id: str,
        drain_cap: int = DEFAULT_DRAIN_CAP,
    ) -> None:
        self._link = link
        self._adapter = adapter
        self._agent_id = agent_id
        self._drain_cap = drain_cap
        self._agent_name: str = ""
        self._agent_description: str = ""
        self._started = False

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def agent_description(self) -> str:
        return self._agent_description

    @property
    def link(self) -> ThenvoiLink:
        return self._link

    # --- Lifecycle ---

    async def startup(self) -> None:
        """Fetch agent metadata and prime the adapter.

        Mirrors the bootstrap half of ``Agent.start()``: sets the adapter's
        runtime agent id and calls ``on_started(name, description)``. Skips
        WebSocket connect and room subscriptions. Idempotent.
        """
        if self._started:
            return

        self._agent_name, self._agent_description = await self._fetch_agent_metadata()
        # Parity with Agent.start(): some adapters read this via getattr at
        # runtime to inject identity into the system prompt.
        setattr(self._adapter, "_thenvoi_agent_id", self._agent_id)
        await self._adapter.on_started(self._agent_name, self._agent_description)
        self._started = True
        logger.info(
            "OneShotInvoker ready: agent_id=%s name=%s",
            self._agent_id,
            self._agent_name,
        )

    async def shutdown(self) -> None:
        """Disconnect the link (best-effort)."""
        try:
            await self._link.disconnect()
        except Exception:
            logger.warning("Error during link disconnect", exc_info=True)

    # --- Event entry point ---

    async def handle_event(self, body: dict[str, Any]) -> dict[str, Any]:
        """Process one forwarded platform event from the bridge envelope.

        Non-message events return ``{"status": "ignored", ...}`` without side
        effects; in v1 only ``message_created`` drives an LLM call.

        Raises:
            OneShotEnvelopeError: envelope is missing ``room_id`` or
                ``payload.id`` for a ``message_created`` event.
            RuntimeError: ``startup()`` was not called first.
        """
        if not self._started:
            raise RuntimeError("OneShotInvoker.startup() not called")

        event_type = body.get("event_type")

        # Long-running containers keep one invoker (and one adapter) alive
        # across many rooms over the container's lifetime. Adapters cache
        # per-room state on ``self`` (e.g. Anthropic's ``_message_history``,
        # Claude SDK's live per-room sessions, langgraph checkpoints); the
        # only thing that frees those entries is ``adapter.on_cleanup``.
        # Without this hookup the cache grows unbounded — and for adapters
        # that spawn subprocesses per room, those subprocesses leak too.
        # Mirrors ``AgentRuntime._destroy_execution``'s cleanup-callback hook
        # in the long-running path.
        if event_type in {"room_removed", "room_deleted"}:
            room_id = body.get("room_id") or (body.get("payload") or {}).get("id")
            if room_id:
                try:
                    await self._adapter.on_cleanup(room_id)
                except Exception:
                    logger.warning(
                        "Adapter on_cleanup failed for room %s",
                        room_id,
                        exc_info=True,
                    )
            return {
                "status": "cleaned_up",
                "event_type": event_type,
                "room_id": room_id,
            }

        # Other forwardable event types intentionally fall through to
        # "ignored":
        #   - room_added: bridge already subscribed the WS; no per-room
        #     context to create on this side.
        #   - participant_added/removed: OneShot fetches participants fresh
        #     on every invocation, so there's no cache to update.
        #   - contact_*: routed via the separate ContactEventConfig flow in
        #     long-running mode; not wired into OneShot.
        if event_type != "message_created":
            logger.debug("Ignoring non-message event: %s", event_type)
            return {"status": "ignored", "event_type": event_type}

        payload = body.get("payload") or {}
        room_id = body.get("room_id") or payload.get("chat_room_id")
        if not room_id:
            raise OneShotEnvelopeError("missing room_id")
        if not payload.get("id"):
            raise OneShotEnvelopeError("missing message id in payload")

        return await self._process_message_event(room_id=room_id, payload=payload)

    # --- Internal: the lifecycle dance ---

    async def _process_message_event(
        self, *, room_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the SDK agent loop for one forwarded message_created event.

        Steps (the message case of ``ExecutionContext._process_event``,
        adapted for request/response):

        1. Self-filter — skip the agent's own echo without an LLM call.
        2. ``get_next_message`` — if the triggering message isn't the next
           open one for this agent, exit early (a sibling invocation already
           claimed it, or there's an older unprocessed message ahead of it).
        3. ``mark_processing`` — claim it.
        4. Fetch participants + history, build ``AgentInput``, run adapter.
        5. ``mark_processed`` on success.
        6. Drain — only swallow messages the LLM actually saw (``seen_ids``).
           A message that arrived after the history snapshot is left open so
           the next invocation handles it with fresh context.
        7. ``mark_failed`` on exception.
        """
        msg_id = payload["id"]

        # 1. Self-message filter — Thenvoi echoes the agent's own outbound
        # messages back on its WS subscription, which the bridge forwards here.
        if (
            payload.get("sender_type") == "Agent"
            and payload.get("sender_id") == self._agent_id
        ):
            return {"status": "skipped_self", "message_id": msg_id}

        # 2. Verify the triggering message is the next open one for this agent.
        # The platform's ``/next`` returns the oldest actionable message —
        # anything not yet in ``processed`` state, including ones stuck in
        # ``processing`` from a previous crash — so a single call covers both
        # the normal claim case and stuck-message reclaim.
        next_msg = await self._link.get_next_message(room_id)
        if next_msg is None:
            logger.info(
                "Skip: room %s has no pending messages (triggering=%s)",
                room_id,
                msg_id,
            )
            return {"status": "no_pending", "message_id": msg_id}
        if next_msg.id != msg_id:
            logger.info(
                "Skip: room %s next-open=%s != triggering=%s",
                room_id,
                next_msg.id,
                msg_id,
            )
            return {
                "status": "already_processed",
                "message_id": msg_id,
                "next_open": next_msg.id,
            }

        # 3. Claim.
        logger.info("Claiming msg %s in room %s", msg_id, room_id)
        await self._link.mark_processing(room_id, msg_id)

        try:
            # 4. Build AgentInput and run the adapter.
            participants = await self._fetch_participants(room_id)
            sender_name = _lookup_sender_name(participants, payload.get("sender_id"))

            msg = _build_platform_message(payload, room_id, sender_name)
            history, seen_ids = await self._fetch_history(
                room_id,
                exclude_message_id=msg.id,
                participants=participants,
            )
            # The triggering message is always something the LLM "saw".
            seen_ids.add(msg_id)

            tools = AgentTools(
                room_id=room_id, rest=self._link.rest, participants=participants
            )

            inp = AgentInput(
                msg=msg,
                tools=tools,
                history=HistoryProvider(raw=history),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=room_id,
            )

            await self._adapter.on_event(inp)

            # 5. Mark the triggering message processed.
            await self._link.mark_processed(room_id, msg_id)
        except Exception as exc:
            # 7. Mark failed so the platform can surface the error.
            logger.exception(
                "Adapter failed for message %s in room %s", msg_id, room_id
            )
            try:
                await self._link.mark_failed(room_id, msg_id, str(exc)[:500] or "error")
            except Exception:
                logger.warning(
                    "Could not mark %s failed in room %s",
                    msg_id,
                    room_id,
                    exc_info=True,
                )
            raise

        # 6. Drain — scoped to what the LLM saw (seen_ids). A message that
        # arrived after the history snapshot is NOT swallowed; it's left open
        # so the next invocation processes it with fresh context.
        drained: list[str] = []
        drain_truncated = False
        for _ in range(self._drain_cap):
            try:
                stale = await self._link.get_next_message(room_id)
            except Exception:
                # The triggering message is already marked processed; a
                # transient ``/next`` failure mid-drain just stops this drain
                # cycle. The next invocation re-fetches via ``/next``.
                logger.warning(
                    "Drain /next failed in room %s — stopping drain",
                    room_id,
                    exc_info=True,
                )
                break
            if stale is None:
                break
            # Defensive: the platform shouldn't return our own messages here,
            # but the SDK guards against it (execution.py self-message skip).
            if stale.sender_type == "Agent" and stale.sender_id == self._agent_id:
                continue
            if stale.id not in seen_ids:
                logger.info(
                    "Drain stopped at %s in room %s — arrived after history snapshot",
                    stale.id,
                    room_id,
                )
                break
            await self._link.mark_processing(room_id, stale.id)
            await self._link.mark_processed(room_id, stale.id)
            drained.append(stale.id)
        else:
            drain_truncated = True
            logger.warning(
                "Hit drain cap (%d) for room %s — leaving remaining messages open",
                self._drain_cap,
                room_id,
            )
        if drained:
            logger.info(
                "Drained %d stale messages in room %s: %s",
                len(drained),
                room_id,
                drained,
            )

        result: dict[str, Any] = {
            "status": "done",
            "room_id": room_id,
            "message_id": msg_id,
        }
        if drained:
            result["drained"] = drained
        if drain_truncated:
            result["drain_truncated"] = True
        return result

    # --- REST helpers ---

    async def _fetch_agent_metadata(self) -> tuple[str, str]:
        response = await self._link.rest.agent_api_identity.get_agent_me(
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to fetch agent metadata from Thenvoi")
        agent = response.data
        return agent.name, agent.description or ""

    async def _fetch_participants(self, room_id: str) -> list[dict[str, Any]]:
        try:
            response = await self._link.rest.agent_api_participants.list_agent_chat_participants(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception:
            logger.warning(
                "Failed to fetch participants for room %s", room_id, exc_info=True
            )
            return []
        if not response.data:
            return []
        return [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "handle": getattr(p, "handle", None),
            }
            for p in response.data
        ]

    async def _fetch_history(
        self,
        room_id: str,
        *,
        exclude_message_id: str | None,
        participants: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Fetch room history formatted for the LLM, plus the set of message
        ids the LLM will see. The id set scopes the drain loop so it never
        swallows a message that arrived after this snapshot.
        """
        try:
            response = await self._link.rest.agent_api_context.get_agent_chat_context(
                chat_id=room_id,
                page=1,
                page_size=50,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception:
            logger.warning(
                "Failed to fetch history for room %s", room_id, exc_info=True
            )
            return [], set()
        items = response.data or []
        seen_ids = {item.id for item in items if getattr(item, "id", None)}
        raw_messages = [context_item_to_dict(item) for item in items]
        history = (
            format_history_for_llm(
                raw_messages,
                exclude_id=exclude_message_id,
                participants=participants,
            )
            or []
        )
        return history, seen_ids


# --- Module-level helpers (no state, easy to unit-test) ---


def _lookup_sender_name(
    participants: list[dict[str, Any]], sender_id: str | None
) -> str | None:
    if not sender_id:
        return None
    for p in participants:
        if p.get("id") == sender_id:
            return p.get("name")
    return None


def _parse_inserted_at(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _build_platform_message(
    payload: dict[str, Any],
    room_id: str,
    sender_name: str | None,
) -> PlatformMessage:
    return PlatformMessage(
        id=payload["id"],
        room_id=room_id,
        content=payload.get("content", ""),
        sender_id=payload.get("sender_id", ""),
        sender_type=payload.get("sender_type", "User"),
        sender_name=sender_name,
        message_type=payload.get("message_type", "user"),
        metadata=payload.get("metadata"),
        created_at=_parse_inserted_at(payload.get("inserted_at")),
    )
