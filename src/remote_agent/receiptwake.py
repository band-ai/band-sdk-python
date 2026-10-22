"""Receipt-addressed dual-write recovery, independent of any hosted adapter.

The room is authority. Transport acceptance is not completion of remote work.
Claimed work survives a crash as held, because arbitrary effects cannot safely
be repeated without destination reconciliation. No model or Band calls occur here.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Self

from remote_agent.storage import StoreError, canonical
from remote_agent.store import Store
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.network import HTTPRoom


class WakeJournal:
    def __init__(self, path: SourcePath, scope: JsonObject) -> None:
        self.scope = deepcopy(scope)
        self.store = Store(path)
        try:
            prior = self.store.get("wake_scope")
            if prior is None:
                self.store.commit("wake_scope", scope, updates={"wake_scope": scope})
            elif prior != scope:
                raise StoreError("Wake journal belongs to another room identity")
        except BaseException:
            self.store.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.store.close()

    def _event(self, receipt: JsonObject) -> JsonObject:
        if not isinstance(receipt, dict):
            raise StoreError("Expected an accepted room receipt")
        if receipt.get("status") == "duplicate" and "original" in receipt:
            receipt = receipt["original"]
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") not in ("accepted", "duplicate")
            or not isinstance(receipt.get("id"), str)
            or not receipt["id"]
            or receipt.get("session") != self.scope["session"]
            or not isinstance(receipt.get("actor"), str)
            or not isinstance(receipt.get("recipients"), list)
            or any(
                not isinstance(actor, str) or not actor
                for actor in receipt["recipients"]
            )
            or len(set(receipt["recipients"])) != len(receipt["recipients"])
        ):
            raise StoreError("Invalid accepted room receipt")
        event = dict(receipt, status="accepted")
        canonical(event)
        return event

    def stage(self, receipt: JsonObject) -> None:
        """Persist before proposal finalization or shell delivery; retries merge."""
        event = self._event(receipt)
        if event["actor"] != self.scope["actor"]:
            raise StoreError("Outbound receipt belongs to another actor")
        with self.store.lock:
            outbox = self.store.get("wake_outbox", {})
            prior = outbox.get(event["id"])
            if prior is not None:
                if prior["event"] != event:
                    raise StoreError("Conflicting room receipt reuse")
                return
            outbox[event["id"]] = {
                "event": event,
                "targets": {actor: "pending" for actor in event["recipients"]},
            }
            self.store.commit(
                "wake_staged", {"receipt": event["id"]}, updates={"wake_outbox": outbox}
            )

    def pending(self) -> list[JsonObject]:
        return [
            {
                "receipt": identifier,
                "recipient": recipient,
                "kind": item["event"].get("kind"),
                "scope": deepcopy(self.scope),
            }
            for identifier, item in self.store.get("wake_outbox", {}).items()
            for recipient, state in item["targets"].items()
            if state == "pending"
        ]

    def confirm(self, receipt: str, recipient: str) -> None:
        """Caller must already possess a checked transport acceptance/duplicate."""
        with self.store.lock:
            outbox = self.store.get("wake_outbox", {})
            item = outbox.get(receipt)
            if item is None or recipient not in item["targets"]:
                raise StoreError("Unknown receipt or wake recipient")
            if item["targets"][recipient] == "confirmed":
                return
            item["targets"][recipient] = "confirmed"
            self.store.commit(
                "wake_confirmed",
                {"receipt": receipt, "recipient": recipient},
                updates={"wake_outbox": outbox},
            )

    def flush(self, send: Callable[[JsonObject], JsonObject]) -> None:
        """Callback reuses receipt identity; lost/invalid replies remain pending."""
        for wake in self.pending():
            result = send(deepcopy(wake))
            if (
                not isinstance(result, dict)
                or result.get("status") not in ("accepted", "duplicate")
                or result.get("receipt") != wake["receipt"]
                or result.get("recipient") != wake["recipient"]
            ):
                raise StoreError(
                    "Wake transport did not confirm the same receipt and recipient"
                )
            self.confirm(wake["receipt"], wake["recipient"])

    def recover(self, room: HTTPRoom, *, limit: int = 128) -> JsonObject:
        """Atomically save authoritative events and cursor, never a wake payload."""
        with self.store.lock:
            cursor = self.store.get("wake_cursor", 0)
            page = room.inbox(cursor, limit=limit)
            after = page.get("cursor")
            if (
                type(after) is not int
                or not cursor <= after <= 9_007_199_254_740_991
                or not isinstance(page.get("events"), list)
                or len(page["events"]) > limit
            ):
                raise StoreError("Invalid recovery inbox page")
            inbox = self.store.get("wake_inbox", {})
            for raw in page["events"]:
                event = self._event(raw)
                if self.scope["actor"] not in event["recipients"]:
                    raise StoreError("Inbox receipt is not addressed to this actor")
                prior = inbox.get(event["id"])
                if prior is not None and prior["event"] != event:
                    raise StoreError("Conflicting inbox receipt reuse")
                if prior is None:
                    inbox[event["id"]] = {"event": event, "state": "pending"}
            if after != cursor or inbox != self.store.get("wake_inbox", {}):
                self.store.commit(
                    "wake_inbox_page",
                    {"cursor": after},
                    updates={"wake_cursor": after, "wake_inbox": inbox},
                )
            return {
                "cursor": after,
                "pending": [
                    identifier
                    for identifier, item in inbox.items()
                    if item["state"] == "pending"
                ],
            }

    def claim(self, receipt: str) -> JsonObject:
        """Durably claim before effects. Restart/duplicate sees held, not new work."""
        with self.store.lock:
            inbox = self.store.get("wake_inbox", {})
            item = inbox.get(receipt)
            if item is None:
                raise StoreError(
                    "Receipt absent from recovered authority inbox; poll again"
                )
            if item["state"] == "completed":
                return {"status": "duplicate", "receipt": receipt}
            if item["state"] == "claimed":
                return {"status": "held", "receipt": receipt}
            item["state"] = "claimed"
            self.store.commit(
                "wake_claimed", {"receipt": receipt}, updates={"wake_inbox": inbox}
            )
            return {
                "status": "claimed",
                "receipt": receipt,
                "event": deepcopy(item["event"]),
            }

    def complete(self, receipt: str) -> None:
        """Host assertion: effects durably completed, or independently reconciled."""
        with self.store.lock:
            inbox = self.store.get("wake_inbox", {})
            item = inbox.get(receipt)
            if item is None or item["state"] == "pending":
                raise StoreError("Only claimed work can be completed")
            if item["state"] == "completed":
                return
            item["state"] = "completed"
            self.store.commit(
                "wake_completed", {"receipt": receipt}, updates={"wake_inbox": inbox}
            )

    def reconcile(self, receipt: str, decision: str, evidence: str) -> None:
        """Explicit host decision, not an automatic crash retry or ownership fence.

        Before retry, the host MUST fence the prior worker and verify no effect
        (or that the destination makes the same receipt idempotent). Evidence is
        retained, not validated as factual truth by this storage layer.
        """
        if (
            decision not in ("retry", "completed")
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            raise StoreError(
                "Held-work reconciliation requires a decision and evidence"
            )
        with self.store.lock:
            inbox = self.store.get("wake_inbox", {})
            item = inbox.get(receipt)
            if item is None or item["state"] != "claimed":
                raise StoreError("Only held claimed work can be reconciled")
            item["state"] = "pending" if decision == "retry" else "completed"
            self.store.commit(
                "wake_reconciled",
                {"receipt": receipt, "decision": decision, "evidence": evidence},
                updates={"wake_inbox": inbox},
            )
