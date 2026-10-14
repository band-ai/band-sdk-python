"""Per-session execution settings and timing constraints."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Platform-side TTL (seconds) for the boolean working-state indicator. The
# adapter must refresh well within this window; if refreshes stop (crash, hang,
# disconnect) the platform clears the indicator. Mirrors the Ticket B contract
# (TTL <= 10s). Used only to derive the keep-alive cadence guard below.
PLATFORM_WORKING_STATE_TTL_SECONDS: float = 10.0


def _require_positive_when_set(name: str, value: float | None) -> None:
    """Shared guard for the optional-timeout fields below: unset (None) means
    unbounded and is always valid; a set value must be strictly positive."""
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be > 0 when set (got {value})")


PositiveFiniteSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SessionConfig(BaseSettings):
    """Execution settings; explicit values override case-insensitive environment values."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    enable_context_cache: bool = True
    context_cache_ttl_seconds: int = 300
    max_context_messages: int = 100
    # Max attempts per message before permanently failing. band_sdk_core.RetryTracker's
    # constructor validates this range itself -- no second Python-side check.
    max_message_retries: int = 1
    enable_context_hydration: bool = True  # Whether to fetch history from platform API
    # Base reconciliation interval, also used for retries and, unless
    # stopped_resync_seconds is set, for stopped-room probes.
    # Queue traffic cannot postpone a check; active handlers remain serial.
    idle_resync_seconds: PositiveFiniteSeconds = 60.0

    # --- Working-state (boolean "is the agent reasoning") reporting ---
    # Kill-switch: disable all working-state reporting instantly if needed.
    enable_working_state: bool = True
    # Keep-alive cadence: how often working:true is refreshed while a cycle runs.
    # Must be < TTL/2 so a single missed/slow ping still lands inside the TTL.
    working_keep_alive_seconds: float = 3.0
    # Per-POST deadline for each activity report (must be < cadence so a slow
    # POST can't pile onto the next keep-alive tick).
    working_request_timeout_seconds: int = 2
    # Optional upper bound on how long we keep asserting working:true for one
    # cycle. When exceeded we stop refreshing (platform TTL clears it); we never
    # cancel the reasoning. None = unbounded. NOT a hang-killer.
    max_working_state_seconds: float | None = None

    # Upper bound on one reasoning cycle (the handler invoked by _run_cycle).
    # Unlike max_working_state_seconds, exceeding this DOES cancel the cycle.
    # This covers the handler cycle, not context hydration or message claim/ack
    # calls. A handler stuck awaiting an external call (e.g. a wedged adapter
    # subprocess) would otherwise leave its message in 'processing' forever.
    # On expiry the cycle is cancelled and TimeoutError propagates through the
    # normal handler-exception path (mark_failed + retry), the same as any
    # other handler error. None = unbounded (default — matches prior behavior
    # for callers that never opt in).
    max_cycle_seconds: float | None = None

    # Post an `error` event when a message fails its final attempt and the
    # adapter didn't report it; otherwise the room can't tell it from a message
    # never received.
    report_turn_failures_to_room: bool = True

    # Empty checks double the interval up to max(base, maximum). Only the first
    # periodic check is spread within the base interval. Equal caps disable growth.
    idle_resync_max_seconds: PositiveFiniteSeconds = 120.0

    # Fixed interval for /next probes while a received STOP pauses the room.
    # These probes are the only way to discover a missed PLAY, so a longer
    # interval delays resuming by up to that long. None uses idle_resync_seconds.
    stopped_resync_seconds: PositiveFiniteSeconds | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        # Working-state invariants only matter when reporting is enabled.
        if self.enable_working_state:
            ttl_half = PLATFORM_WORKING_STATE_TTL_SECONDS / 2
            if self.working_keep_alive_seconds <= 0:
                raise ValueError(
                    f"working_keep_alive_seconds must be > 0 (got {self.working_keep_alive_seconds})"
                )
            if self.working_keep_alive_seconds >= ttl_half:
                raise ValueError(
                    f"working_keep_alive_seconds must be < TTL/2 ({ttl_half}) to keep TTL "
                    f"headroom (got {self.working_keep_alive_seconds})"
                )
            if self.working_request_timeout_seconds <= 0:
                raise ValueError(
                    f"working_request_timeout_seconds must be > 0 (got {self.working_request_timeout_seconds})"
                )
            if self.working_request_timeout_seconds >= self.working_keep_alive_seconds:
                raise ValueError(
                    f"working_request_timeout_seconds ({self.working_request_timeout_seconds}) must be < "
                    f"working_keep_alive_seconds ({self.working_keep_alive_seconds}) so a slow POST can't stack"
                )
            _require_positive_when_set(
                "max_working_state_seconds", self.max_working_state_seconds
            )

        _require_positive_when_set("max_cycle_seconds", self.max_cycle_seconds)
        return self
