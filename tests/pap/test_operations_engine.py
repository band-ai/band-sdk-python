"""Authoritative Operations ownership, approval, disk claims and uncertain effects."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from bandpap.operations import EffectOutcome, OperationError
from bandpap.storage import StateConflict, StateStore
from bandpap.wire import ConfirmOperation, Operation, OperationResult

from tests.pap.kit import operation, session
from tests.pap.operationkit import (
    CONFIRM,
    NOW,
    Approvals,
    Authority,
    Executor,
    RevisionAuthority,
    crash_before_effect,
    proposal,
    service,
)


class TestOperationsEngine:
    async def test_revision_approval_and_private_execution_reference(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        approvals, executor = Approvals(), Executor()
        engine = service(store, approvals=approvals, executor=executor)
        initial = proposal(user_approval_required=True)
        await engine.propose(
            session(account="a"), initial, execution_ref="private-effect"
        )
        assert executor.calls == []
        revised = proposal(
            revision=2, summary="Different exact terms", user_approval_required=True
        )
        await engine.revise(
            session(account="a"), revised, execution_ref="private-revision-2"
        )
        with pytest.raises(OperationError, match="terms_changed") as error:
            await engine.confirm(
                session(account="a"), "op_one", CONFIRM, evidence=approvals.token
            )
        assert error.value.status_code == 409
        assert error.value.operation == revised
        with pytest.raises(OperationError, match="user_approval_required"):
            await engine.confirm(
                session(account="a"),
                "op_one",
                ConfirmOperation(revision=2, approved_by="standing_permission"),
                evidence=approvals.token,
            )
        with pytest.raises(PermissionError):
            await engine.confirm(
                session(account="a"),
                "op_one",
                ConfirmOperation(revision=2, approved_by="user"),
                evidence="User said yes in company chat",
            )
        result = await engine.confirm(
            session(account="a", session_id="new"),
            "op_one",
            ConfirmOperation(revision=2, approved_by="user"),
            evidence=approvals.token,
        )
        assert result.state == "succeeded"
        assert result.confirmation.revision == 2
        assert result.confirmation.approved_by == "user"
        assert executor.calls[0].operation.summary == revised.summary
        assert executor.calls[0].execution_ref == "private-revision-2"
        assert "private-revision" not in result.model_dump_json()
        assert executor.calls[0].key not in result.model_dump_json()
        assert len(executor.calls) == 1
        store.close()

    @pytest.mark.parametrize("owner", [session(), session(account="a")])
    async def test_ownership_across_sessions_accounts_and_scopes(
        self, tmp_path: Path, owner
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        engine = service(store)
        await engine.propose(owner, proposal(), execution_ref="private")
        outsiders = [
            session(user="wrong", account=owner.account),
            session(client="wrong", account=owner.account),
            session(tenant="wrong", account=owner.account),
            session(issuer="https://other.example", account=owner.account),
        ]
        if owner.account is None:
            outsiders += [
                session(session_id="other"),
                session(account="a", session_id="other"),
            ]
            assert (
                await engine.read(session(account="a"), "op_one")
            ).state == "proposed"
        else:
            outsiders += [session(), session(account="wrong")]
            assert (
                await engine.read(session(account="a", session_id="other"), "op_one")
            ).state == "proposed"
        for outsider in outsiders:
            for method in (engine.read, engine.cancel, engine.recover):
                with pytest.raises(OperationError, match="operation_not_found"):
                    await method(outsider, "op_one")
            with pytest.raises(OperationError, match="operation_not_found"):
                await engine.confirm(outsider, "op_one", CONFIRM, evidence=None)
        store.close()

    async def test_host_scope_and_revocation_checks_block_effects(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        authority, executor, approvals = Authority(), Executor(), Approvals()
        engine = service(
            store, authority=authority, executor=executor, approvals=approvals
        )
        await engine.propose(session(), proposal(), execution_ref="private")
        authority.denied.add("confirm")
        with pytest.raises(PermissionError):
            await engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        assert approvals.calls == []
        assert executor.calls == []
        authority.revoked = True
        for method in (engine.read, engine.cancel, engine.recover):
            with pytest.raises(PermissionError):
                await method(session(), "op_one")
        store.close()

    async def test_revisions_are_immutable_and_final_records_are_fixed(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        engine = service(store)
        original = proposal()
        await engine.propose(session(), original, execution_ref="private")
        assert (
            await engine.propose(session(), original, execution_ref="private")
            == original
        )
        for changed in (
            proposal(summary="Different"),
            proposal(revision=3),
            proposal(revision=1),
        ):
            with pytest.raises(StateConflict):
                await engine.revise(session(), changed, execution_ref="changed")
        with pytest.raises(StateConflict):
            await engine.propose(
                session(), proposal(summary="Different"), execution_ref="private"
            )
        final = await engine.cancel(session(), "op_one")
        assert final.state == "cancelled"
        assert await engine.cancel(session(), "op_one") == final
        assert await engine.recover(session(), "op_one") == final
        assert (
            await engine.confirm(session(), "op_one", CONFIRM, evidence=None) == final
        )
        with pytest.raises(StateConflict):
            await engine.revise(
                session(), proposal(revision=2), execution_ref="private"
            )
        store.close()

    async def test_expiry_never_dispatches_and_persists_on_disk(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "operations.db"
        store = StateStore(path)
        executor, approvals = Executor(), Approvals()
        engine = service(store, executor=executor, approvals=approvals)
        await engine.propose(session(), proposal(), execution_ref="private")
        engine.clock = lambda: NOW + timedelta(days=2)
        result = await engine.confirm(
            session(), "op_one", CONFIRM, evidence=approvals.token
        )
        assert result.state == "expired"
        assert executor.calls == []
        store.close()
        store = StateStore(path)
        assert (await service(store).read(session(), "op_one")).state == "expired"
        store.close()

    async def test_concurrent_revision_invalidates_pending_evidence(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "operations.db"
        first, second = StateStore(path), StateStore(path)
        approvals, executor = Approvals(), Executor()
        approvals.entered, approvals.release = asyncio.Event(), asyncio.Event()
        engine = service(first, approvals=approvals, executor=executor)
        await engine.propose(session(), proposal(), execution_ref="private")
        pending = asyncio.create_task(
            engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        )
        await approvals.entered.wait()
        await service(second).revise(
            session(), proposal(revision=2, summary="new"), execution_ref="new-private"
        )
        approvals.release.set()
        with pytest.raises(OperationError, match="terms_changed"):
            await pending
        assert executor.calls == []
        first.close()
        second.close()

    def test_actual_parallel_sqlite_handles_dispatch_only_once(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "operations.db"
        store = StateStore(path)
        asyncio.run(
            service(store).propose(session(), proposal(), execution_ref="private")
        )
        store.close()
        barrier = Barrier(2)
        executor = Executor()

        def confirm_on_handle():
            local = StateStore(path)
            approvals = Approvals()
            engine = service(local, approvals=approvals, executor=executor)
            barrier.wait()
            try:
                return asyncio.run(
                    engine.confirm(
                        session(), "op_one", CONFIRM, evidence=approvals.token
                    )
                )
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm_on_handle(), range(2)))
        assert all(result.state in {"in_progress", "succeeded"} for result in results)
        assert len(executor.calls) == 1
        store = StateStore(path)
        assert (
            asyncio.run(service(store).read(session(), "op_one")).state == "succeeded"
        )
        store.close()

    async def test_lost_effect_response_restart_uses_same_key_and_never_redispatches(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "operations.db"
        store = StateStore(path)
        approvals, executor = Approvals(), Executor()
        executor.raise_after_effect = True
        engine = service(store, approvals=approvals, executor=executor)
        await engine.propose(session(account="a"), proposal(), execution_ref="private")
        uncertain = await engine.confirm(
            session(account="a"), "op_one", CONFIRM, evidence=approvals.token
        )
        assert uncertain.state == "in_progress" and uncertain.result is None
        effect_key = executor.calls[0].key
        store.close()
        store = StateStore(path)
        restarted = service(store, approvals=approvals, executor=executor)
        assert (
            await restarted.confirm(
                session(account="a", session_id="renewed"),
                "op_one",
                CONFIRM,
                evidence=None,
            )
            == uncertain
        )
        executor.outcome = None
        assert (
            await restarted.recover(session(account="a"), "op_one")
        ).state == "in_progress"
        executor.outcome = EffectOutcome(
            state="succeeded", result=OperationResult(summary="Recovered host effect")
        )
        final = await restarted.recover(
            session(account="a", session_id="renewed"), "op_one"
        )
        assert final.state == "succeeded"
        assert len(executor.calls) == 1
        assert all(work.key == effect_key for work in executor.recoveries)
        assert all(work.session.session_id == "session" for work in executor.recoveries)
        store.close()

    async def test_crash_after_claim_before_dispatch_stays_recoverable(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "operations.db"
        store = StateStore(path)
        approvals, executor = Approvals(), Executor()
        executor.entered, executor.release = asyncio.Event(), asyncio.Event()
        engine = service(store, approvals=approvals, executor=executor)
        await engine.propose(session(), proposal(), execution_ref="private")
        pending = asyncio.create_task(
            engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        )
        await executor.entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        store.close()
        store = StateStore(path)
        executor.outcome = None
        restarted = service(store, approvals=approvals, executor=executor)
        assert (
            await restarted.confirm(session(), "op_one", CONFIRM, evidence=None)
        ).state == "in_progress"
        assert (await restarted.recover(session(), "op_one")).state == "in_progress"
        assert len(executor.calls) == 1
        store.close()

    async def test_cancellation_uncertainty_and_known_partial_failure(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        approvals, executor = Approvals(), Executor()
        executor.outcome = None
        engine = service(store, approvals=approvals, executor=executor)
        await engine.propose(session(), proposal(), execution_ref="private")
        assert (
            await engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        ).state == "in_progress"
        assert (await engine.cancel(session(), "op_one")).state == "in_progress"
        executor.outcome = EffectOutcome(
            state="failed",
            result=OperationResult(
                summary="Reserved item, then released it; no charge."
            ),
        )
        failed = await engine.recover(session(), "op_one")
        assert failed.state == "failed"
        assert failed.result.summary == executor.outcome.result.summary
        executor.outcome = EffectOutcome(
            state="cancelled", result=OperationResult(summary="Stopped")
        )
        assert await engine.cancel(session(), "op_one") == failed
        assert len(executor.cancellations) == 1
        store.close()

    async def test_observations_do_not_approve_or_execute(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "operations.db")
        observation = Operation.model_validate(
            operation(
                state="succeeded",
                confirmation={
                    "revision": 1,
                    "approved_by": "user",
                    "confirmed_at": NOW.isoformat(),
                },
                result={"summary": "Untrusted observation"},
            )
        )
        store.save_operation(session(), observation, expected=None)
        executor = Executor()
        engine = service(store, executor=executor)
        with pytest.raises(OperationError, match="operation_not_found"):
            await engine.read(session(), "op_one")
        await engine.propose(session(), proposal(), execution_ref="private")
        assert (await engine.read(session(), "op_one")).state == "proposed"
        assert executor.calls == []
        store.close()

    async def test_restart_after_crash_before_actual_dispatch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        path = tmp_path / "operations.db"
        store = StateStore(path)
        approvals, executor = Approvals(), Executor()
        engine = service(store, approvals=approvals, executor=executor)
        await engine.propose(session(), proposal(), execution_ref="private")
        monkeypatch.setattr(engine, "_effect", crash_before_effect)
        with pytest.raises(RuntimeError, match="before effect dispatch"):
            await engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        assert executor.calls == []
        store.close()
        store = StateStore(path)
        restarted = service(store, executor=executor)
        assert (
            await restarted.confirm(session(), "op_one", CONFIRM, evidence=None)
        ).state == "in_progress"
        executor.outcome = None
        assert (await restarted.recover(session(), "op_one")).state == "in_progress"
        assert executor.calls == []
        store.close()

    async def test_authority_revoked_while_approval_is_pending(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        approvals, executor, authority = Approvals(), Executor(), Authority()
        approvals.entered, approvals.release = asyncio.Event(), asyncio.Event()
        engine = service(
            store, approvals=approvals, executor=executor, authority=authority
        )
        await engine.propose(session(), proposal(), execution_ref="private")
        pending = asyncio.create_task(
            engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        )
        await approvals.entered.wait()
        authority.revoked = True
        approvals.release.set()
        with pytest.raises(PermissionError):
            await pending
        assert executor.calls == []
        store.close()

    @pytest.mark.parametrize(
        "action", ["read", "cancel", "recover", "confirm", "propose"]
    )
    async def test_changed_snapshot_requires_new_authority_before_projection_or_mutation(
        self, tmp_path: Path, action
    ) -> None:
        path = tmp_path / "operations.db"
        first, second = StateStore(path), StateStore(path)
        await service(second).propose(session(), proposal(), execution_ref="private")
        authority, executor, approvals = (
            RevisionAuthority(action),
            Executor(),
            Approvals(),
        )
        engine = service(
            first, authority=authority, executor=executor, approvals=approvals
        )
        if action == "confirm":
            request = engine.confirm(
                session(), "op_one", CONFIRM, evidence=approvals.token
            )
        elif action == "propose":
            request = engine.propose(session(), proposal(), execution_ref="private")
        else:
            request = getattr(engine, action)(session(), "op_one")
        pending = asyncio.create_task(request)
        await authority.entered.wait()
        await service(second).revise(
            session(),
            proposal(revision=2, summary="Private new terms"),
            execution_ref="private2",
        )
        authority.release.set()
        with pytest.raises(PermissionError, match="revised operation"):
            await pending
        current = await service(second).read(session(), "op_one")
        assert current.revision == 2 and current.state == "proposed"
        assert authority.revisions[-1] == 2
        assert (
            executor.calls == []
            and executor.cancellations == []
            and executor.recoveries == []
        )
        first.close()
        second.close()

    async def test_withdrawn_approval_during_final_authorization_blocks_claim(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        store = StateStore(tmp_path / "operations.db")
        approvals, authority, executor = Approvals(), Authority(), Executor()
        engine = service(
            store, approvals=approvals, authority=authority, executor=executor
        )
        await engine.propose(session(), proposal(), execution_ref="private")
        entered, release = asyncio.Event(), asyncio.Event()
        authorize = authority.authorize

        async def gated(session, action, operation):
            await authorize(session, action, operation)
            if action == "confirm" and authority.calls.count("confirm") == 2:
                entered.set()
                await release.wait()

        monkeypatch.setattr(authority, "authorize", gated)
        pending = asyncio.create_task(
            engine.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        )
        await asyncio.wait_for(entered.wait(), 2)
        approvals.enabled = False
        release.set()
        with pytest.raises(PermissionError, match="trusted revision approval"):
            await pending
        assert (await engine.read(session(), "op_one")).state == "proposed"
        assert executor.calls == []
        store.close()
