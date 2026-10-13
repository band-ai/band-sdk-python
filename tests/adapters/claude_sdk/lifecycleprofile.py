"""The Claude SDK adapter's bridge into the common Room lifecycle suite."""

from __future__ import annotations

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.protocols import TurnResultAlreadyReported
from band.core.retention import RetainedRoom
from band.integrations.claude_sdk import transport
from band.integrations.claude_sdk.session_manager import ClaudeRoomSession
from tests.adapters.claude_sdk.process import WorkspacePeer
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import (
    LifecycleBridge,
    LifecycleProfile,
    RetentionHost,
    pause_at,
)


class ClaudePeer(WorkspacePeer):
    """Inject a held start or a held close at the real transport."""

    starting: Hold | None = None
    closing: Hold | None = None

    async def connect(self) -> None:
        await super().connect()
        await pause_at(self.starting)

    async def close(self) -> None:
        await pause_at(self.closing)
        await super().close()


def install(host: RetentionHost, monkeypatch: pytest.MonkeyPatch) -> LifecycleBridge:
    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(
            approvals=ClaudeApprovalOptions(
                mode="auto_accept", text_notifications=False
            )
        ),
        workspace_for_room=host.workspace,
    )

    def child(*, prompt: str, options: ClaudeAgentOptions) -> ClaudePeer:
        peer = ClaudePeer(prompt=prompt, options=options)
        peer.arguments = host.peer_arguments
        host.spawned(peer, str(options.cwd) if options.cwd else None)
        return peer

    monkeypatch.setattr(transport, "SubprocessCLITransport", child)

    def session(room_id: str) -> ClaudeRoomSession | None:
        manager = adapter._session_manager
        return manager.session(room_id) if manager is not None else None

    def residency(room_id: str) -> RetainedRoom | None:
        return room.retained if (room := session(room_id)) is not None else None

    def native_id(room_id: str) -> str | None:
        return room.session_id if (room := session(room_id)) is not None else None

    return LifecycleBridge(adapter=adapter, residency=residency, native_id=native_id)


PROFILES = [
    LifecycleProfile(
        name="claude", install=install, lost_turn=TurnResultAlreadyReported
    )
]
