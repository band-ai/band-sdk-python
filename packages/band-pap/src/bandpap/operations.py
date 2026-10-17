"""Durable Operations protocol decisions; authorization and effects belong to hosts."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from bandpap.storage import Session, StateConflict, StateStore, canonical
from bandpap.wire import ConfirmOperation, Operation, OperationResult

Action = Literal["propose", "read", "revise", "confirm", "cancel", "recover"]
T = TypeVar("T")
FINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


class OperationError(ValueError):
    def __init__(
        self, error: str, status_code: int, operation: Operation | None = None
    ) -> None:
        super().__init__(error)
        self.error, self.status_code, self.operation = error, status_code, operation


class OperationAuthorization(Protocol):
    async def authorize(
        self, session: Session, action: Action, operation: Operation
    ) -> None:
        """Verify caller authority, scopes and revocation, or raise.

        Session is a host-authenticated input, never a protocol body claim. Hosts
        must recheck revocation here, including recovery/background calls.
        """
        ...


class ApprovalVerifier(Protocol):
    async def verify(
        self,
        session: Session,
        operation: Operation,
        confirmation: ConfirmOperation,
        evidence: object,
    ) -> None:
        """Accept host-verified approval of these terms, or raise.

        The host binds evidence to this owner/company/account/revision and the
        claimed approved_by. Company/chat text must never qualify as evidence.
        Standing permission policy belongs entirely to the host. Verification
        may repeat before dispatch/retries and must check current approval; it
        must not consume otherwise valid evidence on a verification-only call.
        """
        ...


@dataclass(frozen=True)
class EffectRequest:
    """Private host handoff. Neither reference nor key belongs in agent views."""

    key: str
    execution_ref: str
    session: Session
    operation: Operation


class EffectOutcome(BaseModel):
    """A known final outcome, including a complete account of partial changes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["succeeded", "failed", "cancelled"]
    result: OperationResult


class EffectExecutor(Protocol):
    """The host must durably deduplicate effects by EffectRequest.key.

    execute is dispatched at most once by this engine. A crash before dispatch
    and a lost effect response are indistinguishable: recover must reconcile the
    same key, never create an independently keyed replacement. None means unknown
    or pending. Exceptions are also unknown, never evidence of failure. cancel
    may return a final outcome only when the host knows what changes occurred.
    All methods coordinate by key: a final cancellation fences concurrent/delayed
    execute, and final outcomes for a key cannot disagree.
    """

    async def execute(self, request: EffectRequest) -> EffectOutcome | None: ...
    async def recover(self, request: EffectRequest) -> EffectOutcome | None: ...
    async def cancel(self, request: EffectRequest) -> EffectOutcome | None: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_service (
    scope TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, account TEXT,
    payload TEXT NOT NULL, execution_ref TEXT NOT NULL, effect_key TEXT NOT NULL UNIQUE,
    dispatched INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(scope, id)
);
CREATE TABLE IF NOT EXISTS operation_service_revisions (
    scope TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
    payload TEXT NOT NULL, execution_ref TEXT NOT NULL,
    PRIMARY KEY(scope, id, revision),
    FOREIGN KEY(scope, id) REFERENCES operation_service(scope, id)
);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def copy_operation(operation: Operation) -> Operation:
    return Operation.model_validate_json(operation.model_dump_json())


class Operations:
    """One authoritative company operation record across all exposed channels.

    StateStore observation records cannot approve or dispatch an effect. Channels
    must share this service's database. Hosts serialize calls on each store
    handle; separate handles/processes coordinate through SQLite. Host callbacks
    run outside transactions, followed by snapshot checks and reauthorization.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        authorization: OperationAuthorization,
        approvals: ApprovalVerifier,
        executor: EffectExecutor,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store, self.authorization, self.approvals = store, authorization, approvals
        self.executor, self.clock = executor, clock
        self.store.db.executescript(SCHEMA)

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Operation clock must include an offset")
        return now

    def _row(self, session: Session, operation_id: str) -> sqlite3.Row:
        row = self.store.db.execute(
            "SELECT * FROM operation_service WHERE scope=? AND id=?",
            (session.scope.key(), operation_id),
        ).fetchone()
        if (
            row is None
            or (row["account"] is None and row["session"] != session.session_id)
            or (row["account"] is not None and row["account"] != session.account)
        ):
            raise OperationError("operation_not_found", 404)
        return row

    def _get(self, session: Session, operation_id: str) -> Operation:
        return Operation.model_validate_json(
            self._row(session, operation_id)["payload"]
        )

    def _write(self, session: Session, operation: Operation) -> None:
        self.store.db.execute(
            "UPDATE operation_service SET payload=? WHERE scope=? AND id=?",
            (canonical(operation.wire()), session.scope.key(), operation.operation_id),
        )

    @staticmethod
    def _changed(operation: Operation, **changes: object) -> Operation:
        return Operation.model_validate(operation.wire() | changes)

    def _expire(
        self, session: Session, operation: Operation, *, now: datetime | None = None
    ) -> Operation:
        if (
            operation.state == "proposed"
            and operation.expires_at is not None
            and operation.expires_at <= (now if now is not None else self._now())
        ):
            operation = self._changed(
                operation,
                state="expired",
                result={
                    "summary": "Proposal expired before confirmation; nothing was done."
                },
            )
            self._write(session, operation)
        return operation

    async def _authorize(
        self, session: Session, action: Action, operation: Operation
    ) -> None:
        await self.authorization.authorize(session, action, copy_operation(operation))

    async def _authorized(
        self,
        session: Session,
        action: Action,
        operation_id: str,
        apply: Callable[[Operation], T],
        *,
        before: Callable[[Operation], Awaitable[None]] | None = None,
    ) -> T:
        while True:
            snapshot = self._get(session, operation_id)
            await self._authorize(session, action, snapshot)
            if before is not None:
                await before(snapshot)
            with self.store.transaction():
                current = self._get(session, operation_id)
                if current != snapshot:
                    continue
                return apply(current)

    def _revision(
        self, session: Session, operation: Operation, execution_ref: str
    ) -> None:
        self.store.db.execute(
            "INSERT INTO operation_service_revisions(scope,id,revision,payload,execution_ref) VALUES(?,?,?,?,?)",
            (
                session.scope.key(),
                operation.operation_id,
                operation.revision,
                canonical(operation.wire()),
                execution_ref,
            ),
        )

    async def propose(
        self, session: Session, operation: Operation, *, execution_ref: str
    ) -> Operation:
        operation = copy_operation(operation)
        if operation.state != "proposed" or operation.revision != 1:
            raise ValueError("A new operation must propose revision 1")
        if not isinstance(execution_ref, str) or not execution_ref:
            raise ValueError("Execution reference must be nonempty")
        await self._authorize(session, "propose", operation)
        with self.store.transaction():
            exists = self.store.db.execute(
                "SELECT 1 FROM operation_service WHERE scope=? AND id=?",
                (session.scope.key(), operation.operation_id),
            ).fetchone()
            if not exists:
                if operation.expires_at is None or operation.expires_at <= self._now():
                    raise ValueError("A proposal must expire in the future")
                self.store.db.execute(
                    """INSERT INTO operation_service
                    (scope,id,session,account,payload,execution_ref,effect_key) VALUES(?,?,?,?,?,?,?)""",
                    (
                        session.scope.key(),
                        operation.operation_id,
                        session.session_id,
                        session.account,
                        canonical(operation.wire()),
                        execution_ref,
                        uuid4().hex,
                    ),
                )
                self._revision(session, operation, execution_ref)
                return operation

        def retry(current: Operation) -> Operation:
            original = self.store.db.execute(
                "SELECT payload,execution_ref FROM operation_service_revisions WHERE scope=? AND id=? AND revision=1",
                (session.scope.key(), operation.operation_id),
            ).fetchone()
            if (
                original["payload"] != canonical(operation.wire())
                or original["execution_ref"] != execution_ref
            ):
                raise StateConflict("Operation ID already has a different proposal")
            return self._expire(session, current)

        return await self._authorized(session, "propose", operation.operation_id, retry)

    async def read(self, session: Session, operation_id: str) -> Operation:
        return await self._authorized(
            session,
            "read",
            operation_id,
            lambda current: self._expire(session, current),
        )

    async def revise(
        self, session: Session, operation: Operation, *, execution_ref: str
    ) -> Operation:
        operation = copy_operation(operation)
        await self._authorize(session, "revise", operation)
        if not isinstance(execution_ref, str) or not execution_ref:
            raise ValueError("Execution reference must be nonempty")

        def revision(current: Operation) -> Operation:
            current = self._expire(session, current)
            if current.state != "proposed":
                raise StateConflict("Only a proposed operation can be revised")
            if (
                operation.state != "proposed"
                or operation.revision != current.revision + 1
            ):
                raise StateConflict("A revision must be the next proposed revision")
            if operation.expires_at is None or operation.expires_at <= self._now():
                raise ValueError("A revision must expire in the future")
            self._revision(session, operation, execution_ref)
            self._write(session, operation)
            self.store.db.execute(
                "UPDATE operation_service SET execution_ref=? WHERE scope=? AND id=?",
                (execution_ref, session.scope.key(), operation.operation_id),
            )
            return operation

        return await self._authorized(
            session, "revise", operation.operation_id, revision
        )

    def _check_confirmation(
        self, operation: Operation, request: ConfirmOperation
    ) -> None:
        if request.revision != operation.revision:
            raise OperationError("terms_changed", 409, operation)
        if operation.user_approval_required and request.approved_by != "user":
            raise OperationError("user_approval_required", 403)

    async def confirm(
        self,
        session: Session,
        operation_id: str,
        request: ConfirmOperation,
        *,
        evidence: object,
    ) -> Operation:
        request = ConfirmOperation.model_validate(request)
        while True:
            snapshot = await self._authorized(
                session,
                "confirm",
                operation_id,
                lambda current: self._expire(session, current),
            )
            self._check_confirmation(snapshot, request)
            if snapshot.state != "proposed":
                return snapshot
            await self.approvals.verify(
                session, copy_operation(snapshot), request, evidence
            )

            def claim(
                current: Operation,
                approved: Operation = snapshot,
            ) -> tuple[Operation, EffectRequest | None] | None:
                if current != approved:
                    return None
                confirmed_at = self._now()
                current = self._expire(session, current, now=confirmed_at)
                if current.state != "proposed":
                    return current, None
                running = self._changed(
                    current,
                    state="in_progress",
                    confirmation={
                        "revision": request.revision,
                        "approved_by": request.approved_by,
                        "confirmed_at": confirmed_at.isoformat(),
                    },
                )
                self._write(session, running)
                self.store.db.execute(
                    "UPDATE operation_service SET dispatched=1 WHERE scope=? AND id=? AND dispatched=0",
                    (session.scope.key(), operation_id),
                )
                return running, self._work(session, operation_id)

            async def verify_claim(
                current: Operation, approved: Operation = snapshot
            ) -> None:
                if current == approved:
                    await self.approvals.verify(
                        session, copy_operation(approved), request, evidence
                    )

            claimed = await self._authorized(
                session, "confirm", operation_id, claim, before=verify_claim
            )
            if claimed is None:
                continue
            running, work = claimed
            if work is None:
                return running
            return await self._effect(session, operation_id, work, "execute")

    def _work(self, session: Session, operation_id: str) -> EffectRequest:
        row = self._row(session, operation_id)
        owner = Session(
            scope=session.scope, session_id=row["session"], account=row["account"]
        )
        return EffectRequest(
            row["effect_key"],
            row["execution_ref"],
            owner,
            self._get(session, operation_id),
        )

    async def _effect(
        self,
        session: Session,
        operation_id: str,
        work: EffectRequest,
        method: Literal["execute", "recover", "cancel"],
    ) -> Operation:
        action: Action = "confirm" if method == "execute" else method
        try:
            outcome = await getattr(self.executor, method)(work)
            if outcome is not None:
                outcome = EffectOutcome.model_validate_json(outcome.model_dump_json())
        except Exception:  # noqa: BLE001 - every unknown host failure stays unresolved
            return await self._authorized(
                session, action, operation_id, lambda current: current
            )
        with self.store.transaction():
            current = self._get(session, operation_id)
            if outcome is not None and current.state == "in_progress":
                current = self._changed(
                    current,
                    state=outcome.state,
                    result=outcome.result.model_dump(mode="json"),
                )
                self._write(session, current)
        return await self._authorized(
            session, action, operation_id, lambda current: current
        )

    async def recover(self, session: Session, operation_id: str) -> Operation:
        def recovery(current: Operation) -> tuple[Operation, EffectRequest | None]:
            current = self._expire(session, current)
            return current, self._work(
                session, operation_id
            ) if current.state == "in_progress" else None

        current, work = await self._authorized(
            session, "recover", operation_id, recovery
        )
        if work is None:
            return current
        return await self._effect(session, operation_id, work, "recover")

    async def cancel(self, session: Session, operation_id: str) -> Operation:
        def cancellation(current: Operation) -> tuple[Operation, EffectRequest | None]:
            current = self._expire(session, current)
            if current.state in FINAL:
                return current, None
            if current.state == "proposed":
                current = self._changed(
                    current,
                    state="cancelled",
                    result={"summary": "Proposal declined; nothing was done."},
                )
                self._write(session, current)
                return current, None
            return current, self._work(session, operation_id)

        current, work = await self._authorized(
            session, "cancel", operation_id, cancellation
        )
        if work is None:
            return current
        return await self._effect(session, operation_id, work, "cancel")
