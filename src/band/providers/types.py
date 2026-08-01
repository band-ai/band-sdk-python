"""Typed provider option vocabularies (static ``Unpack`` + runtime allow-lists)."""

from __future__ import annotations

from typing import TypedDict


class AnthropicProviderOptions(TypedDict, total=False):
    """Anthropic request-payload knobs beyond canonical sampling fields."""

    top_p: float
    top_k: int
    stop_sequences: list[str]
    metadata: dict[str, str]


class GeminiProviderOptions(TypedDict, total=False):
    """Gemini ``GenerateContentConfig`` knobs beyond canonical sampling fields."""

    top_p: float
    top_k: int
    stop_sequences: list[str]
    candidate_count: int
    presence_penalty: float
    frequency_penalty: float


ANTHROPIC_PROVIDER_OPTION_KEYS: frozenset[str] = frozenset(
    AnthropicProviderOptions.__annotations__
)
GEMINI_PROVIDER_OPTION_KEYS: frozenset[str] = frozenset(
    GeminiProviderOptions.__annotations__
)

# Provider-owned credential aliases must never be accepted as request payload.
ANTHROPIC_RAW_OPTIONS_RESERVED: frozenset[str] = frozenset({"anthropic_api_key"})
GEMINI_RAW_OPTIONS_RESERVED: frozenset[str] = frozenset(
    {"gemini_api_key", "google_api_key"}
)
