"""Role-scoped supervised participants; legacy runtime configurations stay separate."""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

from remote_agent.band.arithmetic import (
    RuntimeDriver,
    validate_candidate,
    write_runtime_configuration,
)
from remote_agent.band.configuration import model_arguments, module_command
from remote_agent.band.contractsource import materialize_source
from remote_agent.band.effects import EffectRunner
from remote_agent.band.publication import immutable_bytes
from remote_agent.band.quality import checked_policy
from remote_agent.network import HTTPRoom
from remote_agent.service import Service, load_config, read_file
from remote_agent.storage import canonical, digest, private_directory, private_file
from remote_agent.types import JsonObject, SourcePath


def immutable_file(path: Path, content: str) -> None:
    immutable_bytes(
        path, content.encode("utf-8"), conflict="pinned_runtime_source_changed"
    )


def secret_file(path: Path, content: str) -> None:
    with os.fdopen(private_file(path), "wb") as target:
        target.truncate(0)
        target.write(content.encode("utf-8"))
        target.flush()
        os.fsync(target.fileno())


class GenericRuntimeDriver(RuntimeDriver):
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
        self.hold, self.business_hold, self.last_seen = None, None, None
        self.stopping, self.last_retained_turns = threading.Event(), -1
        self.role = assessment["role"]
        source = value["context"]["source"]
        quality = checked_policy(
            assessment["quality"], role=self.role, contract_sha256=source["sha256"]
        )
        if assessment["quality_sha256"] != digest(quality):
            raise ValueError("quality_policy_identity_conflict")
        if self.options["mode"] not in assessment["allowed_modes"]:
            raise ValueError("execution_mode_not_qualified")
        bindings = value["context"]["config"]["bindings"]
        actor = bindings[self.role]
        agent_key = host_config["agent_keys"].get(actor)
        if not agent_key:
            raise ValueError("agent_credential_not_configured")
        verdict = validate_candidate(
            assessment["source"],
            self.workspace / "initial-validation",
            durable=self.durable,
            qualification=quality,
            role=self.role,
            contract_sha256=source["sha256"],
        )
        if verdict["program_sha256"] != assessment["program_sha256"]:
            raise ValueError("qualified_program_identity_changed")
        self.quality_sha256 = verdict["quality_sha256"]
        program_path = self.workspace / "initial.py"
        policy_path = self.workspace / "quality.json"
        key_path, tokens_path = (
            self.workspace / "room.key",
            self.workspace / "controls.json",
        )
        immutable_file(program_path, assessment["source"])
        contract_path, contract_format = materialize_source(
            self.workspace, source, conflict="pinned_runtime_source_changed"
        )
        immutable_file(policy_path, canonical(quality).decode("utf-8"))
        secret_file(key_path, read_file(Path(agent_key), maximum=2048).decode("ascii"))
        secret_file(
            tokens_path,
            json.dumps(
                {
                    secrets.token_urlsafe(32): {
                        "actor": value["actor"],
                        "permissions": ["controls", "observe"],
                    }
                }
            ),
        )
        room_url = (
            host_config["platform_url"]
            + f"/api/v1/agent/chats/{value['room']}/contracts/{value['session']}"
        )
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
                "command": list(host_config["engine_command"]),
                "contract": str(contract_path),
                "format": contract_format,
            },
            "http": {"host": "127.0.0.1", "port": 0, "tokens_file": str(tokens_path)},
            "agent": {
                "actor": actor,
                "program_source": str(program_path),
                "artifact_directory": str(self.workspace / "artifacts"),
                "room": {
                    "url": room_url,
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
                    "command": module_command(
                        "program_validator",
                        "--qualification",
                        str(policy_path),
                        "--role",
                        self.role,
                        "--contract-sha256",
                        source["sha256"],
                    ),
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
        if self.options["mode"] == "llm":
            config["agent"]["initial_route"] = "llm"
        config_path = self.workspace / "service.json"
        write_runtime_configuration(config_path, config)
        self.service = Service(load_config(config_path))
        try:
            self.task_rooms = {}
            for role, bound_actor in bindings.items():
                credential = host_config["agent_keys"].get(bound_actor)
                if role == self.role or not credential:
                    continue
                room = HTTPRoom(
                    room_url,
                    read_file(Path(credential), maximum=2048).decode("ascii").strip(),
                    authentication="band-api-key",
                )
                if (
                    room.actor != bound_actor
                    or room.session != self.service.room.session
                    or room.identity != self.service.room.identity
                ):
                    raise ValueError("counterparty_authority_mismatch")
                self.task_rooms[role] = room
            self.thread = threading.Thread(
                target=self.run,
                daemon=True,
                name="role-program-" + value["session"][:8],
            )
            self.thread.start()
        except BaseException:
            self.service.close()
            raise

    def summary(self) -> JsonObject:
        return dict(
            super().summary(),
            task_roles=sorted(self.task_rooms),
            quality_sha256=self.quality_sha256,
        )

    def verify_business_results(self) -> None:
        # Generic qualification is reported separately. Schema acceptance or a
        # finite semantic suite is not a per-turn proof of arbitrary business truth.
        return None

    def task(self, body: JsonObject) -> JsonObject:
        role = body.get("sender_role")
        if role is None:
            if len(self.task_rooms) != 1:
                raise ValueError("sending_role_required")
            role = next(iter(self.task_rooms))
        room = self.task_rooms.get(role)
        if room is None:
            raise ValueError("sending_role_not_granted")
        store, identifier = self.service.store, body["id"]
        prior = store.get("effect:" + identifier)
        if prior:
            envelope = prior["request"]
            request = envelope["proposal"]
            if (
                envelope["sender_role"] != role
                or envelope["actor"] != room.actor
                or request["kind"] != body["kind"]
                or canonical(request["payload"]) != canonical(body["payload"])
            ):
                raise ValueError("effect_identity_conflict")
        else:
            view = room.view(room.actor)["view"]
            request = {
                "id": identifier,
                "session": room.session,
                "kind": body["kind"],
                "payload": body["payload"],
                "expected_version": view["version"],
            }
            envelope = {"sender_role": role, "actor": room.actor, "proposal": request}

        def dispatch(sent: JsonObject) -> JsonObject:
            return room.publish(sent["proposal"], sent["actor"])

        def validate(result: JsonObject, sent: JsonObject) -> bool:
            if result.get("status") == "rejected":
                return isinstance(result.get("reason"), str)
            receipt = result.get("original", result)
            proposal = sent["proposal"]
            return (
                result.get("status") in ("accepted", "duplicate")
                and receipt.get("id") == proposal["id"]
                and receipt.get("session") == proposal["session"]
                and receipt.get("actor") == sent["actor"]
            )

        decision = EffectRunner(store).run(identifier, envelope, dispatch, validate)
        return {"proposal_id": identifier, "decision": decision}
