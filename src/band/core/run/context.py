"""Concrete ``RunContext`` for shim and native backends."""

from __future__ import annotations

from dataclasses import dataclass, field

from band.core.run.cancellation import NeverCancelled
from band.core.run.sink import RecordingEventSink
from band.core.protocols import AgentToolsProtocol, CancellationToken, EventSink


@dataclass
class ProviderModelContext:
    """The concrete ``ModelContext`` handed to ``ModelProvider.complete``."""

    cancellation: CancellationToken = field(default_factory=NeverCancelled)


@dataclass
class SimpleRunContext:
    """Concrete ``RunContext``."""

    tools: AgentToolsProtocol
    events: EventSink = field(default_factory=RecordingEventSink)
    cancellation: CancellationToken = field(default_factory=NeverCancelled)
