"""Tests for the shared inbound message claim registry."""

from __future__ import annotations

from band.runtime.claims import MessageClaimRegistry


def test_claim_reports_contention_without_releasing_owner() -> None:
    registry = MessageClaimRegistry()

    with registry.claim("room-1", "message-1") as owner:
        assert owner
        with registry.claim("room-1", "message-1") as contender:
            assert not contender
        assert registry.inflight_ids("room-1") == {"message-1"}


def test_same_message_id_is_independent_between_rooms() -> None:
    registry = MessageClaimRegistry()

    with registry.claim("room-1", "message-1") as room_one:
        with registry.claim("room-2", "message-1") as room_two:
            assert room_one and room_two


def test_completed_cache_pressure_is_isolated_per_room() -> None:
    registry = MessageClaimRegistry(max_completed=1)
    registry.remember_completed("room-1", "old")
    registry.remember_completed("room-2", "kept")
    registry.remember_completed("room-1", "new")

    assert registry.completed_ids("room-1") == ["new"]
    assert registry.completed_ids("room-2") == ["kept"]
