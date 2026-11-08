"""Atomic event persistence, opaque cursors and explicit replay gaps."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from bandpap.storage import StateConflict, StateStore

from tests.pap.kit import bind, event, page, session


class TestEventStore:
    def test_reopen_replay_and_unknown_events_preserve_arrival_order(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller)
        initial = page(event("evt_z"), event("evt_a", type="future"))
        store.ingest(caller, "local", initial, expected_cursor=None)
        store.close()
        store = StateStore(path)
        store.ingest(caller, "local", initial, expected_cursor="evt_a")
        assert [item.id for item in store.events(caller, "local")] == ["evt_z", "evt_a"]
        store.ingest(caller, "local", page(cursor="evt_a"), expected_cursor="evt_a")
        assert store.conversation(caller, "local").cursor == "evt_a"
        store.cursor_expired(caller, "local", expected_cursor="evt_a")
        assert store.conversation(caller, "local").gap
        store.ingest(
            caller,
            "local",
            page(event("evt_a", type="future"), event("evt_b")),
            expected_cursor=None,
        )
        assert [item.id for item in store.events(caller, "local")] == [
            "evt_z",
            "evt_a",
            "evt_b",
        ]
        store.close()

    def test_failure_between_events_and_cursor_rolls_back_entire_batch(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller)
        store.db.execute(
            "CREATE TRIGGER fail_cursor BEFORE UPDATE OF cursor ON conversations BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            store.ingest(caller, "local", page(event()), expected_cursor=None)
        store.close()
        store = StateStore(path)
        assert store.events(caller, "local") == []
        assert store.conversation(caller, "local").cursor is None
        store.db.execute("DROP TRIGGER fail_cursor")
        store.ingest(caller, "local", page(event()), expected_cursor=None)
        assert store.conversation(caller, "local").cursor == "evt_z"
        store.close()

    def test_conflicting_event_empty_page_and_wrong_conversation_do_not_advance_cursor(
        self, tmp_path: Path
    ) -> None:
        caller = session()
        store = StateStore(tmp_path / "state.db")
        bind(store, caller)
        store.ingest(caller, "local", page(event()), expected_cursor=None)
        for invalid in [
            page(event(status="working")),
            page(cursor="evt_other"),
            page(event(), conversation_id="cnv_other"),
        ]:
            with pytest.raises(StateConflict):
                store.ingest(caller, "local", invalid, expected_cursor="evt_z")
        assert store.conversation(caller, "local").cursor == "evt_z"
        assert len(store.events(caller, "local")) == 1
        store.close()

    def test_concurrent_cursor_updates_are_fenced(self, tmp_path: Path) -> None:
        caller = session()
        first, second = (
            StateStore(tmp_path / "state.db"),
            StateStore(tmp_path / "state.db"),
        )
        bind(first, caller)
        first.ingest(caller, "local", page(event()), expected_cursor=None)
        with pytest.raises(StateConflict, match="concurrently"):
            second.ingest(caller, "local", page(event("evt_new")), expected_cursor=None)
        assert [item.id for item in second.events(caller, "local")] == ["evt_z"]
        first.close()
        second.close()
