"""Temporary content-free control tracer; patches are process-wide while active.

Usage: with ControlTrace() as trace: ...; trace.write("/tmp/control-trace.json")
No logger levels change. No file or network I/O happens in observation hooks.
Only allowlisted protocol/control metadata is retained, never frame bodies,
headers other than response request IDs, request URLs, chat text, or credentials.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import httpx
from phoenix_channels_python_client.protocol_handler import PHXProtocolHandler
from phoenix_channels_python_client.topic_runtime import TopicRuntimeMixin
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.connection import Connection

from band.client.streaming.client import WebSocketClient
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime

TOKEN = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
CONTROL_PATH = re.compile(r"/api/v1/me/chats/([0-9a-fA-F-]{36})/agents/(stop|play)\Z")
EVENTS = {
    "agent.control",
    "phx_error",
    "phx_close",
    "phx_reply",
    "phx_join",
    "phx_leave",
    "supersede",
    "heartbeat",
}


def word(value):
    return value.value if isinstance(value, Enum) else value


def token(value):
    value = word(value)
    if value is None:
        return None
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else "<invalid>"


def identifier(value):
    value = word(value)
    if value is None:
        return None
    return value if isinstance(value, str) and UUID.fullmatch(value) else "<non-uuid>"


def control(payload):
    def get(name):
        return (
            payload.get(name)
            if isinstance(payload, dict)
            else getattr(payload, name, None)
        )

    mode, scope = word(get("mode")), word(get("scope"))
    return {
        "mode": mode if mode in ("stop", "play", "interrupt") else "<invalid>",
        "scope": scope if scope in ("room", "agent") else "<invalid>",
        "agent_id": identifier(get("agent_id")),
        "room_id": identifier(get("room_id")),
        "execution_id": identifier(get("execution_id")),
        "correlation_id": token(get("correlation_id")),
    }


def envelope(topic, event, payload, join_ref=None, ref=None):
    event = word(event)
    if not isinstance(topic, str):
        return None
    family, _, ident = topic.partition(":")
    unknown = not isinstance(event, str) or event not in EVENTS
    if unknown and family != "agent_control":
        return None
    if family not in (
        "agent_control",
        "phoenix",
        "agent_rooms",
        "chat_room",
        "room_participants",
    ):
        return None
    # No chat payloads, even on protocol replies.
    result = {
        "topic": family,
        "topic_id": identifier(ident) if ident else None,
        "event": "<unknown>" if unknown else event,
        "join_ref": token(join_ref),
        "ref": token(ref),
    }
    if unknown:
        result["event_type"] = type(event).__name__
        if isinstance(event, str):
            result["event_sha256"] = hashlib.sha256(event.encode()).hexdigest()
    if event == "agent.control":
        result.update(control(payload))
        result["payload_type"] = type(payload).__name__
    elif isinstance(payload, dict):
        status = payload.get("status")
        if status in ("ok", "error"):
            result["status"] = status
        reason = payload.get("reason")
        if reason in ("channel_crash", "unmatched topic", "closed"):
            result["reason"] = reason
    return result


class ControlTrace:
    def __init__(self, *, max_records=50000):
        self.records = []
        self.dropped_records = 0
        self.max_records = max_records
        self._patches = []
        self._sockets = {}
        self._frame_counts = {}
        self._receive_context = ContextVar("control_trace_receive", default=None)
        self.links = {}
        self.runtimes = {}
        self._start = time.monotonic_ns()
        self.started_utc = datetime.now(UTC).isoformat()

    def connection(self, socket):
        if socket not in self._sockets:
            self._sockets[socket] = len(self._sockets) + 1
            self._frame_counts[self._sockets[socket]] = 0
            self.add(
                "socket.observed", connection=self._sockets[socket], socket=id(socket)
            )
        return self._sockets[socket]

    def remember(self, owner):
        if isinstance(owner, BandLink):
            self.links[id(owner)] = owner
        elif isinstance(owner, AgentRuntime):
            self.runtimes[id(owner)] = owner

    def snapshot(self):
        state = {"links": [], "runtimes": []}
        for owner, link in self.links.items():
            ws = link._ws
            try:
                topics = (
                    sorted(t.split(":", 1)[0] for t in ws.joined_topics()) if ws else []
                )
            except (RuntimeError, AttributeError):
                topics = []
            socket = None
            client_state = None
            try:
                client = ws._require_client() if ws else None
                socket = client.connection if client else None
                client_state = word(client._state) if client else None
            except (RuntimeError, AttributeError):
                client_state = "unavailable"
            socket_state = socket.state.name if socket else None
            state["links"].append(
                {
                    "owner": owner,
                    "agent_id": identifier(link.agent_id),
                    "connected": socket_state == "OPEN",
                    "socket_state": socket_state,
                    "client_state": client_state,
                    "connection": self.connection(socket) if socket else None,
                    "socket": id(socket) if socket else None,
                    "joined_topic_families": topics,
                }
            )
        for owner, runtime in self.runtimes.items():
            rooms = []
            for ctx in runtime.executions.values():
                task = getattr(ctx, "_active_cycle_task", None)
                rooms.append(
                    {
                        "room_id": identifier(ctx.room_id),
                        "stopped": getattr(ctx, "_stopped", None),
                        "queue_size": ctx.queue.qsize(),
                        "active_cycle": task is not None and not task.done(),
                    }
                )
            state["runtimes"].append({"owner": owner, "rooms": rooms})
        self.add("snapshot", **state)
        return state

    def add(self, stage, **fields):
        if len(self.records) >= self.max_records:
            self.dropped_records += 1
            return
        self.records.append(
            dict(t_ns=time.monotonic_ns() - self._start, stage=stage, **fields)
        )

    def _patch(self, cls, name, replacement):
        original = getattr(cls, name)
        owned = name in cls.__dict__
        self._patches.append((cls, name, original, owned))
        setattr(cls, name, replacement(original))

    def _async_stage(self, stage, metadata):
        def factory(original):
            @functools.wraps(original)
            async def wrapper(obj, *args, **kwargs):
                self.remember(obj)
                fields = metadata(obj, args, kwargs)
                self.add(stage + ".enter", **fields)
                try:
                    result = await original(obj, *args, **kwargs)
                except BaseException as exc:
                    self.add(stage + ".raise", exception=type(exc).__name__, **fields)
                    raise
                self.add(stage + ".exit", **fields)
                return result

            return wrapper

        return factory

    def __enter__(self):
        if self._patches:
            raise RuntimeError("Trace context already active")

        def receive(original):
            @functools.wraps(original)
            async def wrapped(socket, *args, **kwargs):
                if not isinstance(socket, ClientConnection):
                    return await original(socket, *args, **kwargs)
                connection = self.connection(socket)
                try:
                    raw = await original(socket, *args, **kwargs)
                except BaseException as exc:
                    self.add(
                        "socket.recv.raise",
                        socket=id(socket),
                        connection=connection,
                        exception=type(exc).__name__,
                        close_code=getattr(socket, "close_code", None),
                    )
                    raise
                self._frame_counts[connection] += 1
                sequence = self._frame_counts[connection]
                self._receive_context.set((connection, sequence))
                self.add(
                    "wire.frame",
                    connection=connection,
                    sequence=sequence,
                    size=len(raw),
                )
                # Read-only observation BEFORE Phoenix decoding and SDK validation.
                try:
                    frame = json.loads(raw)
                    if isinstance(frame, list) and len(frame) == 5:
                        join_ref, ref, topic, event, payload = frame
                    elif isinstance(frame, dict):
                        topic, event, payload = (
                            frame.get("topic"),
                            frame.get("event"),
                            frame.get("payload"),
                        )
                        join_ref, ref = frame.get("join_ref"), frame.get("ref")
                    else:
                        self.add(
                            "wire.invalid_envelope",
                            socket=id(socket),
                            connection=connection,
                            sequence=sequence,
                            sha256=hashlib.sha256(
                                raw.encode() if isinstance(raw, str) else raw
                            ).hexdigest(),
                        )
                        return raw
                    fields = envelope(topic, event, payload, join_ref, ref)
                    if fields is not None:
                        self.add(
                            "wire.receive",
                            socket=id(socket),
                            connection=connection,
                            sequence=sequence,
                            **fields,
                        )
                except (ValueError, TypeError, AttributeError):
                    self.add(
                        "wire.invalid_json",
                        socket=id(socket),
                        connection=connection,
                        sequence=sequence,
                        sha256=hashlib.sha256(
                            raw.encode() if isinstance(raw, str) else raw
                        ).hexdigest(),
                    )
                return raw

            return wrapped

        self._patch(Connection, "recv", receive)

        def decode(original):
            @functools.wraps(original)
            def wrapped(protocol, raw):
                current = self._receive_context.get()
                if current is None:
                    return original(protocol, raw)
                connection, sequence = current
                self.add(
                    "phoenix.decode.enter", connection=connection, sequence=sequence
                )
                try:
                    result = original(protocol, raw)
                except BaseException as exc:
                    self.add(
                        "phoenix.decode.raise",
                        connection=connection,
                        sequence=sequence,
                        exception=type(exc).__name__,
                    )
                    raise
                self.add(
                    "phoenix.decode.exit", connection=connection, sequence=sequence
                )
                return result

            return wrapped

        self._patch(PHXProtocolHandler, "parse_message", decode)

        def socket_send(original):
            @functools.wraps(original)
            async def wrapped(socket, message, *args, **kwargs):
                if isinstance(socket, ClientConnection) and isinstance(
                    message, (str, bytes)
                ):
                    try:
                        frame = json.loads(message)
                        if isinstance(frame, list) and len(frame) == 5:
                            join_ref, ref, topic, event, payload = frame
                            fields = envelope(topic, event, payload, join_ref, ref)
                            if fields is not None:
                                self.add(
                                    "wire.send",
                                    connection=self.connection(socket),
                                    socket=id(socket),
                                    **fields,
                                )
                    except (ValueError, TypeError, AttributeError):
                        pass
                return await original(socket, message, *args, **kwargs)

            return wrapped

        self._patch(Connection, "send", socket_send)

        def dispatch(original):
            @functools.wraps(original)
            async def wrapped(client, message, handlers):
                fields = envelope(
                    message.topic,
                    message.event,
                    message.payload,
                    message.join_ref,
                    message.ref,
                )
                if fields is None:
                    return await original(client, message, handlers)
                before = client._validation_error_count
                self.add("sdk.dispatch.enter", client=id(client), **fields)
                try:
                    return await original(client, message, handlers)
                except BaseException as exc:
                    self.add(
                        "sdk.dispatch.raise",
                        client=id(client),
                        exception=type(exc).__name__,
                        **fields,
                    )
                    raise
                finally:
                    self.add(
                        "sdk.dispatch.exit",
                        client=id(client),
                        validation_error_delta=client._validation_error_count - before,
                        **fields,
                    )

            return wrapped

        self._patch(WebSocketClient, "_handle_events", dispatch)

        def topic_dispatch(original):
            @functools.wraps(original)
            async def wrapped(client, topic, message):
                fields = envelope(
                    message.topic,
                    message.event,
                    message.payload,
                    message.join_ref,
                    message.ref,
                )
                if fields is not None:
                    self.add(
                        "phoenix.topic.dispatch",
                        client=id(client),
                        dropped=topic.dropped_message_count,
                        **fields,
                    )
                return await original(client, topic, message)

            return wrapped

        self._patch(TopicRuntimeMixin, "_handle_normal_message_mode", topic_dispatch)

        for cls, name, stage in (
            (BandLink, "_on_control", "link.control"),
            (AgentRuntime, "handle_control", "runtime.control"),
        ):
            self._patch(
                cls,
                name,
                self._async_stage(
                    stage, lambda obj, a, k: dict(owner=id(obj), **control(a[0]))
                ),
            )
        self._patch(
            ExecutionContext,
            "resume_room",
            self._async_stage(
                "execution.resume",
                lambda obj, a, k: {
                    "owner": id(obj),
                    "room_id": identifier(obj.room_id),
                    "stopped_before": obj._stopped,
                },
            ),
        )

        def stop(original):
            @functools.wraps(original)
            def wrapped(ctx, *args, **kwargs):
                self.add(
                    "execution.stop.enter",
                    owner=id(ctx),
                    room_id=identifier(ctx.room_id),
                    stopped_before=ctx._stopped,
                )
                result = original(ctx, *args, **kwargs)
                self.add(
                    "execution.stop.exit",
                    owner=id(ctx),
                    room_id=identifier(ctx.room_id),
                    stopped_after=ctx._stopped,
                )
                return result

            return wrapped

        self._patch(ExecutionContext, "stop_room", stop)
        for name in ("connect", "disconnect", "_on_reconnected", "_on_disconnected"):
            self._patch(
                BandLink,
                name,
                self._async_stage(
                    "link." + name,
                    lambda obj, a, k: {
                        "owner": id(obj),
                        "agent_id": identifier(obj.agent_id),
                    },
                ),
            )

        def send(original):
            @functools.wraps(original)
            async def wrapped(client, request, *args, **kwargs):
                match = CONTROL_PATH.fullmatch(request.url.path)
                if request.method != "POST" or match is None:
                    return await original(client, request, *args, **kwargs)
                fields = {
                    "request": id(request),
                    "room_id": identifier(match[1]),
                    "mode": match[2],
                }
                self.add("http.control.send", **fields)
                try:
                    response = await original(client, request, *args, **kwargs)
                except BaseException as exc:
                    self.add(
                        "http.control.raise", exception=type(exc).__name__, **fields
                    )
                    raise
                result = {
                    "status_code": response.status_code,
                    "request_id": token(response.headers.get("x-request-id")),
                    "correlation_id": token(response.headers.get("x-correlation-id")),
                }
                try:
                    data = response.json().get("data", {})
                    if isinstance(data, dict):
                        if type(data.get("affected")) is int:
                            result["affected"] = data["affected"]
                        if data.get("scope") in ("room", "agent"):
                            result["scope"] = data["scope"]
                except (ValueError, AttributeError, httpx.ResponseNotRead):
                    result["body_decoded"] = False
                self.add("http.control.response", **fields, **result)
                return response

            return wrapped

        self._patch(httpx.AsyncClient, "send", send)
        return self

    def __exit__(self, *exc):
        for cls, name, original, owned in reversed(self._patches):
            if owned:
                setattr(cls, name, original)
            else:
                delattr(cls, name)
        self._patches.clear()

    def write(self, path):
        Path(path).write_text(
            json.dumps(
                {
                    "started_utc": self.started_utc,
                    "dropped_records": self.dropped_records,
                    "records": self.records,
                },
                indent=2,
            )
            + "\n"
        )
