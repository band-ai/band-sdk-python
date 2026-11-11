"""The external worker an adapter test nudges, and the turn it runs."""

from __future__ import annotations

from band.adapters.external_worker import (
    ExternalWorkerAdapter,
    ExternalWorkerAdapterConfig,
    WorkRequest,
)
from band.client.streaming import DeliveryStatus
from band.core.types import PlatformMessage
from band.runtime.tools.agent import AgentTools
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import ROOM_ID

ENV_PREFIX = ExternalWorkerAdapterConfig.model_config["env_prefix"]


class Worker:
    """A worker outside the SDK: it records each nudge and, when the test says
    so, marks the message processed on the platform as a real worker would."""

    def __init__(self, platform: RestPeer) -> None:
        self.requests: list[WorkRequest] = []
        self._platform = platform

    async def __call__(self, request: WorkRequest) -> None:
        self.requests.append(request)

    def finish(self, message_id: str) -> None:
        self._platform.set_status(message_id, DeliveryStatus.PROCESSED)


async def run_turn(
    adapter: ExternalWorkerAdapter, tools: AgentTools, message: PlatformMessage
) -> None:
    """Run one turn the way the runtime does, through the turn judge."""
    await adapter.run_judged_turn(
        msg=message,
        tools=tools,
        history=None,
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id=ROOM_ID,
    )
