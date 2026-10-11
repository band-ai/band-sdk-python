"""Bounded control observations from the installed transport's existing logs."""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypedDict

from band_sdk_core import AgentTopicKind
from phoenix_channels_python_client.phx_messages import PHXEvent

from band.client.streaming.client import WireEvent

logger = logging.getLogger(__name__)

CONTROL_FIELDS = (
    "mode",
    "scope",
    "type",
    "agent_id",
    "room_id",
    "execution_id",
    "correlation_id",
)
OBSERVATION_LIMIT = 64
FIELD_LIMIT = 160
RAW_FRAME_LIMIT = 65536
TRANSPORT_LOGGERS = (
    "phoenix_channels_python_client.protocol_handler.ProtocolHandler",
    "phoenix_channels_python_client.client",
)


def safe_control_fields(payload: Mapping[str, object]) -> dict[str, str | None]:
    """Keep only bounded control identifiers and vocabulary, excluding free text."""
    fields: dict[str, str | None] = {}
    for key in CONTROL_FIELDS:
        value = payload.get(key)
        if isinstance(value, str):
            fields[key] = value[:FIELD_LIMIT]
        elif value is None and key in payload:
            fields[key] = None
    return fields


class TransportSnapshot(TypedDict):
    observations: list[dict[str, object]]
    dropped_observations: int
    oversized_frames: int


class ControlTransportDiagnostics:
    def __init__(self, agent_id: str) -> None:
        self.topic = AgentTopicKind.Control.topic(agent_id)
        self.observations: deque[dict[str, object]] = deque(maxlen=OBSERVATION_LIMIT)
        self.dropped_observations = 0
        self.oversized_frames = 0

    def snapshot(self) -> TransportSnapshot:
        return {
            "observations": list(self.observations),
            "dropped_observations": self.dropped_observations,
            "oversized_frames": self.oversized_frames,
        }

    @property
    def control_correlations(self) -> list[str]:
        return [
            correlation
            for observation in self.observations
            if observation.get("event") == WireEvent.AGENT_CONTROL
            and isinstance(correlation := observation.get("correlation_id"), str)
        ]

    @property
    def dropped_join_refs(self) -> list[str | None]:
        return [
            received
            for observation in self.observations
            if observation.get("reason") == "join_ref"
            and isinstance(received := observation.get("received"), (str, type(None)))
        ]

    @property
    def join_acknowledged(self) -> bool:
        return any(
            observation["boundary"] == "join_acknowledged"
            for observation in self.observations
        )

    def emit(self, record: logging.LogRecord) -> None:
        args = record.args
        if not isinstance(args, tuple):
            return
        observation = self._project(record.msg, args)
        if observation is None:
            return
        if len(self.observations) == OBSERVATION_LIMIT:
            self.dropped_observations += 1
        self.observations.append({"utc": datetime.now(UTC).isoformat(), **observation})

    def _project(
        self, message: object, args: tuple[Any, ...]
    ) -> dict[str, object] | None:
        match message:
            case "Parsing raw message: %s" if len(args) == 1:
                return self._frame(args[0])
            case "Starting websocket message loop for generation %s" if len(args) == 1:
                if isinstance(args[0], int):
                    return {"boundary": "socket_generation", "generation": args[0]}
            case "Successfully subscribed to topic %s" if args == (self.topic,):
                return {"boundary": "join_acknowledged", "topic": self.topic}
            case "Topic message processor cancelled for %s" if args == (self.topic,):
                return {"boundary": "topic_processor_cancelled", "topic": self.topic}
            case "Unregistered topic %s" if args == (self.topic,):
                return {"boundary": "topic_unregistered", "topic": self.topic}
            case (
                "Dropping message with stale join_ref on topic %s. got=%s expected=%s"
                | "Dropping stale queued message for topic %s. got=%s expected=%s"
            ) if len(args) == 3 and args[0] == self.topic:
                return self._drop("join_ref", args[1], args[2])
            case "Dropping message for stale generation on topic %s. routing_gen=%s subscription_gen=%s" if (
                len(args) == 3 and args[0] == self.topic
            ):
                return self._drop("generation", args[1], args[2])
            case (
                "Dropped %s queued messages for topic %s due to full queue"
                | "Drained %s queued messages for topic %s during reconnect"
            ) if len(args) == 2 and args[1] == self.topic:
                if isinstance(args[0], int):
                    return {
                        "boundary": "queue_drop",
                        "topic": self.topic,
                        "count": args[0],
                    }
        return None

    def _drop(
        self, reason: str, received: object, expected: object
    ) -> dict[str, object]:
        return {
            "boundary": "transport_drop",
            "topic": self.topic,
            "reason": reason,
            "received": self._scalar(received),
            "expected": self._scalar(expected),
        }

    @staticmethod
    def _scalar(value: object) -> str | int | None:
        if isinstance(value, str):
            return value[:FIELD_LIMIT]
        return value if isinstance(value, int) else None

    def _frame(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, (str, bytes)):
            return None
        if len(raw) > RAW_FRAME_LIMIT:
            self.oversized_frames += 1
            return None
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            return None
        if not isinstance(frame, list) or len(frame) != 5 or frame[2] != self.topic:
            return None
        join_ref, ref, topic, event, payload = frame
        if event not in (
            WireEvent.AGENT_CONTROL,
            PHXEvent.reply,
            PHXEvent.error,
            PHXEvent.close,
        ):
            return None
        observation: dict[str, object] = {
            "boundary": "socket_before_filter",
            "topic": topic,
            "event": event,
            "join_ref": self._scalar(join_ref),
            "ref": self._scalar(ref),
        }
        if isinstance(payload, dict):
            if event == WireEvent.AGENT_CONTROL:
                observation.update(safe_control_fields(payload))
            elif event == PHXEvent.reply and payload.get("status") in ("ok", "error"):
                observation["status"] = payload["status"]
        return observation


class TransportDiagnosticFilter(logging.Filter):
    def __init__(
        self, collector: ControlTransportDiagnostics, normal_level: int
    ) -> None:
        super().__init__()
        self.collector = collector
        self.normal_level = normal_level

    def filter(self, record: logging.LogRecord) -> bool:
        self.collector.emit(record)
        # Enabling the raw boundary must not expose any transport debug payload.
        return record.levelno > logging.DEBUG and record.levelno >= self.normal_level


@contextmanager
def collect_control_transport(
    agent_id: str, *, enabled: bool
) -> Iterator[ControlTransportDiagnostics]:
    """Observe safe facts before suppressing raw debug records; preserve normal logs."""
    collector = ControlTransportDiagnostics(agent_id)
    if not enabled:
        yield collector
        return
    states = []
    for name in TRANSPORT_LOGGERS:
        transport_logger = logging.getLogger(name)
        states.append(
            (transport_logger, transport_logger.level, transport_logger.filters)
        )
        gate = TransportDiagnosticFilter(
            collector, transport_logger.getEffectiveLevel()
        )
        transport_logger.filters = [gate, *transport_logger.filters]
        transport_logger.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        for transport_logger, level, filters in states:
            transport_logger.filters = filters
            transport_logger.setLevel(level)
        logger.warning(
            "Control transport observations: %s",
            json.dumps(collector.snapshot(), sort_keys=True),
        )
