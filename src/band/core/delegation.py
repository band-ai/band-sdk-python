"""Typed view of the platform's cross-owner identity envelope (INT-992).

The platform mints ``metadata["delegation"]`` server-side on cross-owner
messages so handler and tool code can see *who is asking*. The LLM must never
see it: ``band.runtime.formatters.format_message_for_llm`` strips exactly this
key at the history seam, and both ``PlatformMessage.format_for_llm``
implementations render ``[name]: content`` only.

Import-light on purpose (pydantic + logging): consumed by the wire models in
``band.client.streaming``, the preprocessor, and ``BandLink`` without dragging
runtime modules into ``band.core``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class Originator(BaseModel):
    """Who minted the delegated request. Platform-authored; read-only."""

    model_config = ConfigDict(extra="allow", frozen=True)

    uuid: str | None = None
    handle: str | None = None
    display_name: str | None = None


class DelegationEnvelope(BaseModel):
    """The identity envelope the platform mints on ``metadata["delegation"]``.

    Every field is optional on purpose: the SDK must keep parsing envelopes
    from newer platforms (``extra="allow"`` absorbs added keys; ``version != 1``
    still parses), and a partially formed envelope is more useful than a
    dropped one. Frozen because this is a read-only view — handler/tool code
    consumes it, nothing in the SDK writes through it.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    version: int | None = None
    originator: Originator | None = None
    message_id: str | None = None
    minted_at: str | None = None
    # Reserved by the platform for delegation chains (M2.5); ``None`` today.
    hop: Any | None = None


def parse_delegation_value(value: Any) -> DelegationEnvelope | None:
    """Tolerantly coerce a raw ``delegation`` value into an envelope.

    Accepts an already-parsed :class:`DelegationEnvelope`, a dict in the
    platform's minted shape, or anything else (malformed → ``None``). Never
    raises: a platform bug must not take down the agent loop.
    """
    if value is None:
        return None
    if isinstance(value, DelegationEnvelope):
        return value
    try:
        return DelegationEnvelope.model_validate(value)
    except Exception:
        logger.debug("Ignoring malformed delegation envelope: %r", value)
        return None


def parse_delegation(metadata: Any) -> DelegationEnvelope | None:
    """Extract and parse the identity envelope off message ``metadata``.

    ``metadata`` may be a plain dict (REST / Fern models dump to dicts), a
    pydantic model such as ``MessageMetadata`` (the WebSocket wire type), or
    ``None`` — the same dict-or-model split that
    ``ExecutionContext._metadata_to_dict`` normalizes. Returns ``None`` when
    the envelope is absent or malformed; never raises.
    """
    if metadata is None:
        return None
    try:
        if isinstance(metadata, dict):
            raw = metadata.get("delegation")
        else:
            # Covers typed fields and pydantic extra="allow" attributes alike.
            raw = getattr(metadata, "delegation", None)
    except Exception:
        # Defensive: exotic metadata objects (broken __getattr__) must not
        # take down preprocessing.
        logger.debug("Could not read delegation off metadata: %r", metadata)
        return None
    return parse_delegation_value(raw)


__all__ = [
    "DelegationEnvelope",
    "Originator",
    "parse_delegation",
    "parse_delegation_value",
]
