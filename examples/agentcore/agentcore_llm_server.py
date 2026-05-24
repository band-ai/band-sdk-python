# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "thenvoi-sdk[anthropic]",
#   "fastapi>=0.110",
#   "uvicorn>=0.29",
# ]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""AgentCore container that runs the Thenvoi SDK per invocation.

The bridge (dumb pipe) forwards raw Thenvoi WS events to this container over
HTTP. On each POST /invocations the container:

1. Reconstructs a typed :class:`PlatformMessage` from the forwarded event.
2. Fetches participants and recent history via Thenvoi REST.
3. Builds an :class:`AgentInput` and calls ``adapter.on_event(inp)``.
4. The adapter runs its LLM tool loop; tools call back to Thenvoi REST
   (send_message, add_participant, etc.) under this container's identity.

Each invocation is one-shot — no per-room state is kept across calls.
History comes fresh from Thenvoi each time.

Environment variables:
    THENVOI_AGENT_ID — agent's Thenvoi identity (required)
    THENVOI_API_KEY  — Thenvoi REST API key (required)
    ANTHROPIC_API_KEY — Anthropic API key for the LLM loop (required)
    THENVOI_WS_URL   — defaults to wss://app.thenvoi.com/api/v1/socket/websocket
                       (unused by the container; reserved for SDK consistency)
    THENVOI_REST_URL — defaults to https://app.thenvoi.com
    ANTHROPIC_MODEL  — defaults to claude-sonnet-4-5-20250929
    SYSTEM_PROMPT    — optional custom system prompt for the adapter
    EMIT_EXECUTION   — "true" (default) emits tool_call/tool_result as platform
                       events; set "false" to silence them
    PORT             — defaults to 8080 (AgentCore Runtime contract)

Run locally::

    THENVOI_AGENT_ID=... THENVOI_API_KEY=... ANTHROPIC_API_KEY=... \\
        uv run python examples/agentcore/agentcore_llm_server.py
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from thenvoi.adapters.anthropic import AnthropicAdapter
from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.core.types import (
    AdapterFeatures,
    AgentInput,
    Emit,
    HistoryProvider,
    PlatformMessage,
)
from thenvoi.platform.link import ThenvoiLink
from thenvoi.runtime.formatters import format_history_for_llm
from thenvoi.runtime.tools import AgentTools

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ValueError(f"{name} environment variable is required")
    return value


async def _fetch_agent_metadata(link: ThenvoiLink) -> tuple[str, str]:
    """Fetch the agent's display name and description from Thenvoi."""
    response = await link.rest.agent_api_identity.get_agent_me(
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    if not response.data:
        raise RuntimeError("Failed to fetch agent metadata from Thenvoi")
    agent = response.data
    description = agent.description or ""
    return agent.name, description


async def _fetch_participants(link: ThenvoiLink, room_id: str) -> list[dict[str, Any]]:
    """Fetch participants for a room. Empty list on error."""
    try:
        response = await link.rest.agent_api_participants.list_agent_chat_participants(
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
    link: ThenvoiLink,
    room_id: str,
    *,
    exclude_message_id: str | None,
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fetch room history formatted for the LLM."""
    from thenvoi.runtime._context_serialization import context_item_to_dict

    try:
        response = await link.rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id,
            page=1,
            page_size=50,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
    except Exception:
        logger.warning("Failed to fetch history for room %s", room_id, exc_info=True)
        return []
    raw_messages = [context_item_to_dict(item) for item in (response.data or [])]
    return (
        format_history_for_llm(
            raw_messages,
            exclude_id=exclude_message_id,
            participants=participants,
        )
        or []
    )


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
    """Reconstruct a typed PlatformMessage from the forwarded event payload."""
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


def _build_adapter(anthropic_api_key: str) -> AnthropicAdapter:
    """Build the AnthropicAdapter from env config.

    Enables ``Emit.EXECUTION`` by default so every tool_call and tool_result
    is posted to the room as a platform event (visible in the Band UI).
    Set ``EMIT_EXECUTION=false`` to disable.
    """
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    system_prompt = os.environ.get("SYSTEM_PROMPT")
    emit_execution = os.environ.get("EMIT_EXECUTION", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    emit: frozenset[Emit] = (
        frozenset({Emit.EXECUTION}) if emit_execution else frozenset()
    )
    features = AdapterFeatures(emit=emit)
    return AnthropicAdapter(
        model=model,
        api_key=anthropic_api_key,
        prompt=system_prompt,
        features=features,
    )


# Defensive cap on the drain loop. The platform shouldn't backlog dozens
# of messages for a single agent in normal operation; if it does, log and
# bail out rather than draining indefinitely.
_DRAIN_MAX = 50


async def _process_message_event(
    body: dict[str, Any],
    *,
    link: ThenvoiLink,
    adapter: AnthropicAdapter,
    own_agent_id: str,
) -> dict[str, Any]:
    """Run the SDK agent loop for one forwarded message_created event.

    Honors the platform's lifecycle markers (``mark_processing`` /
    ``mark_processed`` / ``mark_failed``) so concurrent invocations across
    ``(agent, room)`` don't duplicate work — mirroring what the SDK's
    ``ExecutionContext`` does in the long-running Agent flow.

    Flow:
    1. Self-filter (skip the agent's own echo).
    2. ``get_next_message(room_id)`` to verify the triggering message is the
       earliest unprocessed message for this agent. If it isn't (either
       already processed by a sibling invocation that drained, or eclipsed
       by an older un-processed message that should be handled first),
       return early without invoking the LLM.
    3. ``mark_processing(triggering)`` to claim it.
    4. Fetch participants + history, build AgentInput, run the adapter.
    5. ``mark_processed(triggering)``.
    6. Drain the room: ``get_next_message`` until None, marking each as
       processed. Rationale: the LLM had visibility into the full room
       history during its turn; whatever was unanswered at the time is now
       this agent's responsibility, whether the LLM replied or chose to
       ignore. Subsequent invocations should not re-run the LLM on those.
    7. On error: ``mark_failed(triggering)``.
    """
    payload = body.get("payload") or {}
    room_id = body.get("room_id") or payload.get("chat_room_id")
    if not room_id:
        raise HTTPException(status_code=400, detail="missing room_id")
    if not payload.get("id"):
        raise HTTPException(status_code=400, detail="missing message id in payload")

    msg_id = payload["id"]

    # 1. Self-message filter: Thenvoi delivers the agent's own outbound
    # messages back on its WS subscription. The SDK filters these in normal
    # operation; we replicate the check so we don't burn an LLM call on
    # our own echo.
    if (
        payload.get("sender_type") == "Agent"
        and payload.get("sender_id") == own_agent_id
    ):
        return {"status": "skipped_self", "message_id": msg_id}

    # 2. Verify the triggering message is the next open one for this agent.
    # If a sibling invocation already drained it, or there's an older
    # unprocessed message ahead of it, skip the LLM call.
    next_msg = await link.get_next_message(room_id)
    if next_msg is None:
        logger.info(
            "Skip: room %s has no pending messages (triggering=%s)", room_id, msg_id
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

    # 3. Claim the message.
    logger.info("Claiming msg %s in room %s", msg_id, room_id)
    await link.mark_processing(room_id, msg_id)

    try:
        # 4. Build AgentInput and run the adapter.
        participants = await _fetch_participants(link, room_id)
        sender_name = _lookup_sender_name(participants, payload.get("sender_id"))

        msg = _build_platform_message(payload, room_id, sender_name)
        history = await _fetch_history(
            link,
            room_id,
            exclude_message_id=msg.id,
            participants=participants,
        )
        tools = AgentTools(room_id=room_id, rest=link.rest, participants=participants)

        inp = AgentInput(
            msg=msg,
            tools=tools,
            history=HistoryProvider(raw=history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=room_id,
        )

        await adapter.on_event(inp)

        # 5. Mark the triggering message processed.
        await link.mark_processed(room_id, msg_id)
    except Exception as exc:
        # 7. Mark failed so the platform can surface the error.
        logger.exception("Adapter failed for message %s in room %s", msg_id, room_id)
        try:
            await link.mark_failed(room_id, msg_id, str(exc)[:500] or "error")
        except Exception:
            logger.warning(
                "Could not mark %s failed in room %s", msg_id, room_id, exc_info=True
            )
        raise

    # 6. Drain any other open messages this LLM call had visibility into.
    # Each one needs the full processing → processed transition; the platform
    # may reject `mark_processed` on a message that was never marked
    # `processing` (the SDK's ExecutionContext always pairs the two).
    drained: list[str] = []
    for _ in range(_DRAIN_MAX):
        stale = await link.get_next_message(room_id)
        if stale is None:
            break
        await link.mark_processing(room_id, stale.id)
        await link.mark_processed(room_id, stale.id)
        drained.append(stale.id)
    else:
        logger.warning(
            "Hit drain cap (%d) for room %s — leaving remaining messages open",
            _DRAIN_MAX,
            room_id,
        )
    if drained:
        logger.info(
            "Drained %d stale messages in room %s: %s", len(drained), room_id, drained
        )

    result: dict[str, Any] = {
        "status": "done",
        "room_id": room_id,
        "message_id": msg_id,
    }
    if drained:
        result["drained"] = drained
    return result


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize link, fetch agent metadata, prime the adapter."""
    agent_id = _require_env("THENVOI_AGENT_ID")
    api_key = _require_env("THENVOI_API_KEY")
    anthropic_api_key = _require_env("ANTHROPIC_API_KEY")
    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")

    link = ThenvoiLink(
        agent_id=agent_id, api_key=api_key, ws_url=ws_url, rest_url=rest_url
    )

    agent_name, agent_description = await _fetch_agent_metadata(link)
    logger.info("Container ready: agent_id=%s name=%s", agent_id, agent_name)

    adapter = _build_adapter(anthropic_api_key)
    # The SDK uses this private attr for some internal checks; keep parity
    # with what Agent.start() does so adapter behaviour matches the SDK path.
    adapter._thenvoi_agent_id = agent_id  # type: ignore[attr-defined]
    await adapter.on_started(agent_name, agent_description)

    app.state.link = link
    app.state.adapter = adapter
    app.state.agent_id = agent_id

    try:
        yield
    finally:
        try:
            await link.disconnect()
        except Exception:
            logger.warning("Error during link disconnect", exc_info=True)


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping() -> dict[str, str]:
    """AgentCore Runtime health probe."""
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(request: Request) -> dict[str, Any]:
    """Process one forwarded event from the bridge.

    Body shape (from bridge_core.bridge.AgentRunner._serialize_event)::

        {
          "event_type": "message_created" | "room_added" | ...,
          "agent_id": "<recipient agent id>",
          "room_id": "<chat room id or null>",
          "payload": {...},   # Pydantic model_dump of the event payload
          "raw": {...},
          "forwarded_at": "ISO-8601"
        }
    """
    body = await request.json()
    event_type = body.get("event_type")

    if event_type != "message_created":
        # Non-message events are forwarded by the bridge for completeness
        # but the container has nothing to do with them in v1.
        logger.debug("Ignoring non-message event: %s", event_type)
        return {"status": "ignored", "event_type": event_type}

    link: ThenvoiLink = app.state.link
    adapter: AnthropicAdapter = app.state.adapter
    own_agent_id: str = app.state.agent_id

    try:
        return await _process_message_event(
            body, link=link, adapter=adapter, own_agent_id=own_agent_id
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("adapter execution failed")
        raise HTTPException(status_code=500, detail="adapter execution failed")


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
