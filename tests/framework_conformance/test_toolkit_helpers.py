"""PR-run unit tests for two baseline-toolkit helpers.

* ``ToolSpec.as_callable`` must carry the ``band_terminal`` opt-in marker so the
  callable path (pydantic-ai/agno) agrees with the CustomToolDef tuple path.
* ``_is_letta_cloud`` must match the Letta Cloud *host*, ignoring scheme/case/port/
  path, so a real self-hosted URL isn't misread as cloud (or vice versa).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tests.e2e.baseline.toolkit.requirements import _is_letta_cloud
from tests.e2e.baseline.toolkit.tools import ToolSpec


class SampleInput(BaseModel):
    """Sample tool."""

    text: str


def _handler(args: SampleInput) -> str:
    return args.text


def test_as_callable_carries_band_terminal_marker() -> None:
    handler = _handler
    handler.band_terminal = True  # type: ignore[attr-defined]
    call = ToolSpec(SampleInput, handler).as_callable()
    assert getattr(call, "band_terminal", False) is True


def test_as_callable_defaults_non_terminal() -> None:
    def plain(args: SampleInput) -> str:
        return args.text

    call = ToolSpec(SampleInput, plain).as_callable()
    assert getattr(call, "band_terminal", False) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://api.letta.com",
        "https://api.letta.com/v1",
        "http://api.letta.com",
        "HTTPS://API.LETTA.COM",
        "https://api.letta.com/",
        "api.letta.com/v1",
    ],
)
def test_is_letta_cloud_matches_host_regardless_of_shape(url: str) -> None:
    assert _is_letta_cloud(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://localhost:8283",
        "https://letta.internal.example.com",
        "https://api.letta.com.evil.com",
    ],
)
def test_is_letta_cloud_rejects_non_cloud(url: str) -> None:
    assert _is_letta_cloud(url) is False
