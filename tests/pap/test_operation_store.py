"""Persist observations without changing operation ownership or revision terms."""

from __future__ import annotations

from pathlib import Path

import pytest
from bandpap.storage import StateConflict, StateStore, StateUnavailable
from bandpap.wire import Operation

from tests.pap.kit import operation, session


class TestOperationStore:
    def test_signed_out_proposal_is_never_promoted_to_account(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        proposal = Operation.model_validate(operation())
        store.save_operation(session(), proposal, expected=None)
        store.save_operation(session(account="account"), proposal, expected=proposal)
        with pytest.raises(StateUnavailable, match="operation_not_found"):
            store.operation(session(account="account", session_id="another"), "op_one")
        store.close()

    def test_account_operation_can_resume_only_same_account_and_owner(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        store = StateStore(path)
        proposal = Operation.model_validate(operation())
        store.save_operation(session(account="account"), proposal, expected=None)
        store.close()
        store = StateStore(path)
        assert (
            store.operation(session(account="account", session_id="another"), "op_one")
            == proposal
        )
        for caller in [
            session(),
            session(account="wrong"),
            session(account="account", user="wrong"),
            session(account="account", client="wrong"),
        ]:
            with pytest.raises(StateUnavailable, match="operation_not_found"):
                store.operation(caller, "op_one")
        store.close()

    def test_revision_terms_are_immutable_and_updates_use_compare_and_set(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller = session()
        proposal = Operation.model_validate(operation())
        store.save_operation(caller, proposal, expected=None)
        with pytest.raises(StateConflict, match="immutable"):
            store.save_operation(
                caller,
                Operation.model_validate(operation(summary="changed")),
                expected=proposal,
            )
        revised = Operation.model_validate(
            operation(revision=2, summary="new proposal")
        )
        store.save_operation(caller, revised, expected=proposal)
        with pytest.raises(StateConflict, match="concurrently"):
            store.save_operation(caller, proposal, expected=proposal)
        assert store.operation(caller, "op_one") == revised
        store.close()

    def test_confirmed_snapshot_may_omit_proposal_expiry_and_uncertainty_stays_in_progress(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.db"
        store = StateStore(path)
        caller = session()
        proposal = Operation.model_validate(operation())
        store.save_operation(caller, proposal, expected=None)
        value = operation(
            state="in_progress",
            confirmation={
                "revision": 1,
                "approved_by": "user",
                "confirmed_at": "2026-10-16T20:00:00Z",
            },
        )
        del value["expires_at"]
        running = Operation.model_validate(value)
        store.save_operation(caller, running, expected=proposal)
        store.close()
        store = StateStore(path)
        assert store.operation(caller, "op_one").state == "in_progress"
        with pytest.raises(StateConflict):
            store.save_operation(caller, proposal, expected=running)
        final = Operation.model_validate(
            value
            | {
                "state": "succeeded",
                "result": {"summary": "Application completed action"},
            }
        )
        store.save_operation(caller, final, expected=running)
        with pytest.raises(StateConflict, match="Final"):
            store.save_operation(caller, running, expected=final)
        store.close()

    def test_final_snapshot_can_refresh_optional_metadata_not_state_or_terms(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state.db")
        caller = session()
        value = operation(
            state="succeeded",
            confirmation={
                "revision": 1,
                "approved_by": "user",
                "confirmed_at": "2026-10-16T20:00:00Z",
            },
            result={"summary": "done"},
        )
        original = Operation.model_validate(value)
        store.save_operation(caller, original, expected=None)
        del value["expires_at"]
        refreshed = Operation.model_validate(
            value | {"url": "https://company.example/result"}
        )
        store.save_operation(caller, refreshed, expected=original)
        assert store.operation(caller, "op_one").url == "https://company.example/result"
        with pytest.raises(StateConflict, match="Final"):
            store.save_operation(
                caller,
                Operation.model_validate(value | {"state": "failed"}),
                expected=refreshed,
            )
        with pytest.raises(StateConflict, match="immutable"):
            store.save_operation(
                caller,
                Operation.model_validate(value | {"summary": "changed terms"}),
                expected=refreshed,
            )
        store.close()
