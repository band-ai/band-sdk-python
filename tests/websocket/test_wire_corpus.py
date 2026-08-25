"""Proves `WirePayload.from_wire` against the vendored band-sdk-core corpus.

See `corpus.py`'s module docstring for provenance. Each case's
`canonical.normalized` (accept) or `canonical.issues` (reject) is the source
of truth.
"""

from __future__ import annotations

from typing import Any

import pytest
from band_sdk_core import EventType
from pydantic import BaseModel

from band.client.streaming.client import _PAYLOAD_MODELS
from tests.websocket.corpus import CORPUS_CASES


def assert_hydrated_matches(actual: Any, expected: Any) -> None:
    """Assert a hydrated payload's own attributes equal ``expected`` -- attribute
    equality against the real object, not a re-serialized dict, so a value
    pydantic silently coerced during hydration would be caught.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, BaseModel | dict), (
            f"expected a model or dict, got {actual!r}"
        )
        for key, expected_value in expected.items():
            actual_value = (
                getattr(actual, key) if isinstance(actual, BaseModel) else actual[key]
            )
            assert_hydrated_matches(actual_value, expected_value)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            assert_hydrated_matches(actual_item, expected_item)
    else:
        assert actual == expected


@pytest.mark.parametrize(
    "event_type,case",
    CORPUS_CASES,
    ids=[f"{event_type}:{case['name']}" for event_type, case in CORPUS_CASES],
)
def test_corpus_case(event_type: str, case: dict[str, Any]) -> None:
    model = _PAYLOAD_MODELS[event_type]
    canonical = case["canonical"]

    if canonical["decision"] == "accept":
        hydrated = model.from_wire(event_type, case["raw"])
        assert_hydrated_matches(hydrated, canonical["normalized"])
    else:
        with pytest.raises(ValueError) as exc_info:
            model.from_wire(event_type, case["raw"])
        actual_issues = {(path, code) for path, code, _msg in exc_info.value.issues}
        expected_issues = {
            (issue["path"], issue["code"]) for issue in canonical["issues"]
        }
        assert actual_issues == expected_issues


def test_every_corpus_event_type_resolves_to_a_registered_payload_model() -> None:
    event_types = {event_type for event_type, _case in CORPUS_CASES}
    for event_type in event_types:
        assert event_type in _PAYLOAD_MODELS, (
            f"{event_type} has no _PAYLOAD_MODELS entry"
        )


def test_every_registered_event_name_is_known_to_band_sdk_core() -> None:
    for event_name in _PAYLOAD_MODELS:
        assert EventType.from_wire_name(event_name) is not None, event_name
