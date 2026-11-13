"""Adapter for an agent whose work is done by an external worker.

The worker is any program that reads its messages and replies through the
Band Agent REST API itself: an HTTP-triggered routine, a cron job, a process
started by hand. This adapter keeps the agent online, nudges the worker on each
turn, and holds the turn open (so the room shows the agent working) until the
platform reports the message processed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Self

import httpx
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import SettingsConfigDict
from typing_extensions import Unpack

from band.client.rest import AsyncRestClient
from band.client.streaming import DeliveryStatus
from band.core.adapterconfig import EnvAdapterConfig
from band.core.exceptions import BandConfigError, ProviderRunError
from band.core.protocols import FAILURE_CODE_TIMEOUT, AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import FeatureKwargs, PlatformMessage, delivery_status_for_agent
from band.platform.message_lifecycle import MessageLifecycle

logger = logging.getLogger(__name__)

# The body type a platform wake-up webhook carries, so a worker already
# written for wake-ups handles this adapter's trigger unchanged.
WAKEUP_EVENT_TYPE: Final = "agent.wakeup"
TRIGGER_HTTP_TIMEOUT_SECONDS: Final = 30.0
WORKER_FAILED_CODE: Final = "external_worker_failed"
WORKER_UNREACHABLE_CODE: Final = "external_worker_unreachable"


@dataclass(frozen=True)
class WorkRequest:
    """The message an external worker is asked to handle."""

    agent_id: str
    room_id: str
    message_id: str

    def wakeup_body(self) -> dict[str, Any]:
        """Return the JSON body of a platform wake-up, extended with the message."""
        return {
            "type": WAKEUP_EVENT_TYPE,
            "agent_id": self.agent_id,
            "room_id": self.room_id,
            "message_id": self.message_id,
        }


Trigger = Callable[[WorkRequest], Awaitable[None]]


class ExternalWorkerAdapterConfig(EnvAdapterConfig):
    """Settings for nudging an external worker and waiting for it.

    Every field can also come from a ``BAND_EXTERNAL_WORKER_``-prefixed
    environment variable (e.g. ``BAND_EXTERNAL_WORKER_TRIGGER_URL``).

    Attributes:
        trigger_url: Where the built-in HTTP trigger POSTs each work request.
            Leave unset when passing a custom ``trigger`` to the adapter.
        trigger_token: Sent as ``Authorization: Bearer <token>`` with each POST.
        timeout_seconds: One budget for the whole turn: the trigger call and
            the wait for the worker together. When it runs out the turn fails
            and the platform offers the message again later.
        poll_interval_seconds: How often to ask the platform whether the
            worker has marked the message processed.
    """

    model_config = SettingsConfigDict(env_prefix="BAND_EXTERNAL_WORKER_")

    trigger_url: str | None = None
    trigger_token: SecretStr | None = None
    timeout_seconds: float = Field(default=300.0, gt=0)
    poll_interval_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _token_needs_url(self) -> Self:
        if self.trigger_token is not None and self.trigger_url is None:
            raise ValueError("trigger_token is set but trigger_url is not")
        return self


class HttpTrigger:
    """Nudge a worker by POSTing a wake-up-shaped request to ``url``."""

    def __init__(self, url: str, token: SecretStr | None) -> None:
        self._url = url
        self._token = token

    async def __call__(self, request: WorkRequest) -> None:
        """POST ``request``; a non-2xx answer or a transport error raises
        ``ProviderRunError``, whose code is safe to show in the room."""
        try:
            async with httpx.AsyncClient(
                timeout=TRIGGER_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    self._url, json=request.wakeup_body(), headers=self._headers()
                )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderRunError(WORKER_UNREACHABLE_CODE, str(e)) from e

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}


class ExternalWorkerAdapter(SimpleAdapter[Any]):
    """Keep an agent online while an external worker answers its messages.

    For each message the adapter calls ``trigger`` (by default a POST to
    ``config.trigger_url``), then waits until the platform no longer lists the
    message as awaiting processing. The worker posts the reply and marks the
    message processed itself, so this adapter never replies and never judges
    the turn. The turn fails at once if the worker marks the message failed,
    and otherwise once ``config.timeout_seconds`` (shared by the trigger call
    and the wait) runs out; either way the platform offers the message again
    later.

    The worker does not need to know what woke it: a worker that scans its
    rooms for the next unprocessed message works the same whether started by
    this adapter, a platform wake-up webhook, or a schedule.
    """

    def __init__(
        self,
        config: ExternalWorkerAdapterConfig | None = None,
        *,
        trigger: Trigger | None = None,
        rest_client: AsyncRestClient | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        super().__init__(**features)
        self.config = config or ExternalWorkerAdapterConfig()
        self._trigger = trigger or self._http_trigger(self.config)
        self._rest = rest_client
        self._messages = MessageLifecycle()

    @staticmethod
    def _http_trigger(config: ExternalWorkerAdapterConfig) -> Trigger:
        if config.trigger_url is None:
            raise BandConfigError(
                "ExternalWorkerAdapter needs config.trigger_url or a trigger"
            )
        return HttpTrigger(config.trigger_url, config.trigger_token)

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build the REST client that watches message status, unless one was given."""
        await super().on_started(agent_name, agent_description)
        if self._rest is None:
            self._rest = self.build_rest_client()

    @property
    def judges_turns(self) -> bool:
        """The worker replies, so a turn with no reply from here is expected."""
        return False

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
        """Nudge the worker, then wait until it has processed ``msg``."""
        agent_id = self.require_platform().agent_id
        request = WorkRequest(agent_id, room_id, msg.id)
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                await self._trigger(request)
                await self._wait_until_processed(agent_id, room_id, msg.id)
        except TimeoutError as e:
            logger.warning(
                "External worker left message %s in room %s unprocessed for %ss",
                msg.id,
                room_id,
                self.config.timeout_seconds,
            )
            raise ProviderRunError(FAILURE_CODE_TIMEOUT) from e

    async def _wait_until_processed(
        self, agent_id: str, room_id: str, message_id: str
    ) -> None:
        # The room's whole outstanding list, not /messages/next: /next serves
        # the oldest outstanding message, so an older stuck or failed one
        # would hide whether this one is still pending.
        rest = self.require_rest_client(self._rest)
        while pending := await self._messages.get_actionable_message(
            rest, room_id, message_id
        ):
            # The runtime marked this message processing before the turn, so
            # "failed" now can only be the worker giving up on it.
            if (
                delivery_status_for_agent(pending.metadata, agent_id)
                == DeliveryStatus.FAILED
            ):
                raise ProviderRunError(WORKER_FAILED_CODE)
            await asyncio.sleep(self.config.poll_interval_seconds)
