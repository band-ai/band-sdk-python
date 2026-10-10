"""Message-lifecycle REST operations — no WebSocket state involved.

Lifecycle acceptance is checked against actual HTTP status, independently
of WebSocket connection or subscription state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TypeVar

from band_rest.core.api_error import ApiError
from band_rest.core.http_response import AsyncHttpResponse
from band_rest.types.chat_message import ChatMessage

from band.client.rest import DEFAULT_REQUEST_OPTIONS, AsyncRestClient
from band.core.exceptions import RoomExecutionStoppedError
from band.core.types import metadata_to_dict
from band.runtime.types import PlatformMessage

logger = logging.getLogger(__name__)


ResponseData = TypeVar("ResponseData")


def _accepted_response(
    response: AsyncHttpResponse[ResponseData | None],
    *,
    stopped_room_id: str | None = None,
) -> ResponseData:
    if response.status_code == HTTPStatus.NO_CONTENT and stopped_room_id is not None:
        raise RoomExecutionStoppedError(stopped_room_id)
    data = response.data
    payload = getattr(data, "data", None)
    if (
        response.status_code != HTTPStatus.OK
        or data is None
        or payload is None
        or getattr(payload, "success", True) is False
    ):
        raise ApiError(
            status_code=response.status_code,
            headers=dict(response.headers),
            body="Lifecycle response did not confirm acceptance",
        )
    return data


def _platform_message(item: ChatMessage, room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=item.id,
        room_id=item.chat_room_id or room_id,
        content=item.content,
        sender_id=item.sender_id,
        sender_type=item.sender_type,
        sender_name=item.sender_name or "",
        message_type=item.message_type,
        metadata=metadata_to_dict(item.metadata, exclude_none=True),
        created_at=item.inserted_at or datetime.now(UTC),
    )


class MessageLifecycle:
    """Message mark/report/fetch operations for one agent's REST client.

    ``rest`` is taken per call, not cached at construction: the caller
    (``BandLink``) owns the REST client and may swap it at any point, so
    every call here uses whatever ``rest`` the caller currently has rather
    than a snapshot from construction time.
    """

    def __init__(self) -> None:
        # Debounces activity-report warnings: keep-alive runs every few
        # seconds, so a down endpoint would otherwise flood the log.
        self._activity_report_failing = False

    async def mark_processing(
        self, rest: AsyncRestClient, room_id: str, message_id: str
    ) -> bool:
        """
        Mark message as being processed on the server.

        This does NOT remove it from /next: the actionable set excludes only
        'processed', so a crashed or stopped attempt stays replayable. Only
        mark_processed clears the message from /next.
        """
        logger.debug("Marking message %s as processing", message_id)
        try:
            response = await rest.agent_api_messages.with_raw_response.mark_agent_message_processing(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            _accepted_response(response, stopped_room_id=room_id)
        except RoomExecutionStoppedError:
            raise
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            logger.warning("Failed to mark message %s as processing: %s", message_id, e)
            return False
        return True

    async def mark_processed(
        self, rest: AsyncRestClient, room_id: str, message_id: str
    ) -> bool:
        """
        Mark message as successfully processed on the server.

        Clears the message from unprocessed queue.
        """
        logger.debug("Marking message %s as processed", message_id)
        try:
            response = await rest.agent_api_messages.with_raw_response.mark_agent_message_processed(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            _accepted_response(response, stopped_room_id=room_id)
        except RoomExecutionStoppedError:
            raise
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            logger.warning("Failed to mark message %s as processed: %s", message_id, e)
            return False
        return True

    async def mark_failed(
        self, rest: AsyncRestClient, room_id: str, message_id: str, error: str
    ) -> bool:
        """
        Mark message as failed on the server.

        Records the error and may trigger retry logic on the server side.
        """
        error = error.strip() or "Unknown error"
        logger.warning("Marking message %s as failed: %s", message_id, error)
        try:
            response = await rest.agent_api_messages.with_raw_response.mark_agent_message_failed(
                chat_id=room_id,
                id=message_id,
                error=error,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            _accepted_response(response, stopped_room_id=room_id)
        except RoomExecutionStoppedError:
            raise
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            logger.warning("Failed to mark message %s as failed: %s", message_id, e)
            return False
        return True

    async def report_activity(
        self,
        rest: AsyncRestClient,
        room_id: str,
        working: bool,
        *,
        timeout_seconds: int = 2,
    ) -> bool:
        """
        Report the agent's boolean working state for a room's execution.

        ``working=True`` while a reasoning cycle is active (refreshed on a
        keep-alive cadence), ``False`` once it ends. Never raises — failures
        are swallowed and returned as ``False``, since the platform's TTL is
        the backstop and activity reporting must never break message
        processing.

        ``timeout_seconds`` bounds each POST so a slow/half-open endpoint
        can't stall the keep-alive or wedge teardown. Retries are off: a
        dropped keep-alive gets re-sent next cadence tick, and a dropped
        ``false`` is cleared by the platform TTL either way — retrying would
        only add latency.
        """
        try:
            await rest.agent_api_activity.report_agent_chat_activity(
                chat_id=room_id,
                working=working,
                request_options={
                    "timeout_in_seconds": timeout_seconds,
                    "max_retries": 0,
                },
            )
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            if not self._activity_report_failing:
                self._activity_report_failing = True
                logger.warning(
                    "Failed to report activity (working=%s) for room %s: %s; "
                    "suppressing repeat warnings until recovery",
                    working,
                    room_id,
                    e,
                )
            else:
                logger.debug(
                    "Activity report still failing (working=%s) for room %s: %s",
                    working,
                    room_id,
                    e,
                )
            return False
        if self._activity_report_failing:
            self._activity_report_failing = False
            logger.info("Activity reporting recovered for room %s", room_id)
        return True

    async def get_next_message(
        self, rest: AsyncRestClient, room_id: str
    ) -> PlatformMessage | None:
        """
        Get the next actionable message for a room from the server.

        Returns ``None`` only when the platform reports 204 (nothing
        pending) — never to mean "the call failed."

        Raises:
            ApiError: non-204 REST failure.
            Exception: transport-level failure (connection error, timeout).

        Callers that want to swallow transient failures must wrap this call
        themselves: conflating "no pending" with "lookup failed" used to
        silently drop messages at the claim step.
        """
        logger.debug("Getting next message for room %s", room_id)
        response = (
            await rest.agent_api_messages.with_raw_response.get_agent_next_message(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        )
        if response.status_code == HTTPStatus.NO_CONTENT:
            return None
        return _platform_message(_accepted_response(response).data, room_id)

    async def get_stale_processing_messages(
        self, rest: AsyncRestClient, room_id: str
    ) -> list[PlatformMessage]:
        """
        Get messages stuck in 'processing' state for a room.

        Diagnostic listing only. Execution recovery uses /next, which includes
        processing messages and applies the platform's stopped-room gate.
        """
        try:
            messages = []
            page = 1
            while True:
                response = await rest.agent_api_messages.list_agent_messages(
                    chat_id=room_id,
                    status="processing",
                    page=page,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                for item in response.data:
                    messages.append(_platform_message(item, room_id))

                total_pages = response.metadata.total_pages
                if total_pages is None or page >= total_pages:
                    break
                page += 1

            return messages
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            logger.warning(
                "Failed to get stale processing messages for room %s: %s",
                room_id,
                e,
            )
            return []
