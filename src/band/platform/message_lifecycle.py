"""Message-lifecycle REST operations — no WebSocket state involved.

Split out of BandLink: mark_processing/processed/failed, report_activity,
and the /next + stale-processing REST reads share nothing with WebSocket
connection or subscription state, only a REST client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from band.client.rest import AsyncRestClient, DEFAULT_REQUEST_OPTIONS
from band.runtime.types import PlatformMessage
from band_rest.core.api_error import ApiError

logger = logging.getLogger(__name__)


class MessageLifecycle:
    """Message mark/report/fetch operations for one agent's REST client.

    ``rest`` is taken per call, not cached at construction: the caller
    (``BandLink``) owns the REST client and may swap it at any point, so
    every call here uses whatever ``rest`` the caller currently has rather
    than a snapshot from construction time.
    """

    def __init__(self) -> None:
        # Debounce flag for activity-report failures: keep-alive runs at a few
        # seconds per room, so a down endpoint would otherwise flood the log on
        # every refresh. Log the first failure and the recovery, suppress repeats.
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
            await rest.agent_api_messages.mark_agent_message_processing(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
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
            await rest.agent_api_messages.mark_agent_message_processed(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
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
            await rest.agent_api_messages.mark_agent_message_failed(
                chat_id=room_id,
                id=message_id,
                error=error,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
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

        Sends ``working: true`` while a reasoning cycle is active (refreshed on a
        keep-alive cadence) and ``working: false`` when it ends. Failures are
        swallowed and returned as ``False`` — the platform's TTL is the backstop,
        so activity reporting must never break message processing.

        The call is time-bounded by ``timeout_seconds`` (a per-POST deadline, not
        the client default) so a slow/half-open endpoint can never wedge the
        reasoning loop's teardown or stall the keep-alive. Retries are disabled:
        a dropped keep-alive is re-sent on the next cadence tick, and a dropped
        ``false`` is cleared by the platform TTL, so retrying only adds latency.
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
        except Exception as e:
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

        Returns:
            ``PlatformMessage`` if there's an actionable message, or ``None``
            if the platform returned no content (204). ``None`` means *the
            platform told us there's nothing pending* — not "the call failed."

        Raises:
            ApiError: REST call failed with a non-204 status.
            Exception: Transport-level failure (connection error, timeout).
                Callers that want to swallow transient failures should wrap
                this call explicitly; the previous behavior of conflating
                "no pending" with "lookup failed" silently dropped messages
                at the claim step.
        """
        logger.debug("Getting next message for room %s", room_id)
        try:
            response = await rest.agent_api_messages.get_agent_next_message(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except ApiError as e:
            # 204 No Content means no actionable messages — the only "None"
            # case the platform expresses through an ApiError.
            if e.status_code == 204:
                logger.debug("No actionable messages for room %s", room_id)
                return None
            logger.warning("Failed to get next message: %s", e)
            raise

        if response is None or response.data is None:
            return None

        item = response.data
        return PlatformMessage(
            id=item.id,
            room_id=item.chat_room_id or room_id,
            content=item.content,
            sender_id=item.sender_id,
            sender_type=item.sender_type,
            sender_name=item.sender_name or "",
            message_type=item.message_type,
            metadata=item.metadata or {},
            created_at=item.inserted_at or datetime.now(timezone.utc),
        )

    async def get_stale_processing_messages(
        self, rest: AsyncRestClient, room_id: str
    ) -> list[PlatformMessage]:
        """
        Get messages stuck in 'processing' state for a room.

        On agent restart, messages that were being processed when the agent
        crashed remain in 'processing' state. The long-running runtime uses
        this as an explicit recovery sweep at startup.

        Note: ``get_next_message`` (the ``/next`` REST endpoint) already
        includes stuck-processing messages in its "actionable" result set —
        see ``Chat.get_next_actionable_message`` on the platform side, which
        excludes only ``processed``. Callers that drive recovery solely
        through ``/next`` (e.g. the bridge's rehydration nudge and
        ``OneShotInvoker``'s claim step) do not need to call this method;
        it exists for paths that want to drain *every* stuck message up
        front rather than one-per-room.

        Returns:
            List of PlatformMessage objects in processing state.
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
                    messages.append(
                        PlatformMessage(
                            id=item.id,
                            room_id=item.chat_room_id or room_id,
                            content=item.content,
                            sender_id=item.sender_id,
                            sender_type=item.sender_type,
                            sender_name=item.sender_name or "",
                            message_type=item.message_type,
                            metadata=item.metadata or {},
                            created_at=item.inserted_at or datetime.now(timezone.utc),
                        )
                    )

                total_pages = response.metadata.total_pages
                if total_pages is None or page >= total_pages:
                    break
                page += 1

            return messages
        except Exception as e:
            logger.warning(
                "Failed to get stale processing messages for room %s: %s",
                room_id,
                e,
            )
            return []
