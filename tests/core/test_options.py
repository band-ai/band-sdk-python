"""Phase 2 sampling / raw_options resolution."""

from __future__ import annotations

import pytest

from band.core.contracts.model import ModelSamplingOptions
from band.core.exceptions import UnsupportedOptionError
from band.core.options import (
    UNSET,
    merge_raw_options,
    reject_removed_kwargs,
    reject_unknown_options,
    resolve_sampling,
)


def test_unset_sentinel_distinct_from_none() -> None:
    assert UNSET is not None
    assert repr(UNSET) == "UNSET"


def test_resolve_sampling_precedence() -> None:
    provider = ModelSamplingOptions(temperature=0.1, max_output_tokens=100)
    instance = ModelSamplingOptions(temperature=0.5, max_output_tokens=None)
    request = ModelSamplingOptions(temperature=None, max_output_tokens=50)

    resolved = resolve_sampling(
        instance=instance, request=request, provider_defaults=provider
    )
    assert (
        resolved.temperature == 0.5
    )  # instance wins over provider; request fell through
    assert resolved.max_output_tokens == 50  # request wins


def test_resolve_sampling_request_overrides_instance() -> None:
    resolved = resolve_sampling(
        instance=ModelSamplingOptions(temperature=0.2, max_output_tokens=10),
        request=ModelSamplingOptions(temperature=0.9, max_output_tokens=20),
    )
    assert resolved.temperature == 0.9
    assert resolved.max_output_tokens == 20


def test_reject_unknown_provider_options() -> None:
    with pytest.raises(UnsupportedOptionError, match="Unknown"):
        reject_unknown_options({"nope": 1}, allowed=frozenset({"top_p"}))


def test_raw_options_reserved_keys() -> None:
    with pytest.raises(UnsupportedOptionError, match="reserved"):
        merge_raw_options({"api_key": "secret"}, explicit={})


def test_raw_options_collision_with_explicit() -> None:
    with pytest.raises(TypeError, match="collides"):
        merge_raw_options(
            {"temperature": 0.1},
            explicit={"temperature": 0.5},
        )


def test_raw_options_allowed_when_named_omitted() -> None:
    merged = merge_raw_options(
        {"top_p": 0.8},
        explicit={"temperature": UNSET, "top_p": UNSET},
    )
    assert merged == {"top_p": 0.8}


def test_removed_system_prompt_guidance_has_one_assignment() -> None:
    with pytest.raises(TypeError) as exc_info:
        reject_removed_kwargs(["system_prompt"])

    assert str(exc_info.value) == (
        "system_prompt= was removed; use "
        "instructions=Instruction(text=..., mode=InstructionMode.REPLACE) instead"
    )
