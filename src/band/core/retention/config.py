"""Retention settings an adapter config carries."""

from __future__ import annotations

from pydantic import Field

from band.core.adapterconfig import BaseAdapterConfig


class RoomRetentionConfig(BaseAdapterConfig):
    """Automatic suspension is opt-in and requires a qualified provider.

    ``transition_timeout_s`` bounds how long a caller waits to learn the result
    of one owned acquisition or cleanup; an unfinished one stays owned.
    """

    idle_timeout_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    transition_timeout_s: float = Field(default=10.0, gt=0, allow_inf_nan=False)


def require_disabled_retention(
    retention: RoomRetentionConfig, *, reason: str
) -> RoomRetentionConfig:
    if retention.idle_timeout_s is not None:
        raise ValueError(f"{reason} Leave retention.idle_timeout_s unset.")
    return retention
