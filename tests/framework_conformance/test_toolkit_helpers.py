"""PR-run unit tests for baseline-toolkit helpers.

Pure logic that would otherwise be skipped under ``tests/e2e/**`` (E2E-gated), so it
lives here to run on every PR — no platform, no keys.

* ``ToolSpec.as_callable`` must carry the ``band_terminal`` opt-in marker so the
  callable path (pydantic-ai/agno) agrees with the CustomToolDef tuple path.
* ``_is_letta_cloud`` must match the Letta Cloud *host*, ignoring scheme/case/port/
  path, so a real self-hosted URL isn't misread as cloud (or vice versa).
* ``Replies.assert_at_most`` — the narrow upper-bound runaway guard: passes at/below
  the ceiling, fails above it with the offending contents in the diagnostic.
* ``running_members`` — the shared co-residency helper starts every member
  concurrently and tears them all down on error.
* ``AdapterCell.run_many`` — validates ``count`` / ``labels`` before it provisions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from band.client.streaming import MessageCreatedPayload

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.observations import Replies
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
    running_members,
)
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


# --- Replies.assert_at_most (the narrow upper-bound runaway guard) ------------


def _reply(content: str) -> MessageCreatedPayload:
    """A minimal captured agent reply carrying ``content`` — the only field the
    upper-bound guard inspects."""
    now = "2026-01-01T00:00:00Z"
    return MessageCreatedPayload(
        id="m",
        content=content,
        message_type="text",
        sender_id="a",
        sender_type="Agent",
        inserted_at=now,
        updated_at=now,
    )


def test_assert_at_most_passes_at_or_below_ceiling() -> None:
    Replies([_reply("one")]).assert_at_most(3)  # below
    Replies([_reply("a"), _reply("b"), _reply("c")]).assert_at_most(3)  # exactly


def test_assert_at_most_raises_above_ceiling_naming_contents() -> None:
    replies = Replies([_reply("first"), _reply("second"), _reply("RUNAWAY")])
    with pytest.raises(AssertionError, match="RUNAWAY"):
        replies.assert_at_most(2)


# --- running_members (shared concurrent-start machinery) ----------------------


async def test_running_members_starts_concurrently_preserving_order() -> None:
    """Every member enters concurrently — the barrier only releases once all three
    have arrived — and the identities come back in member order. A serial start would
    deadlock the barrier and the test would time out, so passing proves concurrency."""
    barrier = asyncio.Barrier(3)

    @asynccontextmanager
    async def member(index: int) -> AsyncGenerator[ProvisionedAgent, None]:
        await barrier.wait()
        yield ProvisionedAgent(id=f"id-{index}", api_key="k", name=f"n-{index}")

    async with running_members([member(0), member(1), member(2)]) as running:
        assert [p.id for p in running] == ["id-0", "id-1", "id-2"]


async def test_running_members_tears_down_all_members_on_error() -> None:
    """When the body fails, every entered member is still unwound (its ``finally``
    runs), so a failing test never leaks a running agent."""
    exited: list[int] = []

    @asynccontextmanager
    async def member(index: int) -> AsyncGenerator[ProvisionedAgent, None]:
        try:
            yield ProvisionedAgent(id=f"id-{index}", api_key="k", name=f"n-{index}")
        finally:
            exited.append(index)

    with pytest.raises(RuntimeError, match="boom"):
        async with running_members([member(0), member(1)]):
            raise RuntimeError("boom")
    assert sorted(exited) == [0, 1]


# --- AdapterCell.run_many argument validation (no provisioning) ---------------


def _fake_cell() -> AdapterCell:
    """An AdapterCell whose settings/resources are never touched: ``run_many``
    validates its arguments before it provisions anything, so mocks suffice."""
    return AdapterCell(
        adapter_id="fake",
        settings=cast(BaselineSettings, MagicMock()),
        resources=cast(ResourceManager, MagicMock()),
    )


async def test_run_many_rejects_nonpositive_count() -> None:
    with pytest.raises(ValueError, match="positive"):
        async with _fake_cell().run_many(0):
            pass


async def test_run_many_rejects_mismatched_labels() -> None:
    with pytest.raises(ValueError, match="labels length"):
        async with _fake_cell().run_many(2, labels=["only-one"]):
            pass
