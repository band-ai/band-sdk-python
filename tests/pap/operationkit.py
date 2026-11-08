"""Host-owned authority and effect doubles for Operations protocol tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from bandpap.operations import Action, EffectOutcome, EffectRequest, Operations
from bandpap.storage import Session, StateStore
from bandpap.wire import ConfirmOperation, Operation, OperationResult

from tests.pap.kit import operation

NOW = datetime(2026, 10, 16, 20, tzinfo=UTC)
CONFIRM = ConfirmOperation(revision=1, approved_by="user")


def proposal(**changes: Any) -> Operation:
    return Operation.model_validate(operation(**changes))


class Authority:
    def __init__(self) -> None:
        self.revoked = False
        self.denied: set[str] = set()
        self.calls: list[Action] = []

    async def authorize(
        self, session: Session, action: Action, operation: Operation
    ) -> None:
        self.calls.append(action)
        if self.revoked or action in self.denied:
            raise PermissionError("Host denied authority or required scope")


class Approvals:
    def __init__(self) -> None:
        self.calls: list[tuple[Operation, ConfirmOperation]] = []
        self.token = object()
        self.enabled = True
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def verify(
        self,
        session: Session,
        operation: Operation,
        confirmation: ConfirmOperation,
        evidence: object,
    ) -> None:
        self.calls.append((operation, confirmation))
        if evidence is not self.token or not self.enabled:
            raise PermissionError("No trusted revision approval")
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()


class Executor:
    def __init__(self) -> None:
        self.calls: list[EffectRequest] = []
        self.recoveries: list[EffectRequest] = []
        self.cancellations: list[EffectRequest] = []
        self.outcome: EffectOutcome | None = EffectOutcome(
            state="succeeded", result=OperationResult(summary="Host completed effect")
        )
        self.raise_after_effect = False
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def execute(self, request: EffectRequest) -> EffectOutcome | None:
        self.calls.append(request)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.raise_after_effect:
            raise TimeoutError("Effect response lost")
        return self.outcome

    async def recover(self, request: EffectRequest) -> EffectOutcome | None:
        self.recoveries.append(request)
        return self.outcome

    async def cancel(self, request: EffectRequest) -> EffectOutcome | None:
        self.cancellations.append(request)
        return self.outcome


def service(
    store: StateStore,
    *,
    authority: Authority | None = None,
    approvals: Approvals | None = None,
    executor: Executor | None = None,
) -> Operations:
    return Operations(
        store,
        authorization=authority or Authority(),
        approvals=approvals or Approvals(),
        executor=executor or Executor(),
        clock=lambda: NOW,
    )


async def crash_before_effect(*args: object) -> Operation:
    raise RuntimeError("Process stopped after durable claim, before effect dispatch")


class RevisionAuthority(Authority):
    def __init__(self, action: Action) -> None:
        super().__init__()
        self.action = action
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.revisions: list[int] = []
        self.paused = False

    async def authorize(
        self, session: Session, action: Action, operation: Operation
    ) -> None:
        await super().authorize(session, action, operation)
        if action != self.action:
            return
        self.revisions.append(operation.revision)
        if operation.revision != 1:
            raise PermissionError("Host denies access to revised operation")
        if not self.paused:
            self.paused = True
            self.entered.set()
            await self.release.wait()
