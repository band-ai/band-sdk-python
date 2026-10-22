"""Remote program/LLM routing with room-authoritative output and SQLite recovery."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from remote_agent.hostkey import HostKey
from remote_agent.interfaces import InboxReader, PreparedProvider
from remote_agent.invocations import InvocationJournal
from remote_agent.modeljournal import ModelFailure, ModelJournal, ModelUnresolved
from remote_agent.program import Artifact, Program, ProgramError
from remote_agent.protocol import check_depth
from remote_agent.storage import StoreError, canonical, digest
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.interfaces import CandidateValidator, ModelProvider
    from remote_agent.store import Store
    from remote_agent.transport import LocalGate, ReferenceRoom


logger = logging.getLogger(__name__)


def copy_json(value: JsonObject) -> JsonObject:
    return json.loads(canonical(value))


class AgentError(Exception):
    pass


class HistoryQueryRejected(AgentError):
    """A definitive model result requested unusable history."""


class CallbackTimeout(AgentError):
    uncertain = True


class ControlError(AgentError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class HybridAgent:
    def __init__(
        self,
        store: Store,
        program: Program,
        room: ReferenceRoom,
        gate: LocalGate,
        provider: ModelProvider,
        *,
        actor: str,
        artifact_directory: SourcePath,
        now: Callable[[], int] | None = None,
        snapshot_every: int = 10,
        max_pending: int = 16,
        max_model_calls: int = 100,
        max_repairs: int = 3,
        max_attempts: int = 3,
        model_timeout: float = 30,
        validator: CandidateValidator | None = None,
        control_actors: Mapping[str, Sequence[str]] | None = None,
        initial_route: str | None = None,
    ) -> None:
        if initial_route not in (None, "program", "llm"):
            raise AgentError("Invalid initial execution route")
        self.invocations = InvocationJournal(store)
        self.model_journal = ModelJournal(store)
        program = Program(
            program.artifact, timeout=program.timeout, journal=self.invocations
        )
        self.store, self.program, self.room, self.gate, self.provider = (
            store,
            program,
            room,
            gate,
            provider,
        )
        self.actor, self.now = actor, now or (lambda: time.monotonic_ns() // 1_000_000)
        self.artifact_directory = Path(artifact_directory).resolve()
        self.validator, self.model_timeout = validator, model_timeout
        actions = {"wake", "takeover", "resume", "repair", "retry", "reconcile"}
        self.control_actors = {
            name: sorted(set(grants)) for name, grants in (control_actors or {}).items()
        }
        if any(
            not isinstance(name, str) or not set(grants) <= actions
            for name, grants in self.control_actors.items()
        ):
            raise AgentError("Invalid host control grants")
        self.lock, self.jobs_lock = threading.RLock(), threading.Lock()
        self.jobs: dict[str, threading.Thread] = {}
        self.repair_thread: threading.Thread | None = None
        self.closed = False
        self.stopping = threading.Event()
        self.limits = {
            "snapshot_every": snapshot_every,
            "max_pending": max_pending,
            "max_model_calls": max_model_calls,
            "max_repairs": max_repairs,
            "max_attempts": max_attempts,
        }
        if (
            any(type(n) is not int or n < 0 for n in self.limits.values())
            or not snapshot_every
            or not max_pending
            or not max_attempts
            or model_timeout <= 0
        ):
            raise AgentError("Invalid controller limits")
        if gate.engine.identity != room.identity:
            raise AgentError("Local checker and room contract identities differ")
        if room.idempotent is not True:
            raise AgentError("Idempotent room authority required")
        self.host_key = HostKey(store)
        self.host_key.assert_hidden(program.artifact)
        self.host_key.ensure()
        projection = room.view(actor, self.now())
        self.initial_view = projection["view"]
        config = {
            "actor": actor,
            "session": room.session,
            "contract": room.identity,
            "initial_program": program.artifact.to_dict(),
            "limits": self.limits,
            "program_timeout_ms": round(program.timeout * 1000),
            "model_timeout_ms": round(model_timeout * 1000),
            "idempotent_room": room.idempotent,
            "model_journal": ModelJournal.PROFILE,
            "control_actors": self.control_actors,
            "artifact_directory": str(self.artifact_directory),
        }
        # Omitted settings retain the exact configuration identity of existing journals.
        if initial_route is not None:
            config["initial_route"] = initial_route
        prior = store.get("config")
        if prior is None:
            self._check_artifact(program.artifact)
            pending = store.get("pending_configuration")
            if pending is None or pending["config"] != config:
                pending = {"config": config, "view": self.initial_view}
                store.commit(
                    "configure", pending, updates={"pending_configuration": pending}
                )
            self.initial_view = pending["view"]
            # Describe has no external effect. Until initialize commits, boot may
            # retry under a fresh identity, retaining failed/interrupted attempts.
            metadata = program.describe(
                self.initial_view,
                invocation_id="initialize:" + digest(config) + ":" + uuid.uuid4().hex,
                purpose="initialize",
            )
            state = metadata["initial"]
            if (
                initial_route == "program"
                and metadata["coverage"]["mode"] == "llm_only"
            ):
                raise AgentError(
                    "Cannot force an LLM-only participant onto the program route"
                )
            mode = initial_route or (
                "llm" if metadata["coverage"]["mode"] == "llm_only" else "program"
            )
            store.commit(
                "initialize",
                {"config": config, "metadata": metadata, "view": self.initial_view},
                updates={
                    "config": config,
                    "program": program.artifact.to_dict(),
                    "metadata": metadata,
                    "state": state,
                    "mode": mode,
                    "epoch": 0,
                    "turns": 0,
                    "queue": [],
                    "staged": None,
                    "fault": None,
                    "quarantined": False,
                    "model_calls": 0,
                    "repair_calls": 0,
                    "repair": None,
                    "execution": None,
                    "pending_configuration": None,
                },
                snapshot=self._snapshot(state, metadata, program),
            )
        else:
            if config != prior:
                raise AgentError(
                    "Pinned host configuration changed; explicit migration is required"
                )
            self.initial_view = self._initialization()["view"]
            self.program = Program(
                Artifact(**store.get("program")),
                timeout=program.timeout,
                journal=self.invocations,
            )
            self._check_artifact(self.program.artifact)
            self._restore_state()
            repair = store.get("repair")
            if repair and repair["status"] == "running":
                recorded = store.get("model:" + repair["id"])
                status = "requested"
                if recorded:
                    if recorded["status"] != "completed":
                        status = "interrupted"
                    else:
                        try:
                            ModelJournal.result(recorded["execution"])
                        except ModelUnresolved:
                            status = "interrupted"
                        except ModelFailure:
                            status = "rejected"
                # No input record proves no dispatch; a definitive output permits cached validation.
                store.commit(
                    "repair_recovered",
                    {"status": status},
                    updates={"repair": dict(repair, status=status, reserved=True)},
                )

    def _check_artifact(self, artifact: Artifact) -> None:
        self.host_key.assert_hidden(artifact)
        if not Path(artifact.path).resolve().is_relative_to(self.artifact_directory):
            raise AgentError(
                "Program artifact is outside the pinned artifact directory"
            )
        artifact.verify()

    def _initialization(self) -> JsonObject:
        anchor = self.store.anchor()
        if anchor:
            return anchor["initialization"]
        records = [r["data"] for r in self.store.records() if r["kind"] == "initialize"]
        if len(records) != 1:
            raise AgentError("Missing or ambiguous agent initialization")
        return records[0]

    def _snapshot(
        self,
        state: JsonObject,
        metadata: JsonObject | None = None,
        program: Program | None = None,
    ) -> JsonObject:
        metadata = metadata or self.store.get("metadata")
        program = program or self.program
        return {
            "program": program.artifact.sha256,
            "schema": metadata["schema"],
            "state": state,
        }

    def _restore_state(self) -> None:
        snapshot = self.store.latest_snapshot()
        if snapshot is None:
            raise AgentError("Missing initial snapshot")
        state = snapshot["state"]
        for record in self.store.records(after=snapshot["sequence"]):
            if record["kind"] == "turn":
                state = record["data"]["state"]
        if state != self.store.get("state"):
            raise AgentError(
                "Snapshot and journal recovery differs from committed state"
            )

    def close(self) -> None:
        self.request_stop()
        with self.lock:
            self.closed = True
        thread = self.repair_thread
        if thread and thread is not threading.current_thread():
            thread.join(
                max(
                    self.model_timeout,
                    self.program.timeout,
                    getattr(self.room, "timeout", 0),
                )
                + 1
            )
            if thread.is_alive():
                raise AgentError(
                    "Repair shutdown timed out; retain the store until it finishes"
                )

    def request_stop(self) -> None:
        """Stop starting work without waiting for a currently bounded callback."""
        self.stopping.set()

    def _ensure_open(self) -> None:
        if self.closed or self.stopping.is_set():
            raise AgentError("Controller is closed")

    def _held(self, reason: object) -> JsonObject:
        return {"status": "held", "reason": str(reason)[:2000]}

    def receive(self, event: JsonObject) -> JsonObject:
        """Admit a verified fact from the single ordered inbox consumer."""
        with self.lock:
            self._ensure_open()
            event = self.room.accepted_input(event, self.actor, self.now())
            if event["actor"] == self.actor:
                return {"status": "ignored", "reason": "self_authored"}
            key, fingerprint = "input:" + event["id"], digest(event)
            prior = self.store.get(key)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise AgentError("Conflicting duplicate input")
                if prior["result"] is not None:
                    return prior["result"]
            else:
                input_at = self._input_time(event)
                if self._expired_input(event, input_at):
                    return {"status": "expired", "reason": "outside_retained_history"}
                pending = self.store.get("queue")
                if len(pending) >= self.limits["max_pending"]:
                    raise AgentError("Pending input capacity exhausted")
                self.store.commit(
                    "input",
                    event,
                    updates={
                        key: {
                            "event": event,
                            "fingerprint": fingerprint,
                            "result": None,
                            "input_at": input_at,
                        },
                        "queue": pending + [event["id"]],
                        self._input_history_key(event["id"]): [],
                    },
                )
            result = self._drain()
            requested = self.store.get(key)["result"]
        self._kick_repair()
        return requested or result

    def notify(self, event: JsonObject) -> JsonObject:
        """Verify a push hint without admitting input or executing a turn."""
        with self.lock:
            self._ensure_open()
            event = self.room.accepted_input(event, self.actor, self.now())
            if event["actor"] == self.actor:
                return {"status": "ignored", "reason": "self_authored"}
            prior = self.store.get("input:" + event["id"])
            if prior and prior["fingerprint"] != digest(event):
                raise AgentError("Conflicting duplicate input")
            if not prior and self._expired_input(event, self._input_time(event)):
                return {"status": "expired", "reason": "outside_retained_history"}
            return {"status": "notified"}

    def _input_time(self, event: JsonObject) -> int | None:
        # Re-read authority-owned fact time; caller timestamps never establish
        # a retention position. Normalized input bodies remain backwards compatible.
        fact = (
            self.room.view(self.actor, self.now())["view"]
            .get("facts", {})
            .get(event["id"], {})
        )
        at = fact.get("at")
        return at if type(at) is int and at >= 0 else None

    def _expired_input(self, event: JsonObject, at: int | None) -> bool:
        anchor = self.store.anchor()
        if not anchor:
            return False
        if at is None:
            raise AgentError("Authority fact has no usable retention timestamp")
        floor = anchor["input_floor"]
        return at < floor["at"] or (
            at == floor["at"] and digest(event["id"]) in floor["ids"]
        )

    def retain_history(
        self, *, target_bytes: int = 100_000_000, minimum_steps: int = 100
    ) -> JsonObject:
        with self.lock:
            self._ensure_open()
            return self.store.retain(
                target_bytes=target_bytes, minimum_steps=minimum_steps
            )

    def recover(self) -> JsonObject:
        with self.lock:
            self._ensure_open()
            result = self._drain()
        self._kick_repair()
        return result

    def status(self) -> JsonObject:
        with self.lock:
            return {
                "mode": self.store.get("mode"),
                "turns": self.store.get("turns"),
                "pending": len(self.store.get("queue")),
                "model_calls": self.store.get("model_calls"),
                "repair": self.store.get("repair"),
                "quarantined": self.store.get("quarantined"),
            }

    def control(self, actor: str, command: JsonObject) -> JsonObject:
        """Actor comes from authenticated transport, never from the command body."""
        if (
            not isinstance(command, dict)
            or set(command) != {"id", "action", "reason"}
            or not isinstance(command["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", command["id"])
            or command["action"]
            not in ("wake", "takeover", "resume", "repair", "retry", "reconcile")
            or not isinstance(command["reason"], str)
            or not 1 <= len(command["reason"]) <= 2000
        ):
            raise ControlError("Invalid control envelope")
        with self.lock:
            self._ensure_open()
            action = command["action"]
            if action not in self.control_actors.get(actor, []):
                raise ControlError("Actor lacks this control grant", 403)
            key, fingerprint = "control:" + command["id"], digest([actor, command])
            prior = self.store.get(key)
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise ControlError("Conflicting duplicate control", 409)
                return prior["result"]
            pending = {
                "fingerprint": fingerprint,
                "result": self._held(
                    "Control outcome pending; do not repeat activation"
                ),
            }
            self.store.commit(
                "control_received", {"actor": actor, **command}, updates={key: pending}
            )
            try:
                if action == "reconcile":
                    result = self._reconcile_inbox(command, key, pending)
                else:
                    view = self.room.view(self.actor, self.now())["view"]
                    bundle = dict(
                        self._bundle(view=view), control={"actor": actor, **command}
                    )
                    if action in ("wake", "takeover"):
                        if action == "takeover":
                            self._mode("llm", command["reason"], abandon_program=True)
                        reply = self._call_model(
                            "wake", bundle, identifier=key + ":wake"
                        )
                        requested = reply.get("action")
                        if requested not in (
                            "none",
                            "takeover",
                            "repair",
                        ) or not isinstance(reply.get("note"), str):
                            raise AgentError("Invalid wake response")
                        if (
                            requested != "none"
                            and requested not in self.control_actors[actor]
                        ):
                            raise AgentError(
                                "Model request exceeds the control actor's grant"
                            )
                        if requested == "takeover" and action != "takeover":
                            self._mode(
                                "llm",
                                "Explicit wake requested takeover",
                                abandon_program=True,
                            )
                        if requested == "repair":
                            self._request_repair(
                                self.store.get("fault")
                                or {
                                    "reason": command["reason"],
                                    "input": None,
                                    "needs_handled": False,
                                }
                            )
                        result = {
                            "status": "completed",
                            "action": action,
                            "reply": reply,
                            "mode": self.store.get("mode"),
                        }
                    elif action == "resume":
                        if self.store.get("staged") or self.store.get("execution"):
                            raise AgentError(
                                "Reconcile the pending execution or proposal before resuming"
                            )
                        if self.store.get("quarantined"):
                            raise AgentError(
                                "Install a validated repair before resuming the quarantined program"
                            )
                        metadata, verdict = self._validate_candidate(
                            self.program, bundle, self._history()
                        )
                        if metadata["coverage"]["mode"] == "llm_only":
                            raise AgentError(
                                "This participant declares LLM-only coverage"
                            )
                        self._mode("program", command["reason"])
                        result = {
                            "status": "completed",
                            "action": action,
                            "checks": verdict["checks"],
                            "mode": "program",
                        }
                    elif action == "repair":
                        if self.store.get("repair_calls") >= self.limits["max_repairs"]:
                            raise AgentError("Repair call budget exhausted")
                        self._request_repair(
                            self.store.get("fault")
                            or {
                                "reason": command["reason"],
                                "input": None,
                                "needs_handled": False,
                            }
                        )
                        result = {
                            "status": "completed",
                            "action": action,
                            "mode": self.store.get("mode"),
                        }
                    else:
                        # Retry keeps all attempt limits and any unresolved proposal intact.
                        self._retry_execution(command, view=view)
                        result = self._drain()
            except Exception as error:  # noqa: BLE001 -- Explicit controls retain their recorded outcome for every callback failure.
                result = dict(self._held(error), mode=self.store.get("mode"))
            if action != "reconcile" or result["status"] != "completed":
                self.store.commit(
                    "control_completed",
                    {"id": command["id"], "result": result},
                    updates={key: dict(pending, result=result)},
                )
        self._kick_repair()
        return result

    def _reconcile_inbox(
        self, command: JsonObject, key: str, pending: JsonObject
    ) -> JsonObject:
        hold = self.store.get("inbox_hold")
        if not hold:
            raise AgentError("No inbox history hold to reconcile")
        if (hold["session"], hold["actor"]) != (self.room.session, self.actor):
            raise AgentError("Inbox hold identity differs from the pinned participant")
        if (
            self.store.get("staged")
            or self.store.get("execution")
            or self.store.get("queue")
        ):
            raise AgentError(
                "Reconcile pending inputs, executions and proposals before inbox history"
            )
        # This authorized control attests host reconciliation. Position 0 is
        # always legal; arbitrary calculated/reset positions are never accepted.
        # Probe availability, but do not admit or acknowledge the returned page.
        if not isinstance(self.room, InboxReader):
            raise AgentError("Room authority does not support inbox reconciliation")
        self.room.inbox(0, limit=1)
        result = {"status": "completed", "action": "reconcile", "cursor": 0}
        self.store.commit(
            "inbox_reconciled",
            {"control": command["id"], "hold": hold},
            updates={
                "inbox_hold": None,
                "inbox_cursor": 0,
                key: dict(pending, result=result),
            },
        )
        return result

    def _drain(self) -> JsonObject:
        last = {"status": "idle"}
        while self.store.get("queue"):
            if self.stopping.is_set():
                return self._held("Controller is stopping; input retained")
            staged = self.store.get("staged")
            if staged:
                dispatched = self._dispatch(staged, reconcile=True)
                if dispatched is None:
                    continue
                last = dispatched
            else:
                event = self.store.get("input:" + self.store.get("queue")[0])["event"]
                last = self._turn(event)
            if last["status"] == "held":
                return last
        return last

    def _turn(self, event: JsonObject) -> JsonObject:
        key = "attempts:" + event["id"]
        while True:
            if self.stopping.is_set():
                return self._held("Controller is stopping; execution retained")
            try:
                execution = self.store.get("execution")
                admission_view = self.room.view(self.actor, self.now())["view"]
                if admission_view["status"] == "terminal":
                    return self._discard_terminal(
                        event, self.store.get("state"), admission_view
                    )
                if execution is None:
                    # Exhaustion forbids another response, not authoritative terminal cleanup.
                    if self.store.get(key, 0) >= self.limits["max_attempts"]:
                        return self._held("Response attempt budget exhausted")
                    before = self.store.get("state")
                    attempt = self.store.get(key, 0) + 1
                    view = admission_view
                    producer = "llm" if self.store.get("mode") == "llm" else "program"
                    execution = {
                        "id": "turn:"
                        + digest([self.room.session, self.actor, event["id"], attempt]),
                        "input": event["id"],
                        "attempt": attempt,
                        "before": before,
                        "view": view,
                        "producer": producer,
                        "program": self.program.artifact.sha256,
                    }
                    self.store.commit(
                        "attempt",
                        execution,
                        updates={key: attempt, "execution": execution},
                    )
                else:
                    if (
                        execution["input"] != event["id"]
                        or execution["program"] != self.program.artifact.sha256
                    ):
                        raise AgentError(
                            "Pending execution does not match the selected input and program"
                        )
                    before, attempt, view, producer = (
                        execution[k] for k in ("before", "attempt", "view", "producer")
                    )
                response_id = None
                result: JsonObject | None = None
                if producer == "program":
                    try:
                        request = self.program.request(
                            "handle", event=event, state=before, view=view
                        )
                        result = Program.result(
                            self.invocations.run(
                                self.program,
                                request,
                                invocation_id=execution["id"],
                                purpose="turn",
                            )
                        )
                    except ProgramError as error:
                        self._fault(
                            "program_failure: " + str(error), event, repair=True
                        )
                        self._clear_execution(execution, "program_failure")
                        continue
                    if result["type"] == "delegate":
                        execution = dict(execution, producer="llm")
                        updates: JsonObject = {"execution": execution}
                        epoch = self.store.get("epoch")
                        if result["scope"] == "ongoing":
                            epoch += 1
                            updates.update(mode="llm", epoch=epoch)
                        if result["repair"]:
                            repair = self._planned_repair(
                                {
                                    "reason": result["reason"],
                                    "input": event,
                                    "needs_handled": False,
                                },
                                epoch=epoch,
                            )
                            if repair:
                                updates["repair"] = repair
                        self.store.commit(
                            "delegate",
                            {"input": event["id"], **result},
                            updates=updates,
                        )
                        producer = "llm"
                if producer == "llm":
                    try:
                        result, response_id = self._respond_with_history(
                            self._bundle(event, view), execution["id"] + ":respond"
                        )
                    except HistoryQueryRejected as error:
                        self.store.commit(
                            "llm_rejected",
                            {
                                "input": event["id"],
                                "reason": "invalid_history_query: " + str(error)[:2000],
                            },
                        )
                        self._clear_execution(execution, "invalid_history_query")
                        continue
                    except ModelUnresolved:
                        raise
                    except Exception:
                        self._clear_execution(execution, "model_failure")
                        raise
                    try:
                        if not isinstance(result, dict) or set(result) - {
                            "state",
                            "message",
                            "scope",
                        }:
                            raise AgentError("Invalid LLM reply fields")
                        if result.get("scope", "turn") not in ("turn", "ongoing"):
                            raise AgentError("Invalid LLM takeover scope")
                        self._valid_result(dict(result, type="handled", snapshot=None))
                    except (
                        AgentError,
                        StoreError,
                        KeyError,
                        TypeError,
                        ValueError,
                        RecursionError,
                    ) as error:
                        # A definitive but off-schema reply is this attempt's outcome. Count it and
                        # clear it, so the journaled reply is never replayed into a held turn forever.
                        self.store.commit(
                            "llm_rejected",
                            {
                                "input": event["id"],
                                "reason": "invalid_model_reply: " + str(error)[:2000],
                            },
                        )
                        self._clear_execution(execution, "invalid_model_reply")
                        continue
                    if (
                        result.get("scope") == "ongoing"
                        and self.store.get("mode") != "llm"
                    ):
                        self._mode("llm", "LLM requested ongoing control")
                    result = dict(result, type="handled", snapshot=None)
                if result is None:
                    raise AgentError("Execution has no valid producer result")
                try:
                    self._valid_result(result)
                except StoreError as error:
                    if producer != "program":
                        raise
                    self._fault("program_failure: " + str(error), event, repair=True)
                    self._clear_execution(execution, "program_failure")
                    continue
                # Refresh after callback latency. The authority checks again on publication.
                projection = self.room.view(self.actor, self.now())
                if projection["view"]["status"] == "terminal":
                    return self._discard_terminal(
                        event, before, projection["view"], response_id
                    )
                message = result["message"]
                envelope = None
                if message is not None:
                    envelope = {
                        "id": self.host_key.identifier(
                            self.room.session, self.actor, event["id"], attempt
                        ),
                        "session": self.room.session,
                        "kind": message["kind"],
                        "payload": message["payload"],
                        "expected_version": projection["view"]["version"],
                    }
                    decision = self.gate.check(
                        projection, envelope, self.actor, self.now()
                    )
                    if decision["status"] != "accepted":
                        reason = "local_contract: " + str(
                            decision.get("reason", decision["status"])
                        )
                        if producer == "program":
                            self._fault(reason, event, repair=True)
                        else:
                            self.store.commit(
                                "llm_rejected", {"input": event["id"], "reason": reason}
                            )
                        self._clear_execution(execution, "local_rejection")
                        continue
                elif projection["view"]["obligations"]:
                    if producer == "program":
                        self._fault(
                            "local_contract: response obligation left unanswered",
                            event,
                            repair=True,
                        )
                    else:
                        self.store.commit(
                            "llm_rejected",
                            {
                                "input": event["id"],
                                "reason": "response obligation unanswered",
                            },
                        )
                    self._clear_execution(execution, "unanswered_obligation")
                    continue
                staged = {
                    "input": event,
                    "before": before,
                    "state": result["state"],
                    "message": message,
                    "envelope": envelope,
                    "producer": producer,
                    "view": view,
                    "snapshot": result.get("snapshot") is not None,
                    "attempt": attempt,
                    "sends": 0,
                    "program": self.program.artifact.sha256,
                    "invocation": execution["id"],
                    "model_invocation": response_id,
                }
                try:
                    # Keep the proposal only in updates. Duplicating it in data
                    # can exceed the record cap even for bounded program I/O.
                    self.store.commit(
                        "stage",
                        {"input": event["id"], "invocation": execution["id"]},
                        updates={"staged": staged, "execution": None},
                    )
                except StoreError as error:
                    if producer != "program":
                        raise
                    # No proposal has been sent: this definitive program fault
                    # may abandon execution and hand the same input to the LLM.
                    self._fault("program_failure: " + str(error), event, repair=True)
                    self._clear_execution(execution, "program_failure")
                    continue
                dispatched = self._dispatch(staged)
                if dispatched is not None:
                    return dispatched
            except Exception as error:  # noqa: BLE001 -- Any failure after dispatch must preserve the staged proposal for reconciliation.
                # A store exception while finalizing a sent message leaves the staged outbox intact.
                # Never manufacture success, discard the proposal, or resample while it is unresolved.
                self.store.commit(
                    "turn_held", {"input": event["id"], "reason": str(error)[:2000]}
                )
                return self._held(error)

    def _discard_terminal(
        self,
        event: JsonObject,
        before: JsonObject,
        view: JsonObject,
        model_invocation: str | None = None,
    ) -> JsonObject:
        # A known terminal authority cannot accept a later answer. Pending room
        # acknowledgements take the reconciliation path before reaching here.
        # Ordinary expired duties can remain in the view as diagnostics.
        staged = {
            "input": event,
            "before": before,
            "state": before,
            "message": None,
            "envelope": None,
            "producer": "supervisor",
            "view": view,
            "snapshot": True,
            "attempt": 0,
            "sends": 0,
            "program": self.program.artifact.sha256,
            "invocation": None,
            "model_invocation": model_invocation,
        }
        self.store.commit(
            "terminal_input_discarded",
            {"input": event["id"], "outcome": view["outcome"]},
            updates={"execution": None, "staged": staged},
        )
        return self._finish(staged, None)

    def _clear_execution(self, execution: JsonObject, reason: str) -> None:
        self.store.commit(
            "execution_finished",
            {"id": execution["id"], "reason": reason},
            updates={"execution": None},
        )

    @staticmethod
    def _valid_result(result: JsonObject) -> None:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("state"), dict)
            or "message" not in result
        ):
            raise AgentError("Invalid reply state or message")
        message = result["message"]
        if message is not None and (
            not isinstance(message, dict)
            or set(message) != {"kind", "payload"}
            or not isinstance(message["kind"], str)
            or not message["kind"]
            or not isinstance(message["payload"], dict)
        ):
            raise AgentError("Invalid reply message")
        canonical(result)

    def _dispatch(
        self, staged: JsonObject, reconcile: bool = False
    ) -> JsonObject | None:
        if self.stopping.is_set():
            return self._held("Controller is stopping; proposal retained")
        if staged["envelope"] is None:
            return self._finish(staged, None)
        if staged["sends"] >= self.limits["max_attempts"]:
            return self._held("Room reconciliation budget exhausted")
        staged = dict(staged, sends=staged["sends"] + 1)
        self.store.commit(
            "publish_attempt",
            {"id": staged["envelope"]["id"], "attempt": staged["sends"]},
            updates={"staged": staged},
        )
        try:
            method = self.room.lookup if reconcile else self.room.publish
            decision = method(staged["envelope"], self.actor, self.now())
        except Exception as error:  # noqa: BLE001 -- Transport callbacks can fail after publication; retain the unknown outcome.
            self.store.commit(
                "publish_unknown",
                {"id": staged["envelope"]["id"], "reason": str(error)[:2000]},
            )
            return self._held(
                "Room acknowledgement unknown; proposal retained for reconciliation"
            )
        receipt = (
            decision["original"] if decision.get("status") == "duplicate" else decision
        )
        if receipt.get("status") == "accepted":
            if (
                receipt.get("id") != staged["envelope"]["id"]
                or receipt.get("session") != self.room.session
                or receipt.get("actor") != self.actor
                or receipt.get("kind") != staged["message"]["kind"]
                or receipt.get("payload") != staged["message"]["payload"]
            ):
                return self._held("Room receipt does not match the pending proposal")
            return self._finish(staged, receipt)
        if decision.get("status") != "rejected":
            return self._held("Room response was not an authoritative decision")
        self.store.commit(
            "room_rejected",
            {"input": staged["input"]["id"], "decision": decision},
            updates={"staged": None},
        )
        if decision.get("reason") == "terminal":
            view = self.room.view(self.actor, self.now())["view"]
            if view["status"] == "terminal":
                return self._discard_terminal(
                    staged["input"],
                    staged["before"],
                    view,
                    staged.get("model_invocation"),
                )
        self._fault(
            "room_rejected: " + str(decision.get("reason")),
            staged["input"],
            repair=False,
        )
        return None

    def _finish(self, staged: JsonObject, receipt: JsonObject | None) -> JsonObject:
        event, state = staged["input"], staged["state"]
        result = {
            "status": "completed",
            "producer": staged["producer"],
            "receipt": receipt,
        }
        key, turns = "input:" + event["id"], self.store.get("turns") + 1
        entry = dict(self.store.get(key), result=result)
        snapshot = (
            self._snapshot(state)
            if staged["producer"] == "llm"
            or staged["snapshot"]
            or turns % self.limits["snapshot_every"] == 0
            else None
        )
        self.store.commit(
            "turn",
            dict(staged, receipt=receipt, input_at=entry.get("input_at")),
            updates={
                "state": state,
                "turns": turns,
                "staged": None,
                key: entry,
                "queue": self.store.get("queue")[1:],
            },
            snapshot=snapshot,
        )
        return result

    def _mode(
        self,
        mode: str,
        reason: str,
        quarantined: bool | None = None,
        abandon_program: bool = False,
    ) -> None:
        updates = {"mode": mode, "epoch": self.store.get("epoch") + 1}
        data = {"mode": mode, "reason": reason}
        execution = self.store.get("execution")
        if (
            abandon_program
            and execution
            and execution["producer"] == "program"
            and not self.store.get("staged")
        ):
            updates["execution"] = None
            data["abandoned_execution"] = execution["id"]
        if quarantined is not None:
            updates["quarantined"] = quarantined
        self.store.commit("routing", data, updates=updates)

    def _retry_execution(
        self, command: JsonObject, *, view: JsonObject | None = None
    ) -> None:
        execution = self.store.get("execution")
        if not execution or self.store.get("staged"):
            return
        history_index = None
        if execution["producer"] == "program":
            prior = self.store.get("invocation:" + execution["id"])
            uncertain = prior and prior["status"] != "completed"
        else:
            identifier = execution["id"] + ":respond"
            prior = self.store.get("model:" + identifier)
            for index in (1, 2):
                response_id = self._response_identifier(
                    identifier,
                    index,
                    execution.get("history_retries", {}).get(str(index), 0),
                )
                followup = self.store.get("model:" + response_id)
                if followup is None:
                    break
                prior, history_index = followup, index
            uncertain = prior and prior["status"] != "completed"
            if prior and prior["status"] == "completed":
                try:
                    recorded = ModelJournal.result(prior["execution"])
                    # A complete capture can still hold no definitive result (an OpenAI 408/5xx
                    # after dispatch). Classify it as _call_model would, so retry can abandon it.
                    prepared = self.prepared_provider
                    if prepared is not None:
                        prepared.decode("respond", recorded)
                except ModelUnresolved:
                    uncertain = True
                except Exception as error:  # noqa: BLE001 -- A definitive provider decode failure is retired by the recorded turn.
                    logger.debug(
                        "Recorded model result will be retired: %s",
                        type(error).__name__,
                    )
            with self.jobs_lock:
                active = self.jobs.get("respond")
                if active and active.is_alive():
                    raise AgentError(
                        "Previous model callback is still running; cannot abandon it yet"
                    )
        if uncertain:
            if history_index is not None:
                # Terminal work needs retirement, not another response. The
                # callback-alive check above and staged reconciliation still apply.
                if view is not None and view["status"] == "terminal":
                    return
                key = "attempts:" + execution["input"]
                attempt = self.store.get(key, execution["attempt"])
                if attempt >= self.limits["max_attempts"]:
                    raise AgentError("Response attempt budget exhausted")
                retries = dict(execution.get("history_retries", {}))
                retries[str(history_index)] = retries.get(str(history_index), 0) + 1
                # Keep completed queries and their pinned pages. Only the unknown
                # followup gets a fresh identity under this explicit control.
                self.store.commit(
                    "model_response_retried",
                    {
                        "id": execution["id"],
                        "control": command["id"],
                        "reason": command["reason"],
                        "history_index": history_index,
                    },
                    updates={
                        "execution": dict(
                            execution, history_retries=retries, attempt=attempt + 1
                        ),
                        key: attempt + 1,
                    },
                )
            else:
                self.store.commit(
                    "execution_abandoned",
                    {
                        "id": execution["id"],
                        "control": command["id"],
                        "reason": command["reason"],
                        "producer": execution["producer"],
                    },
                    updates={"execution": None},
                )

    def _fault(self, reason: str, event: JsonObject, *, repair: bool) -> None:
        fault = {"reason": reason, "input": event, "needs_handled": True}
        self._mode("llm", reason, quarantined=repair or self.store.get("quarantined"))
        self.store.commit("fault", fault, updates={"fault": fault})
        if repair:
            self._request_repair(fault)

    def _request_repair(self, fault: JsonObject) -> None:
        repair = self._planned_repair(fault)
        if repair:
            self.store.commit("repair_requested", repair, updates={"repair": repair})

    def _planned_repair(
        self, fault: JsonObject, epoch: int | None = None
    ) -> JsonObject | None:
        active = self.store.get("repair")
        if active and active["status"] in ("requested", "running", "waiting"):
            return
        if self.store.get("repair_calls") >= self.limits["max_repairs"]:
            return
        return {
            "id": "repair:" + uuid.uuid4().hex,
            "status": "requested",
            "fault": fault,
            "epoch": self.store.get("epoch") if epoch is None else epoch,
        }

    def _bundle(
        self, event: JsonObject | None = None, view: JsonObject | None = None
    ) -> JsonObject:
        # Only the public role view is supplied; the opaque engine checkpoint is not model context.
        records, retained_count = self.store.tail_records(limit=30)
        tail = [self._context_record(record) for record in records]
        bundle = {
            "profile": "remote-agent/sidecar/1",
            "session": self.room.session,
            "actor": self.actor,
            "input": event,
            "state": self.store.get("state"),
            "view": view or self.initial_view,
            "mode": self.store.get("mode"),
            "fault": self.store.get("fault"),
            "coverage": self.store.get("metadata")["coverage"],
            "schema": self.store.get("metadata")["schema"],
            "program": self.store.get("program"),
            "snapshot": self.store.latest_snapshot(),
            "staged": self.store.get("staged"),
            "journal_tail": tail,
            "journal_context": {
                "mode": "summary",
                "omitted_records": max(0, retained_count - len(tail)),
                "retained_from": (self.store.anchor() or {}).get("sequence", 0) + 1,
            },
            "model_calls": self.store.get("model_calls"),
            "limits": self.limits,
        }
        while len(canonical(bundle)) > 131_072 and bundle["journal_tail"]:
            bundle["journal_tail"].pop(0)
            bundle["journal_context"]["omitted_records"] += 1
        if len(canonical(bundle)) > 131_072:
            raise AgentError(
                "Current state and role view exceed the model context limit"
            )
        return bundle

    @staticmethod
    def _context_record(record: JsonObject) -> JsonObject:
        data = record["data"]
        if record["kind"] in (
            "model_input",
            "model_output",
            "program_input",
            "program_output",
        ):
            data = {
                key: data[key]
                for key in ("id", "operation", "purpose", "input_hash")
                if key in data
            }
        elif record["kind"] == "turn":
            data = {
                key: data[key]
                for key in ("input", "state", "message", "producer")
                if key in data
            }
        if len(canonical(data)) > 8192:
            data = {"omitted": True, "sha256": digest(data)}
        return {"sequence": record["sequence"], "kind": record["kind"], "data": data}

    @staticmethod
    def _response_identifier(identifier: str, index: int, retry: int = 0) -> str:
        value = identifier if index == 0 else identifier + ":history:" + str(index)
        return value + ":retry:" + str(retry) if retry else value

    @staticmethod
    def _input_history_key(identifier: str) -> str:
        # Retention removes this with the other completed-turn context caches.
        return "context:turn:input:" + digest(identifier)

    def _input_history(
        self, execution: JsonObject, identifier: str
    ) -> tuple[str, list[str]]:
        key = self._input_history_key(execution["input"])
        contexts = self.store.get(key)
        if contexts is None:
            # Older pending inputs have individual page caches only. Recover
            # their first two pages once, without querying or sampling again.
            roots = [
                row["data"]["id"] + ":respond"
                for row in self.store.records()
                if row["kind"] == "attempt"
                and row["data"].get("input") == execution["input"]
            ]
            contexts = []
            for root in dict.fromkeys([*roots, identifier]):
                for index in (0, 1):
                    context = "context:" + root + ":" + str(index)
                    if self.store.get(context) is not None and len(contexts) < 2:
                        contexts.append(context)
            self.store.commit(
                "history_context_recovered",
                {"input": execution["input"]},
                updates={key: contexts},
            )
        return key, contexts

    def _respond_with_history(
        self, bundle: JsonObject, identifier: str
    ) -> tuple[JsonObject, str]:
        execution = self.store.get("execution")
        history_key, contexts = self._input_history(execution, identifier)
        # Replay this execution's queries in their original order. A new attempt
        # starts with the pages already obtained by previous attempts for this input.
        pages = [
            self.store.get(key)
            for key in contexts
            if not key.startswith("context:" + identifier + ":")
        ]
        if any(page is None for page in pages):
            raise AgentError("Recorded history page is unavailable")
        allowed_kinds = {
            "input",
            "turn",
            "fault",
            "routing",
            "room_rejected",
            "program_installed",
            "control_received",
            "control_completed",
        }
        for index in range(3):
            execution = self.store.get("execution") or {}
            retry = (
                execution.get("history_retries", {}).get(str(index), 0)
                if identifier == execution.get("id", "") + ":respond"
                else 0
            )
            response_id = self._response_identifier(identifier, index, retry)
            result = self._call_model(
                "respond", dict(bundle, history_pages=pages), identifier=response_id
            )
            if not isinstance(result, dict) or set(result) != {"history_query"}:
                return result, response_id
            query = result["history_query"]
            if (
                index == 2
                or not isinstance(query, dict)
                or set(query) - {"after", "through", "limit", "kind", "query"}
                or not isinstance(query.get("kind"), str)
                or query.get("kind") not in allowed_kinds
                or type(query.get("limit", 10)) is not int
                or not 1 <= query.get("limit", 10) <= 20
            ):
                raise HistoryQueryRejected(
                    "History query scope or two-query budget exceeded"
                )
            key = "context:" + identifier + ":" + str(index)
            page = self.store.get(key)
            if page is None:
                if len(contexts) >= 2:
                    raise HistoryQueryRejected(
                        "Two-query history budget exceeded for this input"
                    )
                try:
                    raw = self.store.search(**query)
                except StoreError as error:
                    raise HistoryQueryRejected(
                        "History query refused: " + str(error)
                    ) from error
                page = dict(
                    raw,
                    records=[self._context_record(frame) for frame in raw["records"]],
                )
                if len(canonical(dict(bundle, history_pages=pages + [page]))) > 131_072:
                    raise HistoryQueryRejected(
                        "Requested history exceeds the bounded model context"
                    )
                self.store.commit(
                    "history_query",
                    {"id": response_id, "query": query, "through": page["through"]},
                    updates={key: page, history_key: contexts + [key]},
                )
                contexts = contexts + [key]
            elif key not in contexts:
                if len(contexts) >= 2:
                    raise HistoryQueryRejected(
                        "Two-query history budget exceeded for this input"
                    )
                contexts = contexts + [key]
                self.store.commit(
                    "history_context_recovered",
                    {"input": execution["input"]},
                    updates={history_key: contexts},
                )
            pages.append(page)
        raise AgentError("History query budget exceeded")

    @property
    def prepared_provider(self) -> PreparedProvider | None:
        if not callable(getattr(self.provider, "prepare", None)):
            return None
        if not isinstance(self.provider, PreparedProvider):
            raise AgentError(
                "Prepared providers require prepare, execute and decode callbacks"
            )
        return self.provider

    def _call_model(
        self, operation: str, bundle: JsonObject, *, identifier: str
    ) -> JsonObject:
        prepared = self.prepared_provider
        provider = getattr(
            self.provider,
            "identity",
            {
                "profile": "remote-agent/python-model-callback/1",
                "class": type(self.provider).__module__
                + "."
                + type(self.provider).__qualname__,
            },
        )

        def prepare() -> JsonObject:
            return (
                prepared.prepare(operation, bundle)
                if prepared is not None
                else {"operation": operation, "bundle": bundle}
            )

        def execute(request: JsonObject) -> JsonObject:
            if prepared is not None:
                return prepared.execute(request, timeout=self.model_timeout)
            value = self._invoke(
                operation, getattr(self.provider, operation), request["bundle"]
            )
            return {
                "profile": "remote-agent/callback-result/1",
                "value": value,
                "failure": None,
                "uncertain": False,
            }

        execution = self.model_journal.run(
            identifier,
            operation,
            provider,
            prepare,
            execute,
            max_calls=self.limits["max_model_calls"],
            timeout_ms=round(self.model_timeout * 1000),
            usage=getattr(self.provider, "usage", None),
        )
        return (
            prepared.decode(operation, execution)
            if prepared is not None
            else execution["value"]
        )

    def _invoke(
        self, operation: str, callback: Callable[..., JsonObject], *args: Any
    ) -> JsonObject:
        results: queue.Queue[
            tuple[Literal[True], JsonObject] | tuple[Literal[False], Exception]
        ] = queue.Queue(maxsize=1)

        def execute() -> None:
            try:
                value = callback(*args)
                check_depth(value)
                results.put((True, copy_json(value)))
            except Exception as error:  # noqa: BLE001 -- Worker threads must return arbitrary callback failures to their waiting owner.
                results.put((False, error))

        with self.jobs_lock:
            old = self.jobs.get(operation)
            if old and old.is_alive():
                raise AgentError("Previous callback is still running")
            thread = threading.Thread(
                target=execute, daemon=True, name="hybrid-" + operation
            )
            self.jobs[operation] = thread
            thread.start()
        try:
            outcome = results.get(timeout=self.model_timeout)
        except queue.Empty as error:
            raise CallbackTimeout(
                "Model or validator timeout; late result will be ignored"
            ) from error
        if outcome[0] is False:
            raise AgentError(str(outcome[1])) from outcome[1]
        return outcome[1]

    def _kick_repair(self) -> None:
        with self.lock:
            repair = self.store.get("repair")
            if (
                self.closed
                or self.stopping.is_set()
                or not repair
                or repair["status"] not in ("requested", "waiting")
            ):
                return
            if repair["status"] == "waiting" and (
                self.store.get("execution") or self.store.get("staged")
            ):
                return
            if self.repair_thread and self.repair_thread.is_alive():
                return
            if self.validator is None:
                self.store.commit(
                    "repair_held",
                    {"reason": "Host quality validator required"},
                    updates={"repair": dict(repair, status="held")},
                )
                return
            calls = self.store.get("repair_calls") + (
                0 if repair.get("reserved") else 1
            )
            repair = dict(repair, status="running", reserved=True)
            self.store.commit(
                "repair_started", {}, updates={"repair": repair, "repair_calls": calls}
            )
            self.repair_thread = threading.Thread(
                target=self._repair, args=(repair,), daemon=True, name="hybrid-repair"
            )
            self.repair_thread.start()

    def _history(self) -> list[JsonObject]:
        return [
            record["data"]
            for record in self.store.records()
            if record["kind"] == "turn"
        ]

    def _validate_candidate(
        self, candidate: Program, bundle: JsonObject, history: list[JsonObject]
    ) -> tuple[JsonObject, JsonObject]:
        self._ensure_open()
        self._check_artifact(candidate.artifact)
        metadata = candidate.describe(self.initial_view)
        original = self._initialization()["metadata"]
        if (
            metadata["schema"] != original["schema"]
            or metadata["initial"] != original["initial"]
        ):
            raise AgentError("Candidate schema or initial state is incompatible")
        for turn in history:
            self._ensure_open()
            result = candidate.handle(turn["input"], turn["before"], turn["view"])
            if result["type"] == "delegate" and turn["producer"] == "llm":
                continue
            if (
                result["type"] != "handled"
                or result["state"] != turn["state"]
                or result["message"] != turn["message"]
            ):
                raise AgentError("Candidate diverges from committed behavior")
        fault = bundle.get("fault")
        if fault and fault["needs_handled"]:
            matching = next(
                (
                    turn
                    for turn in history
                    if turn["input"]["id"] == fault["input"]["id"]
                ),
                None,
            )
            if matching:
                result = candidate.handle(
                    matching["input"], matching["before"], matching["view"]
                )
                if result["type"] != "handled":
                    raise AgentError("Candidate still delegates the program fault")
            else:
                projection = self.room.view(self.actor, self.now())
                result = candidate.handle(
                    fault["input"], bundle["state"], projection["view"]
                )
                if result["type"] != "handled":
                    raise AgentError(
                        "Candidate still delegates the pending program fault"
                    )
                self._valid_result(result)
                if result["message"] is None:
                    if projection["view"]["obligations"]:
                        raise AgentError(
                            "Candidate leaves the pending obligation unanswered"
                        )
                else:
                    probe = dict(
                        result["message"],
                        session=self.room.session,
                        id="probe:" + candidate.artifact.sha256[:40],
                        expected_version=projection["view"]["version"],
                    )
                    decision = self.gate.check(
                        projection, probe, self.actor, self.now()
                    )
                    if decision["status"] != "accepted":
                        raise AgentError(
                            "Candidate fails local contract probe: "
                            + str(decision.get("reason"))
                        )
        if self.validator is None:
            raise AgentError("Host quality validator required")
        self._ensure_open()
        verdict = self._invoke("validate", self.validator, candidate, bundle)
        if (
            not isinstance(verdict, dict)
            or verdict.get("ok") is not True
            or not isinstance(verdict.get("checks"), list)
            or not verdict["checks"]
        ):
            raise AgentError("Candidate did not pass the host quality checks")
        return metadata, verdict

    def _repair(self, repair: JsonObject) -> None:
        try:
            view = self.room.view(self.actor, self.now())["view"]
            with self.lock:
                if self.closed or self.stopping.is_set():
                    return
                bundle = self._bundle(repair["fault"]["input"], view)
            response = self._call_model("repair", bundle, identifier=repair["id"])
            if (
                not isinstance(response, dict)
                or type(response.get("resume", False)) is not bool
            ):
                raise AgentError("Invalid repair response")
            candidate = Program(
                Artifact(**response["artifact"]),
                timeout=self.program.timeout,
                journal=self.invocations,
            )
            for _ in range(3):
                with self.lock:
                    if self.closed or self.stopping.is_set():
                        return
                    history = self._history()
                metadata, verdict = self._validate_candidate(candidate, bundle, history)
                with self.lock:
                    if self.closed or self.stopping.is_set():
                        return
                    if len(self._history()) != len(history):
                        continue
                    if self.store.get("staged") or self.store.get("execution"):
                        self.store.commit(
                            "repair_waiting",
                            {"reason": "Pending execution or room proposal"},
                            updates={"repair": dict(repair, status="waiting")},
                        )
                        return
                    resume = (
                        response.get("resume", False)
                        and self.store.get("epoch") == repair["epoch"]
                    )
                    mode = (
                        (
                            "program"
                            if metadata["coverage"]["mode"] != "llm_only"
                            else "llm"
                        )
                        if resume
                        else self.store.get("mode")
                    )
                    self.store.commit(
                        "program_installed",
                        {
                            "artifact": candidate.artifact.to_dict(),
                            "checks": verdict["checks"],
                        },
                        updates={
                            "program": candidate.artifact.to_dict(),
                            "metadata": metadata,
                            "mode": mode,
                            "quarantined": False,
                            "repair": dict(repair, status="installed"),
                            "epoch": self.store.get("epoch") + 1,
                        },
                        snapshot=self._snapshot(
                            self.store.get("state"), metadata, candidate
                        ),
                    )
                    self.program = candidate
                    return
            raise AgentError(
                "Conversation changed during bounded repair catch-up; candidate not installed"
            )
        except Exception as error:  # noqa: BLE001 -- Repair callbacks must durably record rejection instead of losing the worker outcome.
            with self.lock:
                if not self.closed and not self.stopping.is_set():
                    status = (
                        "interrupted"
                        if isinstance(error, ModelUnresolved)
                        else "rejected"
                    )
                    self.store.commit(
                        "repair_" + status,
                        {"reason": str(error)[:2000]},
                        updates={
                            "repair": dict(
                                repair, status=status, reason=str(error)[:2000]
                            )
                        },
                    )

    def wait_for_repair(self, timeout: float) -> bool:
        thread = self.repair_thread
        if thread:
            thread.join(timeout)
            return not thread.is_alive()
        return True
