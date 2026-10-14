"""Agent connection and room subscription policy."""

from __future__ import annotations

from dataclasses import dataclass

from band.core.types import ConflictPolicy


@dataclass
class AgentConfig:
    """Configuration for agent runtime."""

    auto_subscribe_existing_rooms: bool = True
    # Refuse to start when another process on this host already runs the
    # same agent id: duplicates steal each other's in-flight room messages
    # (the recovery sweep has no liveness check) and stateful adapters
    # resume the same on-disk sessions, splitting one conversation.
    single_instance: bool = True
    # Platform-side duplicate guard for the initial WebSocket connect (cross-host,
    # cross-TMPDIR); complements ``single_instance`` on this host. See
    # ``ConflictPolicy`` for semantics and limits.
    conflict_policy: ConflictPolicy = ConflictPolicy.SUPERSEDE
