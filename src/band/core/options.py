"""Sampling / provider option resolution (Phase 2).

Precedence: per-request override > per-instance default > provider default.

``UNSET`` means a provider constructor omitted this knob. Constructor ``None``
explicitly clears the named setting and blocks a colliding raw wire option;
neither value is sent as ``null`` for sampling. On ``ModelSamplingOptions``,
``None`` instead means a request field falls through to instance/default
sampling.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any, Final

from band.core.contracts.model import ModelSamplingOptions
from band.core.exceptions import UnsupportedOptionError


class _Unset:
    """Type-safe sentinel for an omitted constructor / kwargs field."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final[_Unset] = _Unset()
"""Constructor omission, distinct from an explicit ``None``."""


# Credentials, clients, and lifecycle must never arrive via raw_options.
RAW_OPTIONS_RESERVED: frozenset[str] = frozenset(
    {
        # The providers here always call non-streaming and read the whole
        # response; accepting a stream flag would silently do nothing.
        "stream",
        "api_key",
        "provider_key",
        "client",
        "http_client",
        "transport",
        "base_url",
        "auth_token",
        "access_token",
        "credentials",
        "model",  # identity — set on the provider, not per raw payload
    }
)


# v1 constructor spellings and what replaced them. One table: these adapters
# accept ``**provider_options``, so an un-rejected v1 kwarg is swallowed in
# silence, and a message that drifts between adapters sends the reader looking
# for a difference that is not there.
REMOVED_ADAPTER_KWARGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "api_key": "provider_key",
        "max_tokens": "max_output_tokens",
        "prompt": "instructions",
        "system_prompt": "instructions=Instruction(text=..., mode=InstructionMode.REPLACE)",
        "custom_section": "instructions",
        "enable_memory_tools": "features=AdapterFeatures(...)",
        "enable_execution_reporting": "features=AdapterFeatures(...)",
    }
)


def reject_removed_kwargs(
    supplied: Iterable[str], *, also: Mapping[str, str] | None = None
) -> None:
    """Raise ``TypeError`` naming the replacement for any v1 spelling.

    ``also`` carries an adapter's own aliases — its vendor-prefixed key.
    """
    removed = {**REMOVED_ADAPTER_KWARGS, **(also or {})}
    for name in supplied:
        replacement = removed.get(name)
        if replacement is not None:
            hint = replacement if "=" in replacement else f"{replacement}="
            raise TypeError(f"{name}= was removed; use {hint} instead")


def resolve_sampling(
    *,
    instance: ModelSamplingOptions | None,
    request: ModelSamplingOptions | None,
    provider_defaults: ModelSamplingOptions | None = None,
) -> ModelSamplingOptions:
    """Merge sampling with request > instance > provider-default precedence.

    Field-level: a request field that is ``None`` means "fall through" (see
    :class:`ModelSamplingOptions`). An instance/provider ``None`` means the
    field is omitted from the resolved result (provider may apply its own
    wire default).
    """
    base = provider_defaults or ModelSamplingOptions()
    inst = instance or ModelSamplingOptions()
    req = request or ModelSamplingOptions()

    temperature = (
        req.temperature
        if req.temperature is not None
        else inst.temperature
        if inst.temperature is not None
        else base.temperature
    )
    max_output_tokens = (
        req.max_output_tokens
        if req.max_output_tokens is not None
        else inst.max_output_tokens
        if inst.max_output_tokens is not None
        else base.max_output_tokens
    )
    return ModelSamplingOptions(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def reject_unknown_options(
    options: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    label: str = "provider_options",
) -> dict[str, Any]:
    """Copy ``options`` or raise :class:`UnsupportedOptionError` for unknown keys."""
    unknown = [key for key in options if key not in allowed]
    if unknown:
        bad = unknown[0]
        raise UnsupportedOptionError.with_suggestion(
            f"Unknown {label} key {bad!r}.",
            bad,
            allowed,
        )
    return dict(options)


def merge_raw_options(
    raw_options: Mapping[str, Any] | None,
    *,
    explicit: Mapping[str, Any],
    reserved_keys: frozenset[str] = RAW_OPTIONS_RESERVED,
) -> dict[str, Any]:
    """Merge request ``raw_options`` into a payload dict.

    - Reserved keys (credentials / client / transport) are rejected.
    - A raw key that collides with an **explicitly set** named option raises
      ``TypeError``.
    - A raw key whose named counterpart was omitted is allowed (escape hatch).

    ``explicit`` maps canonical option names to either ``UNSET`` (omitted) or
    the value that was set (including ``None`` when explicitly passed). Raw
    options are provider-native wire fields: a required provider default that
    is already in the payload is explicit and cannot be replaced through this
    escape hatch.
    """
    if not raw_options:
        return {}

    merged: dict[str, Any] = {}
    for key, value in raw_options.items():
        if key in reserved_keys:
            raise UnsupportedOptionError(
                f"raw_options must not set reserved key {key!r} "
                "(credentials, client, transport, and lifecycle are out of scope)"
            )
        if key in explicit and explicit[key] is not UNSET:
            raise TypeError(
                f"raw_options key {key!r} collides with an explicitly set named option"
            )
        merged[key] = value
    return merged


class ProviderOptionResolver:
    """Shared instance / request option bookkeeping for model providers.

    Owns the instance ``explicit`` map (including named aliases), validates
    ``provider_options`` / ``raw_options``, rebuilds the per-request explicit
    surface, and applies merged raw options through an injected ``applier`` so
    dict payloads (Anthropic) and ``setattr`` configs (Gemini) share one policy.
    """

    def __init__(
        self,
        *,
        aliases: Mapping[str, str] | None = None,
        reserved_keys: frozenset[str] = RAW_OPTIONS_RESERVED,
    ) -> None:
        # alias name -> canonical named-option key (e.g. max_tokens -> max_output_tokens)
        self._aliases = dict(aliases or {})
        self._reserved_keys = reserved_keys
        self._explicit: dict[str, Any] = {}
        self._provider_options: dict[str, Any] = {}
        self._raw_options: dict[str, Any] = {}

    @property
    def provider_options(self) -> Mapping[str, Any]:
        return self._provider_options

    @property
    def raw_options(self) -> Mapping[str, Any]:
        return self._raw_options

    def bind(
        self,
        *,
        named: Mapping[str, Any],
        provider_options: Mapping[str, Any],
        allowed: frozenset[str],
        raw_options: Mapping[str, Any] | None = None,
        label: str = "provider_options",
    ) -> None:
        """Record instance named options, reject unknown keys, validate raw_options."""
        explicit = dict(named)
        for alias, source in self._aliases.items():
            if source not in named:
                raise KeyError(
                    f"alias {alias!r} requires named option {source!r} in bind()"
                )
            explicit[alias] = named[source]
        self._explicit = explicit
        self._provider_options = reject_unknown_options(
            provider_options,
            allowed=allowed,
            label=label,
        )
        self._raw_options = dict(raw_options) if raw_options else {}
        # Instance raw_options validated once (no named collision at init).
        merge_raw_options(
            self._raw_options,
            explicit={},
            reserved_keys=self._reserved_keys,
        )

    def apply_raw(
        self,
        *,
        request_raw_options: Mapping[str, Any] | None,
        present: Mapping[str, Any],
        applier: Callable[[str, Any], None],
    ) -> None:
        """Merge instance + request ``raw_options`` onto the call target.

        ``present`` is the named surface already applied for this request
        (payload keys or config fields). Those keys are marked explicit so a
        colliding raw key raises; every non-colliding raw key is applied.

        A caller must therefore report *everything* it applied for this request,
        per-request sampling included — that is the only way the resolver learns
        a value the instance never set.
        """
        explicit = self._request_explicit(present)
        raw_merged = merge_raw_options(
            {
                **self._raw_options,
                **(dict(request_raw_options) if request_raw_options else {}),
            },
            explicit=explicit,
            reserved_keys=self._reserved_keys,
        )
        for key, value in raw_merged.items():
            applier(key, value)

    def _request_explicit(self, present: Mapping[str, Any]) -> dict[str, Any]:
        """Everything explicitly set for this request, by any route."""
        explicit = dict(self._explicit)
        for key, value in present.items():
            explicit[key] = value
        # Alias ↔ source are one collision surface (e.g. max_tokens / max_output_tokens).
        for alias, source in self._aliases.items():
            if alias in present:
                explicit[source] = present[alias]
            if source in present:
                explicit[alias] = present[source]
        for key, value in self._provider_options.items():
            explicit[key] = value
        return explicit
