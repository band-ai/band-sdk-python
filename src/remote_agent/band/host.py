"""Explicitly configured host of the optional supervised participant runtime."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from remote_agent.capabilities import exclusive_lock
from remote_agent.network import HTTPError
from remote_agent.storage import (
    StoreError,
    canonical,
    digest,
    private_directory,
    private_file,
)
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.interfaces import RuntimeParticipant


ALL_OPERATIONS = "(SELECT *, 'operations' AS storage FROM operations UNION ALL SELECT *, 'ephemeral.operations' AS storage FROM ephemeral.operations)"


class ProgramHost:
    def __init__(
        self,
        root: SourcePath,
        *,
        assessor: Callable[[JsonObject, JsonObject], JsonObject],
        factory: Callable[[JsonObject, JsonObject, Path], RuntimeParticipant],
        authors: Iterable[str],
        maximum: int = 16,
    ) -> None:
        self.root = private_directory(root)
        self.assessor = assessor
        self.factory = factory
        self.authors = set(authors)
        self.maximum = maximum
        self.lock = threading.RLock()
        self.closed = False
        self.instances = {}
        self.stopping = threading.Event()
        self.owner = os.fdopen(private_file(self.root / "registry.owner"), "a+b")
        try:
            exclusive_lock(self.owner.fileno(), nonblocking=True)
        except BlockingIOError:
            self.owner.close()
            raise ValueError("host_already_running") from None
        self.db = sqlite3.connect(
            self.root / "registry.sqlite3", check_same_thread=False
        )
        (self.root / "registry.sqlite3").chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,room TEXT NOT NULL,session TEXT NOT NULL,
            actor TEXT NOT NULL,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,input TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS runtimes(scope TEXT PRIMARY KEY,id TEXT NOT NULL,configuration TEXT NOT NULL,status TEXT NOT NULL,hold TEXT);
          CREATE INDEX IF NOT EXISTS qualified_assessment_lookup
            ON operations(room,json_extract(input,'$.context.source.sha256'),created DESC)
            WHERE kind='assess' AND status='ready' AND json_type(result,'$.deployable')='true'
              AND json_extract(result,'$.classification') IN ('program','hybrid');
        """)
        self.db.execute("ATTACH DATABASE ':memory:' AS ephemeral")
        self.db.execute(
            "CREATE TABLE ephemeral.operations AS SELECT * FROM operations WHERE 0"
        )
        with self.db:
            self.db.execute(
                "UPDATE operations SET status='held',result=? WHERE status='running'",
                (
                    canonical(
                        {"error": "interrupted_operation_outcome_unknown"}
                    ).decode(),
                ),
            )
            for scope, encoded, status in self.db.execute(
                "SELECT scope,configuration,status FROM runtimes"
            ).fetchall():
                item = json.loads(encoded)
                # Older registries can contain a durable stop with no runtime
                # barrier. Never construct a participant to finish its stop.
                stop = self.db.execute(
                    "SELECT 1 FROM operations WHERE room=? AND session=? AND kind='control' AND json_extract(input,'$.request.action')='stop' LIMIT 1",
                    (item["room"], item["session"]),
                ).fetchone()
                if status == "stopped":
                    continue
                if status == "stopping" or stop:
                    self.db.execute(
                        "UPDATE runtimes SET status='stopping',hold=COALESCE(hold,'stop_outcome_unknown') WHERE scope=?",
                        (scope,),
                    )
                    self.db.execute(
                        "UPDATE operations SET status='held',result=? WHERE room=? AND session=? AND kind='control' AND json_extract(input,'$.request.action')='stop' AND status='queued'",
                        (
                            canonical({"error": "stop_outcome_unknown"}).decode(),
                            item["room"],
                            item["session"],
                        ),
                    )
                elif not item["request"]["journal"]:
                    self.db.execute(
                        "UPDATE runtimes SET status='lost',hold=? WHERE scope=?",
                        ("ephemeral_state_lost_start_a_fresh_session", scope),
                    )
                else:
                    self.db.execute(
                        "UPDATE runtimes SET status='recovering' WHERE scope=?",
                        (scope,),
                    )
        for companion in self.root.glob("registry.sqlite3*"):
            companion.chmod(0o600)

    def ready(self) -> bool:
        return not self.closed

    def metrics(self) -> JsonObject:
        return {"ready": self.ready(), "runtimes": len(self.instances)}

    def dispatch(
        self, method: str, path: str, grant: JsonObject, data: JsonObject | None
    ) -> tuple[int, JsonObject]:
        if method != "POST" or path != "/v1/operations":
            raise HTTPError(404, "Unknown endpoint")
        if grant.get("actor") != "BandPlatform" or "controls" not in grant.get(
            "permissions", []
        ):
            raise HTTPError(403, "Host grant required")
        try:
            return 200, self.operation(data)
        except ValueError as error:
            raise HTTPError(
                409 if str(error) == "identity_conflict" else 422, str(error)
            ) from error

    def operation(self, value: JsonObject | None) -> JsonObject:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "profile",
                "room",
                "session",
                "actor",
                "operation",
                "request",
                "context",
            }
            or value["profile"] != "band-contract/program-host-request/1"
            or not value["actor"].startswith("User:")
            or not isinstance(value["request"], dict)
            or not isinstance(value["context"], dict)
        ):
            raise ValueError("invalid_host_request")
        kind = value["operation"]
        body = value["request"]
        scope = value["room"] + "/" + value["session"]
        with self.lock:
            if self.closed:
                raise ValueError("host_stopping")
            if kind == "status":
                return self.status(value)
            if kind == "journal":
                instance = self.instances.get(scope)
                if not instance:
                    raise ValueError("runtime_not_running")
                if not instance.durable:
                    raise ValueError("journal_disabled")
                try:
                    page = instance.journal(**body)
                except StoreError as error:
                    # The store refuses a page outside its retained range; that is
                    # the caller's request, not a storage outage.
                    raise ValueError("invalid_journal_page") from error
                return dict(self.status(value), journal_page=page)
            if kind not in (
                "assess",
                "start",
                "control",
                "replay",
                "task",
            ) or not isinstance(body.get("id"), str):
                raise ValueError("invalid_operation")
            identifier = body["id"]
            fingerprint = digest({k: v for k, v in value.items() if k != "context"})
            prior = self.db.execute(
                "SELECT fingerprint,status,storage FROM "
                + ALL_OPERATIONS
                + " WHERE id=?",
                (identifier,),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("identity_conflict")
                # An explicit exact task retry reconciles its staged room proposal.
                if (
                    kind == "task"
                    and prior[1] == "held"
                    and not self.stop_barrier(scope)
                ):
                    with self.db:
                        self.db.execute(
                            "UPDATE "
                            + prior[2]
                            + " SET status='queued',result=NULL WHERE id=?",
                            (identifier,),
                        )
                elif (
                    kind == "control"
                    and body.get("action") == "stop"
                    and prior[1] == "held"
                    and scope in self.instances
                ):
                    # Retry cleanup on the same physical instance only. After a
                    # crash its closure remains unknown; a factory is not proof.
                    with self.db:
                        self.db.execute(
                            "UPDATE "
                            + prior[2]
                            + " SET status='queued',result=NULL WHERE id=?",
                            (identifier,),
                        )
                elif (
                    kind == "start"
                    and prior[1] in ("held", "complete")
                    and scope not in self.instances
                ):
                    runtime = self.db.execute(
                        "SELECT id,configuration,status FROM runtimes WHERE scope=?",
                        (scope,),
                    ).fetchone()
                    # A completed start may have a later held recovery. Recover
                    # only the original durable participant; an ephemeral start
                    # may already have effects with no journal.
                    if (
                        runtime
                        and runtime[0] == identifier
                        and runtime[2] == "held"
                        and json.loads(runtime[1])["request"].get("journal") is True
                    ):
                        with self.db:
                            self.db.execute(
                                "UPDATE "
                                + prior[2]
                                + " SET status='queued',result=NULL WHERE id=?",
                                (identifier,),
                            )
                return self.status(value)
            # The owner's first stop of a running scope adds at most one row per
            # runtime, so a spent operation budget never keeps a runtime running.
            first_stop = (
                kind == "control"
                and body.get("action") == "stop"
                and scope in self.instances
                and not self.stop_barrier(scope)
            )
            if (
                not first_stop
                and self.db.execute(
                    "SELECT COUNT(*) FROM " + ALL_OPERATIONS
                ).fetchone()[0]
                >= self.maximum * 16
            ):
                raise ValueError("operation_capacity")
            if kind == "assess":
                if body.get("author_id") not in self.authors:
                    raise ValueError("author_unavailable")
                if body.get("role") not in value["context"]["config"]["bindings"]:
                    raise ValueError("unknown_role")
                if (
                    self.db.execute(
                        "SELECT COUNT(*) FROM operations WHERE kind='assess'"
                    ).fetchone()[0]
                    >= self.maximum
                ):
                    raise ValueError("assessment_budget_exhausted")
            if kind == "start":
                assessment = self.assessment(body.get("assessment_id"))
                if (
                    not assessment
                    or assessment["status"] != "ready"
                    or assessment.get("deployable") is not True
                    or assessment.get("classification") == "llm"
                    or assessment.get("room") != value["room"]
                    or assessment.get("source_sha256")
                    != value["context"]["source"]["sha256"]
                ):
                    raise ValueError("validated_assessment_required")
                if value["context"]["state"]["status"] != "active":
                    raise ValueError("session_terminal")
                if value["context"]["state"].get("accepted_count", 0) != 0:
                    raise ValueError("start_on_a_fresh_session")
                if self.db.execute(
                    "SELECT 1 FROM runtimes WHERE scope=?", (scope,)
                ).fetchone():
                    raise ValueError("runtime_already_exists")
            if kind in ("control", "replay", "task") and scope not in self.instances:
                raise ValueError("runtime_not_running")
            if (
                kind in ("control", "replay", "task")
                and self.stop_barrier(scope)
                and not (kind == "control" and body.get("action") == "stop")
            ):
                raise ValueError("runtime_stopping")
            if kind == "replay" and not self.instances[scope].durable:
                raise ValueError("journal_disabled")
            instance = self.instances.get(scope)
            table = (
                "ephemeral.operations"
                if instance and not instance.durable
                else "operations"
            )
            with self.db:
                self.db.execute(
                    "INSERT INTO " + table + " VALUES(?,?,?,?,?,?,?,'queued',NULL,?)",
                    (
                        identifier,
                        value["room"],
                        value["session"],
                        value["actor"],
                        kind,
                        fingerprint,
                        canonical(value).decode(),
                        time.time_ns(),
                    ),
                )
                if kind == "control" and body.get("action") == "stop":
                    self.db.execute(
                        "UPDATE runtimes SET status='stopping' WHERE scope=?", (scope,)
                    )
            return self.status(value)

    def stop_barrier(self, scope: str) -> bool:
        row = self.db.execute(
            "SELECT status FROM runtimes WHERE scope=?", (scope,)
        ).fetchone()
        return row is not None and row[0] in ("stopping", "stopped")

    def assessment(self, identifier: str | None) -> JsonObject | None:
        row = self.db.execute(
            "SELECT input,status,result FROM operations WHERE id=? AND kind='assess'",
            (identifier,),
        ).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        result = json.loads(row[2]) if row[2] else {}
        return dict(
            result,
            id=identifier,
            status=row[1],
            role=value["request"]["role"],
            author_id=value["request"]["author_id"],
            room=value["room"],
            session=value["session"],
            source_sha256=value["context"]["source"]["sha256"],
        )

    def status(self, value: JsonObject) -> JsonObject:
        room, session = value["room"], value["session"]
        scope = room + "/" + session
        row = self.db.execute(
            "SELECT id FROM operations WHERE room=? AND session=? AND kind='assess' ORDER BY rowid DESC LIMIT 1",
            (room, session),
        ).fetchone()
        assessment = self.assessment(row[0]) if row else None
        context = value["context"]
        source = context.get("source", {})
        state = context.get("state", {})
        if (
            row is None
            and isinstance(source, dict)
            and isinstance(source.get("sha256"), str)
            and isinstance(state, dict)
            and state.get("status") == "active"
            and state.get("accepted_count") == 0
        ):
            # Reuse an already qualified artifact without spending a model call.
            # The partial index bounds polling to this room and exact source.
            reusable = self.db.execute(
                """SELECT id FROM operations
                WHERE room=? AND json_extract(input,'$.context.source.sha256')=?
                  AND kind='assess' AND status='ready' AND json_type(result,'$.deployable')='true'
                  AND json_extract(result,'$.classification') IN ('program','hybrid')
                ORDER BY created DESC LIMIT 1""",
                (room, source["sha256"]),
            ).fetchone()
            if reusable:
                candidate = self.assessment(reusable[0])
                assert candidate is not None
                if candidate["role"] in context.get("config", {}).get("bindings", {}):
                    assessment = candidate
        runtime = self.db.execute(
            "SELECT id,configuration,status,hold FROM runtimes WHERE scope=?", (scope,)
        ).fetchone()
        runtime_value = None
        if runtime:
            configuration = json.loads(runtime[1])
            assessed = self.assessment(configuration["request"]["assessment_id"])
            assert assessed is not None
            if row is None:
                assessment = assessed
            runtime_value = {
                "id": runtime[0],
                "status": runtime[2],
                "hold": runtime[3],
                "role": assessed["role"],
                "actor": configuration["context"]["config"]["bindings"][
                    assessed["role"]
                ],
                "journal": configuration["request"]["journal"],
                "mode": configuration["request"]["mode"],
            }
            if scope in self.instances:
                runtime_value.update(self.instances[scope].summary())
            if runtime[2] in ("stopping", "stopped"):
                runtime_value.update(status=runtime[2], hold=runtime[3])

        def latest(kind: str) -> JsonObject | None:
            row = self.db.execute(
                "SELECT id,status,result,input FROM "
                + ALL_OPERATIONS
                + " WHERE room=? AND session=? AND kind=? ORDER BY created DESC LIMIT 1",
                (room, session, kind),
            ).fetchone()
            if not row:
                return None
            result = json.loads(row[2]) if row[2] else {}
            result.update(
                id=row[0],
                status=result.get("status", row[1]) if kind == "control" else row[1],
            )
            # Owners may reconcile only the original task identity and body. Never expose its host context.
            if kind == "task":
                result["request"] = json.loads(row[3])["request"]
            return result

        return {
            "profile": "band-contract/program/1",
            "available": True,
            "room": room,
            "session": session,
            "assessment_authors": sorted(self.authors),
            "assessment": assessment,
            "runtime": runtime_value,
            "journal_page": None,
            "replay": latest("replay"),
            "control": latest("control"),
            "task": latest("task"),
        }

    def step(self) -> bool:
        with self.lock:
            recovering = self.db.execute(
                "SELECT scope,configuration FROM runtimes WHERE status='recovering' LIMIT 1"
            ).fetchone()
            if recovering:
                value = json.loads(recovering[1])
                identifier = value["request"]["id"]
                kind = "recover"
                table = "operations"
            else:
                row = self.db.execute(
                    "SELECT id,input,kind,storage FROM "
                    + ALL_OPERATIONS
                    + " WHERE status='queued' ORDER BY created LIMIT 1"
                ).fetchone()
                if not row:
                    return False
                identifier, encoded, kind, table = row
                value = json.loads(encoded)
                with self.db:
                    self.db.execute(
                        "UPDATE " + table + " SET status='running' WHERE id=?",
                        (identifier,),
                    )
        scope = value["room"] + "/" + value["session"]
        body = value["request"]
        try:
            if kind in ("start", "recover", "control", "replay", "task"):
                with self.lock:
                    if self.stop_barrier(scope) and not (
                        kind == "control" and body.get("action") == "stop"
                    ):
                        raise ValueError("runtime_stopping")
            if kind == "assess":
                result = self.assessor(value["context"], body)
                status = "ready"
                if result.get("classification") not in ("program", "hybrid", "llm"):
                    raise ValueError("invalid_assessment")
            elif kind in ("start", "recover"):
                with self.lock:
                    assessment = self.assessment(body["assessment_id"])
                    assert assessment is not None
                    if kind == "start":
                        encoded = canonical(value).decode()
                        existing = self.db.execute(
                            "SELECT id,configuration FROM runtimes WHERE scope=?",
                            (scope,),
                        ).fetchone()
                        if existing:
                            if (
                                existing != (identifier, encoded)
                                or scope in self.instances
                            ):
                                raise ValueError("runtime_already_exists")
                            with self.db:
                                self.db.execute(
                                    "UPDATE runtimes SET status='starting',hold=NULL WHERE scope=? AND id=?",
                                    (scope, identifier),
                                )
                        else:
                            with self.db:
                                self.db.execute(
                                    "INSERT INTO runtimes VALUES(?,?,?,'starting',NULL)",
                                    (scope, identifier, encoded),
                                )
                instance = self.factory(
                    value, assessment, self.root / "runtimes" / value["session"]
                )
                with self.lock:
                    if instance is not None:
                        self.instances[scope] = instance
                    with self.db:
                        self.db.execute(
                            "UPDATE runtimes SET status='running',hold=NULL WHERE scope=?",
                            (scope,),
                        )
                        if kind == "recover":
                            # Recovery satisfies this exact queued or interrupted
                            # start, using its pinned identity and configuration.
                            self.db.execute(
                                "UPDATE operations SET status='complete',result=? WHERE id=? AND kind='start' AND status IN ('queued','held') AND input=?",
                                (
                                    canonical({"runtime_id": identifier}).decode(),
                                    identifier,
                                    canonical(value).decode(),
                                ),
                            )
                result = {"runtime_id": identifier}
                status = "complete"
            elif kind == "control":
                if body.get("action") == "stop" and scope not in self.instances:
                    raise ValueError("stop_outcome_unknown")
                result = self.instances[scope].control(value["actor"], body)
                status = "complete"
                if body["action"] == "stop":
                    self.instances[scope].close()
                    with self.lock:
                        self.instances.pop(scope)
                        with self.db:
                            self.db.execute(
                                "UPDATE runtimes SET status='stopped',hold=NULL WHERE scope=?",
                                (scope,),
                            )
            elif kind == "task":
                result = self.instances[scope].task(body)
                status = "complete"
            else:
                result = self.instances[scope].replay()
                status = "complete"
            if kind != "recover":
                with self.lock, self.db:
                    self.db.execute(
                        "UPDATE " + table + " SET status=?,result=? WHERE id=?",
                        (status, canonical(result).decode(), identifier),
                    )
        except Exception as error:  # noqa: BLE001 -- Runtime factories and participant callbacks must leave a durable hold on failure.
            # Private logs may carry diagnostics; outward state is a bounded category.
            result = {
                "error": str(error)[:200]
                if isinstance(error, ValueError)
                else type(error).__name__
            }
            with self.lock, self.db:
                if kind in ("start", "recover"):
                    self.db.execute(
                        "UPDATE runtimes SET status='held',hold=? WHERE scope=? AND id=? AND status NOT IN ('stopping','stopped')",
                        (result["error"], scope, identifier),
                    )
                if kind == "control" and body.get("action") == "stop":
                    self.db.execute(
                        "UPDATE runtimes SET hold=? WHERE scope=? AND status='stopping'",
                        (result["error"], scope),
                    )
                if kind != "recover":
                    self.db.execute(
                        "UPDATE " + table + " SET status='held',result=? WHERE id=?",
                        (canonical(result).decode(), identifier),
                    )
        return True

    def run(self) -> None:
        while not self.stopping.is_set():
            if not self.step():
                self.stopping.wait(0.25)

    def close(self) -> None:
        if self.closed:
            return
        self.stopping.set()
        for instance in list(self.instances.values()):
            instance.close()
        with self.lock:
            self.instances.clear()
            self.closed = True
            self.db.close()
            self.owner.close()
