"""Message-lifecycle REST operations — no WebSocket state involved.

Lifecycle acceptance is checked against actual HTTP status, independently
of WebSocket connection or subscription state.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, TypeVar

from band_rest.agent_api_activity.types.report_agent_chat_activity_response import (
    ReportAgentChatActivityResponse,
)
from band_rest.core.api_error import ApiError
from band_rest.core.http_response import AsyncHttpResponse
from band_rest.core.request_options import RequestOptions
from band_rest.types.agent_activity_detail import AgentActivityDetail
from band_rest.types.agent_activity_response import AgentActivityResponse
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
from band.platform.activity import activity_support
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

# A tool name over this length is a 422 on the activity endpoint, which the
# compatibility path would read as an old platform; clip first, never reject.
MAX_ACTIVITY_NAME_CHARACTERS = 1024


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


def _first_rejected_field(body: object) -> str:
    """A content-free cause: the first JSON pointer a validation error names."""
    envelope = _validated(ErrorEnvelope, body)
    if envelope is None:
        return "unparsed"
    return next(iter(envelope.error.details), "unnamed")


async def _post_activity(
    rest: AsyncRestClient,
    room_id: str,
    working: bool,
    activity: AgentActivityDetail | None,
    timeout_seconds: int,
) -> AgentActivityResponse:
    detail = activity if activity_support.supported else None
    try:
        response = await _send_activity(rest, room_id, working, detail, timeout_seconds)
    except ApiError as error:
        if detail is None or error.status_code != HTTPStatus.UNPROCESSABLE_ENTITY:
            raise
        activity_support.disable(
            room_id=room_id, field=_first_rejected_field(error.body)
        )
        logger.debug("Activity 422 body for room %s: %r", room_id, error.body)
        response = await _send_activity(rest, room_id, working, None, timeout_seconds)
    activity_support.observe(response.headers)
    return response.data.data


async def _send_activity(
    rest: AsyncRestClient,
    room_id: str,
    working: bool,
    activity: AgentActivityDetail | None,
    timeout_seconds: int,
) -> AsyncHttpResponse[ReportAgentChatActivityResponse]:
    kwargs: dict[str, Any] = {}
    if activity is not None:
        if activity.name and len(activity.name) > MAX_ACTIVITY_NAME_CHARACTERS:
            # I-08: clip before the POST, never let a long name read as an
            # old platform (a detail-bearing 422 latches detail off).
            activity = activity.model_copy(
                update={"name": activity.name[:MAX_ACTIVITY_NAME_CHARACTERS]}
            )
        kwargs["activity"] = activity
    return await rest.agent_api_activity.with_raw_response.report_agent_chat_activity(
        chat_id=room_id,
        working=working,
        request_options={
            **NO_RETRY_REQUEST_OPTIONS,
            "timeout_in_seconds": timeout_seconds,
        },
        **kwargs,
    )


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
        activity: AgentActivityDetail | None = None,
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

        Detail is attached once a response reported a supporting agent API
        version. One deadline bounds the POST and its sole compatibility retry:
        a rejected detail permanently downgrades the process to boolean
        reports; other failures wait for the next keep-alive or platform TTL.

        A successful ``working=True`` report echoed as ``False`` identifies a
        stopped execution. Notify ``on_stopped`` separately from the request's
        success boolean; transport failures never imply STOP.
        """
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await _post_activity(
                    rest,
                    room_id,
                    working,
                    activity if working else None,
                    timeout_seconds,
                )
            stopped = working and response.working is False
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
