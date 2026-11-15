"""Durable user-scoped deduplication and independent conversation delivery leases."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from bandpap.storage import RetryFailure, StateConflict, StateStore, StateUnavailable

from tests.pap.kit import bind, request, route, session


class TestDeliveryStore:
    def test_lost_first_response_recovers_original_conversation_after_restart(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller, remote=None)
        store.enqueue(caller, "local", request(data={"custom": [1, False]}))
        lease = store.claim(caller, "local", now=10, lease_seconds=5)
        store.settle(
            caller,
            "msg_one",
            lease=lease.lease,
            now=11,
            response={
                "conversation_id": "cnv_created",
                "status": "working",
                "responder": "agent",
            },
            status_code=201,
            remote_id="cnv_created",
        )
        store.close()
        store = StateStore(path)
        bind(store, caller, "other", remote=None)
        retried = store.enqueue(caller, "other", request(data={"custom": [1, False]}))
        assert retried.binding == "local"
        assert retried.status_code == 201
        assert retried.response["conversation_id"] == "cnv_created"
        assert retried.state == "delivered"
        assert store.claim(caller, "other", now=20, lease_seconds=5) is None
        with pytest.raises(StateConflict, match="message_id_conflict"):
            store.enqueue(caller, "other", request(data={"custom": [2, False]}))
        store.close()

    def test_two_handles_accept_one_logical_message(self, tmp_path: Path) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller)
        store.close()

        def enqueue() -> str:
            handle = StateStore(path)
            try:
                return handle.enqueue(caller, "local", request()).message_id
            finally:
                handle.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(lambda _: enqueue(), range(2))) == [
                "msg_one",
                "msg_one",
            ]
        store = StateStore(path)
        assert store.claim(caller, "local", now=10, lease_seconds=5) is not None
        assert store.claim(caller, "local", now=11, lease_seconds=5) is None
        store.close()

    def test_expired_lease_is_fenced_and_other_conversations_progress(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        first, second = StateStore(path), StateStore(path)
        bind(first, caller)
        bind(first, caller, "other", remote="cnv_other")
        first.enqueue(caller, "local", request())
        first.enqueue(caller, "local", request("msg_two"))
        first.enqueue(caller, "other", request("msg_three"))
        old = first.claim(caller, "local", now=10, lease_seconds=5)
        assert second.claim(caller, "local", now=11, lease_seconds=5) is None
        assert (
            second.claim(caller, "other", now=11, lease_seconds=5).message_id
            == "msg_three"
        )
        renewed = second.claim(caller, "local", now=16, lease_seconds=5)
        assert renewed.message_id == old.message_id
        assert renewed.lease != old.lease
        with pytest.raises(StateConflict, match="lease"):
            first.settle(
                caller,
                "msg_one",
                lease=old.lease,
                now=17,
                response={
                    "conversation_id": "cnv_one",
                    "status": "working",
                    "responder": "agent",
                },
                status_code=202,
            )
        second.settle(caller, "msg_one", lease=renewed.lease, now=17)
        assert second.get_delivery(caller, "msg_one").state == "uncertain"
        assert (
            first.claim(caller, "local", now=18, lease_seconds=5).message_id
            == "msg_one"
        )
        first.close()
        second.close()

    @pytest.mark.parametrize(
        "change",
        [
            {"tenant": "other"},
            {"user": "other"},
            {"client": "other"},
            {"issuer": "https://other.example"},
            {"direction": "inbound"},
        ],
    )
    def test_scope_collisions_do_not_leak_or_mutate(
        self, tmp_path: Path, change: dict
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller, other = session(), session(**change)
        bind(store, caller)
        store.enqueue(caller, "local", request(text="private"))
        with pytest.raises(StateUnavailable):
            store.get_delivery(other, "msg_one")
        bind(store, other)
        assert (
            store.enqueue(other, "local", request(text="different")).payload["message"][
                "text"
            ]
            == "different"
        )
        assert (
            store.get_delivery(caller, "msg_one").payload["message"]["text"]
            == "private"
        )
        store.close()

    def test_revocation_blocks_claim_replay_and_stale_settlement(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller = session()
        bind(store, caller)
        store.enqueue(caller, "local", request())
        lease = store.claim(caller, "local", now=10, lease_seconds=5)
        store.revoke(caller.scope, "local")
        with pytest.raises(StateUnavailable):
            store.claim(caller, "local", now=16, lease_seconds=5)
        with pytest.raises(StateUnavailable):
            store.settle(
                caller,
                "msg_one",
                lease=lease.lease,
                now=11,
                response={
                    "conversation_id": "cnv_one",
                    "status": "working",
                    "responder": "agent",
                },
                status_code=202,
            )
        with pytest.raises(StateUnavailable):
            store.get_delivery(caller, "msg_one")
        with pytest.raises(StateConflict):
            store.connect(caller, "local", route())
        store.close()

    def test_account_bound_conversation_survives_session_renewal_not_signout(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller = session()
        bind(store, caller)
        signed_in = session(account="account")
        store.bind_account(signed_in, "local")
        assert (
            store.conversation(
                session(account="account", session_id="new"), "local"
            ).remote_id
            == "cnv_one"
        )
        with pytest.raises(StateUnavailable):
            store.conversation(caller, "local")
        with pytest.raises(StateUnavailable):
            store.bind_account(session(account="different"), "local")
        store.close()

    @pytest.mark.parametrize("status", [429, 503])
    def test_retryable_response_keeps_work_and_retry_time_across_restart(
        self, tmp_path: Path, status: int
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller)
        store.enqueue(caller, "local", request())
        lease = store.claim(caller, "local", now=10, lease_seconds=5)
        with pytest.raises(ValueError):
            store.settle(
                caller,
                "msg_one",
                lease=lease.lease,
                now=11,
                response={"error": "rate_limited"},
                status_code=status,
            )
        store.settle(
            caller,
            "msg_one",
            lease=lease.lease,
            now=11,
            failure=RetryFailure(status_code=status, retry_at=30),
        )
        store.close()
        store = StateStore(path)
        assert store.get_delivery(caller, "msg_one").state == "ready"
        assert store.get_delivery(caller, "msg_one").failure.status_code == status
        assert store.claim(caller, "local", now=29, lease_seconds=5) is None
        assert (
            store.claim(caller, "local", now=30, lease_seconds=5).message_id
            == "msg_one"
        )
        store.close()

    @pytest.mark.parametrize("now", [float("nan"), float("inf"), -float("inf")])
    def test_nonfinite_clock_cannot_create_unrecoverable_leases(
        self, tmp_path: Path, now: float
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller = session()
        bind(store, caller)
        store.enqueue(caller, "local", request())
        with pytest.raises(ValueError):
            store.claim(caller, "local", now=now, lease_seconds=5)
        assert store.claim(caller, "local", now=10, lease_seconds=5) is not None
        store.close()

    def test_failed_commit_never_acknowledges_or_retains_message(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        caller = session()
        store = StateStore(path)
        bind(store, caller)

        def deny_commit(action: int, argument: str | None, *unused: object) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT"
                else sqlite3.SQLITE_OK
            )

        store.db.set_authorizer(deny_commit)
        with pytest.raises(sqlite3.DatabaseError):
            store.enqueue(caller, "local", request())
        store.db.set_authorizer(None)
        store.close()
        store = StateStore(path)
        with pytest.raises(StateUnavailable):
            store.get_delivery(caller, "msg_one")
        assert store.enqueue(caller, "local", request()).state == "ready"
        store.close()
