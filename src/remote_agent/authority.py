"""Durable room authority. Decisions and private rejection audits precede replies."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from remote_agent.storage import canonical, digest
from remote_agent.transport import ReferenceRoom, TransportError, reference_room_policy
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store
    from remote_agent.transport import EngineProcess

# Member operations on the hosted endpoint; each needs fresh host evidence (D-15).
MEMBER_OPERATIONS = frozenset({"new", "step", "view", "stop_contribution"})


def audit_label(value: object) -> str | None:
    """Retain bounded identity metadata, never stringify invalid payload objects."""
    if isinstance(value, str):
        try:
            if 0 < len(value.encode("utf-8")) <= 256:
                return value
        except UnicodeError:
            pass
    return None


class RoomClock:
    """UTC milliseconds with a monotonic floor during this process's lifetime."""

    def __init__(self, floor: int = 0) -> None:
        self.value_ns = max(floor * 1_000_000, time.time_ns())
        self.monotonic = time.monotonic_ns()

    def __call__(self) -> int:
        current = time.monotonic_ns()
        self.value_ns = max(self.value_ns + current - self.monotonic, time.time_ns())
        self.monotonic = current
        return self.value_ns // 1_000_000


class DurableRoom(ReferenceRoom):
    idempotent = True

    # ReferenceRoom.CAPABILITIES are stated on new and restore when the configuration states none.
    # A stated list is pinned in the room configuration; an unstated one is not, so older rooms
    # still restore.

    def __init__(
        self,
        store: Store,
        engine: EngineProcess,
        *,
        session: str,
        bindings: dict[str, str],
        clock: Callable[[], int] | None = None,
        inactivity: str = "accepted",
        max_messages: int = 100,
        max_bytes: int = 16_384,
        snapshot_every: int = 20,
        capabilities: Sequence[str] | None = None,
    ) -> None:
        self.store, self.engine, self.session = store, engine, session
        self.lock, self.failed = threading.RLock(), False
        self.clock = clock or RoomClock(store.get("room_now", 0))
        self.observed_now = store.get("room_now", 0)
        self.snapshot_every = snapshot_every
        if type(snapshot_every) is not int or snapshot_every <= 0:
            raise TransportError("Invalid room snapshot interval")
        self.config: JsonObject = {
            "profile": "remote-agent/durable-room/1",
            "identity": self.identity,
            "session": session,
            "bindings": bindings,
            "inactivity": inactivity,
            "max_messages": max_messages,
            "max_bytes": max_bytes,
            "snapshot_every": snapshot_every,
        }
        if capabilities is not None:
            self.config["capabilities"] = list(capabilities)
        self.capabilities = list(
            self.CAPABILITIES if capabilities is None else capabilities
        )
        prior = store.get("room_config")
        try:
            if prior is None:
                if store.records():
                    raise TransportError("The database belongs to a different service")
                with self.lock:
                    now = self._now()
                    engine.request(
                        op="new",
                        session=session,
                        bindings=bindings,
                        now=now,
                        inactivity=inactivity,
                        max_messages=max_messages,
                        max_bytes=max_bytes,
                        room_policy=self._room_policy(),
                        capabilities=self.capabilities,
                    )
                    summary = engine.request(op="summary")
                    store.commit(
                        "room_initialize",
                        {"config": self.config},
                        updates={
                            "room_config": self.config,
                            "room_now": now,
                            "room_operations": 0,
                            "room_summary": summary,
                        },
                        snapshot=self._snapshot(summary),
                    )
            else:
                if prior != self.config:
                    raise TransportError(
                        "Pinned room configuration changed; migration is required"
                    )
                self._restore()
                # Time spent offline still counts toward the original deadlines.
                self.tick()
        except BaseException:
            self.failed = True
            engine.close(force=True)
            raise

    def _now(self) -> int:
        now = self.clock()
        if type(now) is not int or not 0 <= now <= 9_007_199_254_740_991:
            raise TransportError("Invalid authoritative clock")
        self.observed_now = max(now, self.observed_now)
        return self.observed_now

    def _snapshot(self, summary: JsonObject) -> JsonObject:
        return {
            "program": digest(self.config),
            "schema": "room/1",
            "state": {
                "checkpoint": self.engine.request(op="checkpoint"),
                "now": summary["now"],
            },
        }

    def _restore(self) -> None:
        snapshot = self.store.latest_snapshot()
        if (
            not snapshot
            or snapshot["program"] != digest(self.config)
            or snapshot["schema"] != "room/1"
        ):
            raise TransportError("Missing or mismatched durable room checkpoint")
        self.engine.request(
            op="restore", capabilities=self.capabilities, **snapshot["state"]
        )
        for frame in self.store.records(after=snapshot["sequence"]):
            # Audit attempts do not occupy accepted IDs or mutate the reducer.
            # Store verifies their immutable hash chain; never resubmit them.
            if frame["kind"] == "room_rejection":
                continue
            if frame["kind"] != "room_operation":
                raise TransportError("Unexpected room journal entry")
            data = frame["data"]
            observed = self.engine.request(**data["request"])
            if canonical(observed) != canonical(data["result"]):
                raise TransportError("Room replay disagrees with the recorded decision")
        if self.engine.request(op="summary") != self.store.get("room_summary"):
            raise TransportError("Room checkpoint and journal state disagree")

    def _room_policy(self) -> JsonObject:
        # Membership is the pinned room config's bindings, read on every call.
        return reference_room_policy(self.config["bindings"])

    def _execute(self, operation: str, **payload: Any) -> JsonObject:
        with self.lock:
            if self.failed:
                raise TransportError(
                    "Room requires recovery after an uncommitted operation"
                )
            request: JsonObject = dict(payload, op=operation, now=self._now())
            if operation in MEMBER_OPERATIONS:
                # Built under the commit lock and journaled with the request, so replay
                # reproduces it; never taken from a caller's payload.
                request["room_policy"] = self._room_policy()
            try:
                result = self.engine.request(**request)
                summary = self.engine.request(op="summary")
                previous = self.store.get("room_summary")
                if summary["version"] != previous["version"]:
                    count = self.store.get("room_operations") + 1
                    snapshot = (
                        self._snapshot(summary)
                        if count % self.snapshot_every == 0
                        else None
                    )
                    saved_request, saved_result = request, result
                    if operation == "step" and result.get("status") == "rejected":
                        # A rejected proposal can still cause an overdue timer to
                        # fire. Persist only that tick, not its private payload.
                        saved_request = {"op": "tick", "now": request["now"]}
                        saved_result = self.engine.request(**saved_request)
                    self.store.commit(
                        "room_operation",
                        {"request": saved_request, "result": saved_result},
                        updates={
                            "room_now": summary["now"],
                            "room_summary": summary,
                            "room_operations": count,
                        },
                        snapshot=snapshot,
                    )
                if operation == "step" and result.get("status") == "rejected":
                    message = payload.get("message")
                    message = message if isinstance(message, dict) else {}
                    self.store.commit(
                        "room_rejection",
                        {
                            "id": audit_label(message.get("id")),
                            "actor": audit_label(payload.get("actor")),
                            "kind": audit_label(message.get("kind")),
                            "reason": result["reason"],
                            "now": request["now"],
                        },
                        updates={"room_now": request["now"]},
                    )
                # Ordinary reads, exact accepted retries and idle timer polls
                # do not consume journal capacity. Rejected attempts do, but
                # never increment the protocol version or accepted-op count.
                return result
            except BaseException:
                # Never expose candidate state after a failed database commit. Recovery
                # uses the committed checkpoint/journal in a fresh engine process.
                self.failed = True
                self.engine.close(force=True)
                raise

    def view(self, actor: str, now: int | None = None) -> JsonObject:
        return self._execute("view", actor=actor)

    def publish(
        self, message: JsonObject, actor: str, now: int | None = None
    ) -> JsonObject:
        return self._execute("step", message=message, actor=actor)

    def tick(self) -> JsonObject:
        return self._execute("tick")

    def status(self) -> JsonObject:
        with self.lock:
            return {
                "ready": not self.failed and not self.engine.closed,
                "operations": self.store.get("room_operations"),
                "room": self.store.get("room_summary"),
            }
