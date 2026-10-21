"""Message-lifecycle REST operations — no WebSocket state involved.

Lifecycle acceptance is checked against actual HTTP status, independently
of WebSocket connection or subscription state.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, TypeVar

from band_rest.core.api_error import ApiError
from band_rest.core.http_response import AsyncHttpResponse
from band_rest.core.request_options import RequestOptions
from band_rest.types.chat_message import ChatMessage
from band_rest.types.error import Error as PlatformError
from band_rest.types.validation_error import ValidationError as ErrorEnvelope
from pydantic import BaseModel
from pydantic import ValidationError as ModelValidationError

from band.client.rest import (
    DEFAULT_REQUEST_OPTIONS,
    NO_RETRY_REQUEST_OPTIONS,
    AsyncRestClient,
)
from band.client.streaming.client import DeliveryStatus
from band.core.exceptions import RoomExecutionStoppedError
from band.core.settlement import SettlementResult
from band.core.types import metadata_to_dict
from band.runtime.messages import PlatformMessage

logger = logging.getLogger(__name__)


ResponseData = TypeVar("ResponseData")
SchemaT = TypeVar("SchemaT", bound=BaseModel)

# The platform renders a terminal write against a message with no current
# processing attempt as HTTP 422 with this code and one detail under this
# JSON Pointer (the agent API's `ValidationError` schema).
VALIDATION_ERROR_CODE = "validation_error"
PROCESSING_ATTEMPT_POINTER = "/processing_attempt"
NOT_FOUND_ERROR_CODE = "not_found"


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


def _validated(schema: type[SchemaT], body: object) -> SchemaT | None:
    try:
        return schema.model_validate(body)
    except ModelValidationError:
        return None


def _names_missing_attempt(body: object) -> bool:
    envelope = _validated(ErrorEnvelope, body)
    return (
        envelope is not None
        and envelope.error.code == VALIDATION_ERROR_CODE
        and bool(envelope.error.details.get(PROCESSING_ATTEMPT_POINTER))
    )


def _names_missing_resource(body: object) -> bool:
    envelope = _validated(PlatformError, body)
    return envelope is not None and envelope.error.code == NOT_FOUND_ERROR_CODE


def _failure_result(error: Exception) -> SettlementResult:
    """Classify a failed terminal write from the public status and error body.

    ``ParsingError`` (a body that misses the generated schema) is
    UNCONFIRMED: the envelope checks below would reject that body too.
    """
    if not isinstance(error, ApiError):
        return SettlementResult.UNCONFIRMED
    match error.status_code:
        case HTTPStatus.NOT_FOUND if _names_missing_resource(error.body):
            return SettlementResult.NOT_FOUND
        case HTTPStatus.UNPROCESSABLE_ENTITY if _names_missing_attempt(error.body):
            return SettlementResult.REJECTED
        case _:
            return SettlementResult.UNCONFIRMED


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


ACTIONABLE_PAGE_SIZE = 100
WriteKey = tuple[str, DeliveryStatus]
FailureCause = tuple[type[Exception], int | None]
# Diagnostic suppression stays bounded; evicted intents warn again on retry.
UNCONFIRMED_WRITE_LIMIT = 256


@dataclass(frozen=True)
class ActionablePage:
    messages: list[PlatformMessage]
    next_cursor: str | None


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
        # Recent unconfirmed intents suppress repeat warnings without retaining
        # abandoned writes for the lifetime of the link.
        self._unconfirmed_writes: OrderedDict[WriteKey, FailureCause] = OrderedDict()

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
        """Mark message as processed, clearing it from the unprocessed queue."""
        result = await self.mark_processed_result(
            rest, room_id, message_id, request_options=DEFAULT_REQUEST_OPTIONS
        )
        return result is SettlementResult.ACCEPTED

    async def mark_failed(
        self, rest: AsyncRestClient, room_id: str, message_id: str, error: str
    ) -> bool:
        """Mark message as failed, recording the error for server-side retry logic."""
        result = await self.mark_failed_result(
            rest, room_id, message_id, error, request_options=DEFAULT_REQUEST_OPTIONS
        )
        return result is SettlementResult.ACCEPTED

    async def mark_processed_result(
        self,
        rest: AsyncRestClient,
        room_id: str,
        message_id: str,
        *,
        request_options: RequestOptions = NO_RETRY_REQUEST_OPTIONS,
    ) -> SettlementResult:
        """Write the processed mark and report what it established.

        One attempt by default: retrying a committed write whose response was
        lost reads back as a missing-attempt refusal.
        """
        logger.debug("Marking message %s as processed", message_id)
        return await self._settle(
            lambda: (
                rest.agent_api_messages.with_raw_response.mark_agent_message_processed(
                    chat_id=room_id,
                    id=message_id,
                    request_options=request_options,
                )
            ),
            room_id=room_id,
            message_id=message_id,
            action=DeliveryStatus.PROCESSED,
        )

    async def mark_failed_result(
        self,
        rest: AsyncRestClient,
        room_id: str,
        message_id: str,
        error: str,
        *,
        request_options: RequestOptions = NO_RETRY_REQUEST_OPTIONS,
    ) -> SettlementResult:
        """Write the failed mark and report what it established.

        One attempt by default, as ``mark_processed_result``.
        """
        error = error.strip() or "Unknown error"
        logger.debug("Marking message %s as failed: %s", message_id, error)
        return await self._settle(
            lambda: rest.agent_api_messages.with_raw_response.mark_agent_message_failed(
                chat_id=room_id,
                id=message_id,
                error=error,
                request_options=request_options,
            ),
            room_id=room_id,
            message_id=message_id,
            action=DeliveryStatus.FAILED,
        )

    async def _settle(
        self,
        write: Callable[[], Awaitable[AsyncHttpResponse[Any]]],
        *,
        room_id: str,
        message_id: str,
        action: DeliveryStatus,
    ) -> SettlementResult:
        try:
            _accepted_response(await write(), stopped_room_id=room_id)
        except RoomExecutionStoppedError:
            raise
        except Exception as e:  # noqa: BLE001 -- a failed write is an outcome, never a crash of the turn/link
            result = _failure_result(e)
            self._record_failed_write(message_id, action, result, e)
            return result
        self._unconfirmed_writes.pop((message_id, action), None)
        return SettlementResult.ACCEPTED

    def _record_failed_write(
        self,
        message_id: str,
        action: DeliveryStatus,
        result: SettlementResult,
        error: Exception,
    ) -> None:
        key = (message_id, action)
        cause = (type(error), getattr(error, "status_code", None))
        unconfirmed = result is SettlementResult.UNCONFIRMED
        repeated = unconfirmed and self._unconfirmed_writes.get(key) == cause
        level = logging.DEBUG if repeated else logging.WARNING
        logger.log(
            level,
            "Marking message %s as %s ended %s: %s",
            message_id,
            action,
            result,
            error,
        )
        if unconfirmed:
            self._remember_unconfirmed(key, cause)
        else:
            self._unconfirmed_writes.pop(key, None)

    def _remember_unconfirmed(self, key: WriteKey, cause: FailureCause) -> None:
        self._unconfirmed_writes[key] = cause
        self._unconfirmed_writes.move_to_end(key)
        if len(self._unconfirmed_writes) > UNCONFIRMED_WRITE_LIMIT:
            self._unconfirmed_writes.popitem(last=False)

    async def report_activity(
        self,
        rest: AsyncRestClient,
        room_id: str,
        working: bool,
        *,
        timeout_seconds: int = 2,
        on_stopped: Callable[[], None] | None = None,
    ) -> bool:
        """
        Report the agent's boolean working state for a room's execution.

        ``working=True`` while a reasoning cycle is active (refreshed on a
        keep-alive cadence), ``False`` once it ends. Request failures are
        swallowed and returned as ``False``, since the platform's TTL is
        the backstop and activity reporting must never break message
        processing.

        ``timeout_seconds`` bounds each POST so a slow/half-open endpoint
        can't stall the keep-alive or wedge teardown. Retries are off: a
        dropped keep-alive gets re-sent next cadence tick, and a dropped
        ``false`` is cleared by the platform TTL either way — retrying would
        only add latency.

        A successful ``working=True`` report echoed as ``False`` identifies a
        stopped execution. Notify ``on_stopped`` separately from the request's
        success boolean; transport failures never imply STOP.
        """
        try:
            response = await rest.agent_api_activity.report_agent_chat_activity(
                chat_id=room_id,
                working=working,
                request_options={
                    **NO_RETRY_REQUEST_OPTIONS,
                    "timeout_in_seconds": timeout_seconds,
                },
            )
            stopped = working and response.data.working is False
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
        if stopped and on_stopped is not None:
            on_stopped()
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

    async def get_actionable_page(
        self, rest: AsyncRestClient, room_id: str, *, cursor: str | None = None
    ) -> ActionablePage:
        """Read one chronological page of outstanding deliveries; failures propagate."""
        kwargs: dict[str, Any] = {"cursor": cursor} if cursor is not None else {}
        response = await rest.agent_api_messages.list_agent_messages(
            chat_id=room_id,
            sort_order="asc",
            limit=ACTIONABLE_PAGE_SIZE,
            request_options=DEFAULT_REQUEST_OPTIONS,
            **kwargs,
        )
        next_cursor = (
            response.metadata.next_cursor if response.metadata.has_more else None
        )
        if response.metadata.has_more and next_cursor is None:
            raise ValueError("Actionable messages page has_more without next_cursor")
        return ActionablePage(
            [_platform_message(item, room_id) for item in response.data], next_cursor
        )

    async def get_actionable_messages(
        self, rest: AsyncRestClient, room_id: str
    ) -> list[PlatformMessage]:
        """Collect a complete, deduplicated snapshot before delivery statuses change."""
        messages: dict[str, PlatformMessage] = {}
        page = 1
        pagination: dict[str, Any] = {}
        seen_cursors: set[str] = set()
        while True:
            response = await rest.agent_api_messages.list_agent_messages(
                chat_id=room_id,
                sort_order="asc",
                limit=ACTIONABLE_PAGE_SIZE,
                request_options=DEFAULT_REQUEST_OPTIONS,
                **pagination,
            )
            for item in response.data:
                messages.setdefault(item.id, _platform_message(item, room_id))

            cursor = response.metadata.next_cursor
            if response.metadata.has_more is True and cursor:
                if cursor in seen_cursors:
                    raise ValueError("Message listing has no usable pagination cursor")
                seen_cursors.add(cursor)
                pagination = {"cursor": cursor}
                continue

            total_pages = response.metadata.total_pages
            if (
                "cursor" not in pagination
                and total_pages is not None
                and page < total_pages
            ):
                page += 1
                pagination = {"page": page}
                continue

            if response.metadata.has_more is True:
                raise ValueError("Message listing has no usable pagination cursor")
            return list(messages.values())

    async def get_actionable_message(
        self, rest: AsyncRestClient, room_id: str, message_id: str
    ) -> PlatformMessage | None:
        cursor: str | None = None
        while True:
            page = await self.get_actionable_page(rest, room_id, cursor=cursor)
            for message in page.messages:
                if message.id == message_id:
                    return message
            if page.next_cursor is None:
                return None
            cursor = page.next_cursor

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
