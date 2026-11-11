"""The external worker an adapter test nudges, and the turn it runs."""

from __future__ import annotations

from band.adapters.external_worker import (
    ExternalWorkerAdapter,
    ExternalWorkerAdapterConfig,
    Trigger,
    WorkRequest,
)
from band.client.streaming import DeliveryStatus
from band.core.types import PlatformMessage
from band.runtime.tools.agent import AgentTools
from band.testing.platform import platform_connection_stub
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import AGENT_ID, ROOM_ID

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


class GivingUpWorker(Worker):
    """A worker that marks each message it is nudged about failed at once."""

    async def __call__(self, request: WorkRequest) -> None:
        await super().__call__(request)
        self._platform.set_status(request.message_id, DeliveryStatus.FAILED)


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


def connected_adapter(
    tools: AgentTools,
    config: ExternalWorkerAdapterConfig | None = None,
    *,
    trigger: Trigger | None = None,
) -> ExternalWorkerAdapter:
    """An adapter as the runtime leaves it after start: the agent's platform
    identity injected, and the REST client the room's tools use."""
    adapter = ExternalWorkerAdapter(config, trigger=trigger, rest_client=tools.rest)
    adapter.platform = platform_connection_stub(AGENT_ID)
    return adapter
