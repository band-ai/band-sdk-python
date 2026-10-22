"""Pure program qualification and a supervised SDK instance beside a Band agent."""

from __future__ import annotations

import copy
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from remote_agent.band.configuration import model_arguments, module_command
from remote_agent.band.effects import EffectRunner
from remote_agent.band.publication import immutable_bytes, read_published
from remote_agent.band.quality import qualify_candidate
from remote_agent.diagnostics import agent_summary
from remote_agent.invocations import InvocationJournal
from remote_agent.network import HTTPRoom
from remote_agent.program import Artifact, Program
from remote_agent.publication import publish_file
from remote_agent.service import Service, load_config
from remote_agent.storage import canonical, private_directory
from remote_agent.store import Store
from remote_agent.types import JsonObject, SourcePath

ALLOWED_SOURCES = {
    "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0",
    "88b415331ea8c5c08910c4facfb1c6f128e427ebe10dc9bbd7b891fc9ecdbc62",
    "2bbad024ffab6370a702e6d9825cb10b0805196e8c522ed3861490f4cf411d7b",
}


logger = logging.getLogger(__name__)


def write_runtime_configuration(path: Path, candidate: JsonObject) -> None:
    """A host release does not migrate an existing participant's callback identities."""
    if path.exists():
        previous = json.loads(read_published(path))
        comparable = copy.deepcopy(candidate)
        for callback in ("provider", "validator"):
            comparable["agent"][callback] = previous["agent"][callback]
        if canonical(comparable) != canonical(previous):
            raise ValueError("pinned_runtime_configuration_changed")
        return
    publish_file(
        path, lambda stream: stream.write(json.dumps(candidate).encode("utf-8"))
    )


def validate_candidate(
    source: str,
    workspace: SourcePath,
    *,
    durable: bool = True,
    qualification: JsonObject | None = None,
    role: str | None = None,
    contract_sha256: str | None = None,
) -> JsonObject:
    if qualification is not None:
        return qualify_candidate(
            source,
            workspace,
            qualification=qualification,
            role=role,
            contract_sha256=contract_sha256,
            durable=durable,
        )
    workspace = private_directory(workspace)
    artifact = Artifact.publish(source, workspace / "artifacts")
    with Store(workspace / "validation.sqlite3", durable=durable) as journal:
        program = Program(artifact, journal=InvocationJournal(journal))
        metadata = program.describe({})
        if metadata["schema"] != "1" or metadata["initial"] != {"answered": 0}:
            raise ValueError("semantic_state_schema")
        mode = metadata["coverage"]["mode"]
        if mode not in ("full", "partial"):
            raise ValueError("semantic_coverage")
        state = {"answered": 0}
        checked = []
        handled_ops = set()
        for op in ("add", "multiply"):
            for a, b in (
                (0, 0),
                (0, 1000),
                (1, 999),
                (1000, 1000),
                (37, 61),
                (223, 881),
            ):
                event = {
                    "id": f"{op}-{a}-{b}",
                    "kind": "question",
                    "payload": {"op": op, "a": a, "b": b},
                }
                result = program.handle(event, state, {"status": "active"})
                if result["type"] == "delegate" and mode == "partial":
                    continue
                expected = a + b if op == "add" else a * b
                if (
                    result["type"] != "handled"
                    or result["state"] != {"answered": state["answered"] + 1}
                    or result["message"]
                    != {
                        "kind": "answer",
                        "payload": {"request": event["id"], "value": expected},
                    }
                ):
                    raise ValueError("semantic_arithmetic_or_state")
                state = result["state"]
                handled_ops.add(op)
                checked.append(f"{op}({a},{b})={expected}")
        if not handled_ops or (mode == "full" and handled_ops != {"add", "multiply"}):
            raise ValueError("semantic_incomplete_coverage")
        for payload in (
            {"op": "subtract", "a": 2, "b": 1},
            {"op": "add", "a": True, "b": 1},
            {"op": "multiply", "a": 1001, "b": 0},
            {"text": "please add two numbers"},
        ):
            if (
                program.handle(
                    {"id": "unsupported", "kind": "question", "payload": payload},
                    state,
                    {"status": "active"},
                )["type"]
                != "delegate"
            ):
                raise ValueError("semantic_unsupported_input_guessed")
        for event in (
            {"id": "cancel", "kind": "cancel", "payload": {"request": "q"}},
            {"id": "stop", "kind": "stop", "payload": {}},
            {"id": "q", "kind": "question", "payload": {"op": "add", "a": 2, "b": 3}},
        ):
            result = program.handle(event, state, {"status": "terminal"})
            if (
                result["type"] != "handled"
                or result["message"] is not None
                or result["state"] != state
            ):
                raise ValueError("semantic_terminal_reply")
        checked.extend(
            [
                "unsupported inputs delegate",
                "terminal work is abandoned",
                "state increments only for handled questions",
            ]
        )
        return {
            "ok": True,
            "checks": checked,
            "program_sha256": artifact.sha256,
            "coverage": metadata["coverage"],
        }


def replay_turns(
    turns: list[JsonObject], initial: JsonObject, artifacts: SourcePath
) -> JsonObject:
    state = copy.deepcopy(initial)
    versions = set()
    program_count = 0
    model_count = 0
    supervisor_count = 0
    for turn in turns:
        if turn["before"] != state:
            raise ValueError("replay_diverged_before_state")
        if turn["producer"] == "program":
            artifact = Artifact(
                str(Path(artifacts) / (turn["program"] + ".py")), turn["program"]
            )
            result = Program(artifact).handle(turn["input"], state, turn["view"])
            if (
                result["type"] != "handled"
                or result["state"] != turn["state"]
                or result["message"] != turn["message"]
            ):
                raise ValueError("replay_diverged_program_output")
            versions.add(artifact.sha256)
            program_count += 1
        elif turn["producer"] == "supervisor":
            if (
                turn["view"].get("status") != "terminal"
                or turn["message"] is not None
                or turn["state"] != state
            ):
                raise ValueError("replay_diverged_terminal_discard")
            supervisor_count += 1
        else:
            # These are the recorded accepted state/message decisions. No provider
            # is invoked and no outgoing effect is published during replay.
            model_count += 1
        state = copy.deepcopy(turn["state"])
    return {
        "match": True,
        "state": state,
        "turns": len(turns),
        "program_turns": program_count,
        "recorded_model_turns": model_count,
        "supervisor_turns": supervisor_count,
        "versions": sorted(versions),
        "published_effects": 0,
        "model_calls": 0,
    }


def variant(source: str, mode: str) -> str:
    if mode == "program":
        return source
    extra = """\n_original_handle = handle
_original_coverage = COVERAGE
COVERAGE = dict(COVERAGE, mode="partial", handles=["addition and terminal inputs"], delegates=["multiplication is routed to the model in hybrid mode"])
def handle(event,state,context):
    if context.view.get("status") != "terminal" and event.get("kind") == "question" and event.get("payload",{}).get("op") == "multiply":
        return Delegate("Multiplication is assigned to the model for this hybrid participant")
    return _original_handle(event,state,context)
"""
    if mode == "llm":
        extra = '\nCOVERAGE = dict(COVERAGE, mode="llm_only", handles=[], delegates=["Model owns all turns by explicit host selection"])\n'
    return source + "\nfrom remote_agent.sdk import Delegate\n" + extra


class RuntimeDriver:
    def __init__(
        self,
        value: JsonObject,
        assessment: JsonObject,
        workspace: SourcePath,
        *,
        host_config: JsonObject,
    ) -> None:
        self.workspace = private_directory(workspace)
        self.options = value["request"]
        self.durable = self.options["journal"]
        self.hold = None
        self.business_hold = None
        self.stopping = threading.Event()
        self.last_retained_turns = -1
        self.last_seen = None
        role = assessment["role"]
        actor = value["context"]["config"]["bindings"][role]
        if (
            role != "calculator"
            or value["context"]["source"]["sha256"] not in ALLOWED_SOURCES
        ):
            raise ValueError("host_validator_not_configured_for_role")
        agent_key = host_config["agent_keys"].get(actor)
        if not agent_key:
            raise ValueError("agent_credential_not_configured")
        source = variant(assessment["source"], self.options["mode"])
        if self.options["mode"] != "llm":
            verdict = validate_candidate(
                source, self.workspace / "initial-validation", durable=self.durable
            )
            if (
                self.options["mode"] == "program"
                and verdict["coverage"]["mode"] != "full"
            ):
                raise ValueError("program_requires_full_coverage")
        program_path = self.workspace / "initial.py"
        contract_path = self.workspace / "contract.band"
        key_path = self.workspace / "room.key"
        tokens_path = self.workspace / "controls.json"
        for path, text in (
            (program_path, source),
            (contract_path, value["context"]["source"]["content"]),
            (key_path, Path(agent_key).read_text()),
            (
                tokens_path,
                json.dumps(
                    {
                        secrets.token_urlsafe(32): {
                            "actor": value["actor"],
                            "permissions": ["controls", "observe"],
                        }
                    }
                ),
            ),
        ):
            if path in (program_path, contract_path):
                immutable_bytes(
                    path, text.encode("utf-8"), conflict="pinned_runtime_source_changed"
                )
            else:
                path.write_text(text)
                path.chmod(0o600)
        engine = list(host_config["engine_command"])
        config = {
            "profile": "band-contract/service/1",
            "kind": "agent",
            "poll_interval_ms": 500,
            "store": {
                "path": str(self.workspace / "agent.sqlite3"),
                "durable": self.durable,
                "max_records": 1_000_000,
                "max_database_bytes": 1_073_741_824,
            },
            "engine": {
                "command": engine,
                "contract": str(contract_path),
                "format": "frontmatter",
            },
            "http": {"host": "127.0.0.1", "port": 0, "tokens_file": str(tokens_path)},
            "agent": {
                "actor": actor,
                "program_source": str(program_path),
                "artifact_directory": str(self.workspace / "artifacts"),
                "room": {
                    "url": host_config["platform_url"]
                    + f"/api/v1/agent/chats/{value['room']}/contracts/{value['session']}",
                    "token_file": str(key_path),
                    "authentication": "band-api-key",
                },
                "provider": {
                    "command": module_command(
                        "program_model",
                        "--audit",
                        str(self.workspace / "model"),
                        *model_arguments(host_config["model_config"]),
                    )
                    + ([] if self.durable else ["--no-journal"]),
                    "timeout_ms": 95_000,
                },
                "validator": {
                    "command": module_command("program_validator"),
                    "timeout_ms": 30_000,
                },
                "control_actors": {
                    value["actor"]: [
                        "wake",
                        "takeover",
                        "resume",
                        "repair",
                        "retry",
                        "reconcile",
                    ]
                },
                "program_timeout_ms": 2000,
                "model_timeout_ms": 95_000,
                "snapshot_every": 5,
                "max_model_calls": 12,
                "max_repairs": 2,
            },
        }
        config_path = self.workspace / "service.json"
        write_runtime_configuration(config_path, config)
        self.service = Service(load_config(config_path))
        try:
            requester = value["context"]["config"]["bindings"]["requester"]
            requester_key = host_config["agent_keys"].get(requester)
            if not requester_key:
                raise ValueError("requester_credential_not_configured")
            self.requester = HTTPRoom(
                host_config["platform_url"]
                + f"/api/v1/agent/chats/{value['room']}/contracts/{value['session']}",
                Path(requester_key).read_text().strip(),
                authentication="band-api-key",
            )
            if (
                self.requester.actor != requester
                or self.requester.session != self.service.room.session
                or self.requester.identity != self.service.room.identity
            ):
                raise ValueError("requester_authority_mismatch")
            self.thread = threading.Thread(
                target=self.run, daemon=True, name="program-" + value["session"][:8]
            )
            self.thread.start()
        except BaseException:
            self.service.close()
            raise

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.service.cycle()
                self.hold = None
                self.last_seen = time.time_ns() // 1_000_000
                store = self.service.store
                if (
                    not store.get("queue")
                    and not store.get("staged")
                    and not store.get("execution")
                ):
                    turns = store.get("turns")
                    if turns != self.last_retained_turns:
                        self.verify_business_results()
                        try:
                            self.service.controller.retain_history(
                                target_bytes=self.options["target_bytes"],
                                minimum_steps=self.options["minimum_steps"],
                            )
                        except Exception as error:  # noqa: BLE001 -- Retention failure must preserve the running participant and pending state.
                            logger.debug(
                                "History retention deferred: %s", type(error).__name__
                            )
                        self.last_retained_turns = turns
            except Exception as error:  # noqa: BLE001 -- The participant supervisor converts arbitrary callback failures to a hold.
                self.hold = type(error).__name__
            self.stopping.wait(0.5)

    def summary(self) -> JsonObject:
        store = self.service.store
        with store.lock:
            value = agent_summary(store)
            snapshot = store.latest_snapshot()
            anchor = store.anchor()
            return dict(
                value,
                status="held"
                if self.hold or self.business_hold or self.service.held
                else "running",
                hold=self.hold or self.business_hold,
                business_checks=store.get("business_checks", []),
                journal=self.durable,
                state=store.get("state"),
                program_sha256=store.get("program")["sha256"],
                coverage=store.get("metadata")["coverage"],
                snapshot={
                    "sequence": snapshot["sequence"],
                    "program": snapshot["program"],
                    "schema": snapshot["schema"],
                }
                if snapshot
                else None,
                retained_from=anchor["sequence"] + 1 if anchor else 1,
                storage=store.metrics(),
                last_seen=self.last_seen,
            )

    def task(self, body: JsonObject) -> JsonObject:
        store = self.service.store
        room = self.requester
        identifier = body["id"]
        prior = store.get("effect:" + identifier)
        if prior:
            request = prior["request"]
        else:
            view = room.view(room.actor)["view"]
            payload = body["payload"]
            if body["kind"] == "cancel":
                resolved = {
                    f["payload"].get("request")
                    for f in view["facts"].values()
                    if f["kind"] in ("answer", "cancel")
                }
                pending = [
                    f["id"]
                    for f in view["facts"].values()
                    if f["kind"] == "question" and f["id"] not in resolved
                ]
                if len(pending) != 1:
                    raise ValueError("one_pending_question_required")
                payload = {"request": pending[0]}
            request = {
                "id": identifier,
                "session": room.session,
                "kind": body["kind"],
                "payload": payload,
                "expected_version": view["version"],
            }

        def validate(result: JsonObject, sent: JsonObject) -> bool:
            if result.get("status") == "rejected":
                return isinstance(result.get("reason"), str)
            receipt = result.get("original", result)
            return (
                result.get("status") in ("accepted", "duplicate")
                and receipt.get("id") == sent["id"]
                and receipt.get("session") == sent["session"]
                and receipt.get("actor") == room.actor
            )

        decision = EffectRunner(store).run(
            identifier, request, lambda sent: room.publish(sent, room.actor), validate
        )
        return {"proposal_id": identifier, "decision": decision}

    def verify_business_results(self) -> None:
        store = self.service.store
        view = self.requester.view(self.requester.actor)["view"]
        if store.get("business_version") == view["version"]:
            return
        checked = set(store.get("business_checked", []))
        checks = store.get("business_checks", [])
        for fact in view["facts"].values():
            if fact["kind"] != "answer" or fact["id"] in checked:
                continue
            question = view["facts"].get(fact["payload"].get("request"))
            if not question or question["kind"] != "question":
                raise ValueError("business_reference_unavailable")
            payload = question["payload"]
            expected = (
                payload["a"] + payload["b"]
                if payload["op"] == "add"
                else payload["a"] * payload["b"]
            )
            actual = fact["payload"].get("value")
            matches = type(actual) is int and actual == expected
            result = {
                "question": question["id"],
                "answer": fact["id"],
                "expected": expected,
                "actual": actual,
                "match": matches,
            }
            checked.add(fact["id"])
            checks = (checks + [result])[-20:]
            store.commit(
                "business_check",
                result,
                updates={
                    "business_checked": sorted(checked),
                    "business_checks": checks,
                },
            )
            if not matches:
                self.business_hold = "business_rule_mismatch_request_model_inspection"
        store.commit(
            "business_observed",
            {"version": view["version"]},
            updates={"business_version": view["version"]},
        )

    def control(self, actor: str, body: JsonObject) -> JsonObject:
        if body["action"] == "stop":
            return {"action": "stop", "status": "stopped"}
        return self.service.controller.control(
            actor, {k: body[k] for k in ("id", "action", "reason")}
        )

    def journal(self, **page: Any) -> JsonObject:
        return self.service.store.search(**page)

    def replay(self) -> JsonObject:
        with self.service.store.lock:
            store = self.service.store
            store._verify()
            store._verify_snapshots()
            records = store.records()
            anchor = store.anchor()
            initialization = (
                anchor["initialization"]
                if anchor
                else next(r["data"] for r in records if r["kind"] == "initialize")
            )
            state = (
                anchor["snapshot"]["state"]
                if anchor
                else initialization["metadata"]["initial"]
            )
            turns = [r["data"] for r in records if r["kind"] == "turn"]
            head = store.journal_head()
        result = replay_turns(turns, state, self.workspace / "artifacts")
        return dict(
            result,
            through=head["sequence"],
            head=head["hash"],
            retained_from=anchor["sequence"] + 1 if anchor else 1,
        )

    def close(self) -> None:
        self.stopping.set()
        self.service.request_stop()
        self.thread.join(110)
        if self.thread.is_alive():
            raise RuntimeError("runtime_shutdown_pending")
        self.service.close()
