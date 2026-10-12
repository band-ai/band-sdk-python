"""Parity of Python's turn-effect vocabulary with band-sdk-core's.

``TurnEffect`` stays a local ``StrEnum`` because it is public API
(``@declares_turn_effect(TurnEffect.ACT)``), and core's classes are opaque PyO3
types. These tests keep the two equivalent.
"""

from __future__ import annotations

import band_sdk_core
import pytest

from band.runtime.tools import ALL_TOOL_NAMES, TurnEffect, turn_effect

CORE_EFFECTS = band_sdk_core.band_tool_effects()


def test_effect_values_match_core() -> None:
    core_values = {
        member.wire_name
        for name in dir(band_sdk_core.TurnEffect)
        if isinstance(
            member := getattr(band_sdk_core.TurnEffect, name), band_sdk_core.TurnEffect
        )
    }
    assert {effect.value for effect in TurnEffect} == core_values


def test_band_tools_match_core() -> None:
    assert ALL_TOOL_NAMES == set(CORE_EFFECTS)


@pytest.mark.parametrize("tool_name", sorted(CORE_EFFECTS))
def test_turn_effect_matches_core(tool_name: str) -> None:
    assert turn_effect(tool_name) == CORE_EFFECTS[tool_name].wire_name


@pytest.mark.parametrize("effect", list(TurnEffect))
def test_did_work_matches_core_verdict(effect: TurnEffect) -> None:
    ledger = band_sdk_core.TurnLedger()
    ledger.record(band_sdk_core.TurnEffect.from_wire_name(effect.value))
    assert effect.did_work == (ledger.verdict() == band_sdk_core.TurnVerdict.Complete)
