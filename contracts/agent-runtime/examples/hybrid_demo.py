# /// script
# requires-python = "==3.12.*"
# dependencies = ["band-sdk[contracts]>=6.0.1"]
# ///
"""Exercise the retained hybrid fixture with local engines and scripted commands."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.program import Artifact, Program
from remote_agent.providers import CommandProvider, CommandValidator
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate, ReferenceRoom
from remote_agent.types import JsonObject


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="A fresh directory to retain the journal and generated artifacts",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if (workspace / "agent.sqlite3").exists():
        raise ValueError(
            "Choose a fresh workspace; the demo never replaces an existing journal"
        )
    examples = Path(__file__).resolve().parent
    contract = examples.parents[1] / "contract-lab/contracts/hybrid-assistant.band"
    with (
        closing(EngineProcess(contract)) as authority,
        closing(EngineProcess(contract)) as checker,
    ):
        store = agent = endpoint = None
        try:
            assert authority.process.pid != checker.process.pid
            room = ReferenceRoom(
                authority,
                session="demo",
                bindings={"requester": "user", "assistant": "agent"},
            )
            artifact = Artifact.publish(
                (examples / "partial_program.py").read_text(), workspace / "artifacts"
            )
            provider = CommandProvider(
                [sys.executable, str(examples / "scripted_sidecar.py"), str(workspace)],
                workspace / "artifacts",
                timeout=20,
            )
            validator = CommandValidator(
                [sys.executable, str(examples / "check_candidate.py")]
            )
            options = {
                "actor": "agent",
                "artifact_directory": workspace / "artifacts",
                "now": lambda: 0,
                "validator": validator,
                "snapshot_every": 2,
                "control_actors": {
                    "user": ["wake", "takeover", "resume", "repair", "retry"]
                },
            }
            room_token, user_token = (
                secrets.token_urlsafe(32),
                secrets.token_urlsafe(32),
            )
            tokens = {
                room_token: {"actor": "room", "events": True},
                user_token: {"actor": "user", "events": False},
            }
            store = Store(workspace / "agent.sqlite3")
            agent = HybridAgent(
                store, Program(artifact), room, LocalGate(checker), provider, **options
            )
            endpoint = Endpoint(agent, tokens, notify=agent.notify)
            transcript = []

            def post(path: str, data: JsonObject, token: str) -> JsonObject:
                assert endpoint is not None
                req = urllib.request.Request(
                    endpoint.url + path,
                    data=json.dumps(data).encode(),
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    assert response.status == 200
                    return json.loads(response.read())

            def ask(text: str, producer: str) -> tuple[JsonObject, JsonObject]:
                assert agent is not None
                version = room.view("user", 0)["view"]["version"]
                event = room.publish(
                    {
                        "id": "q" + str(version),
                        "session": "demo",
                        "kind": "question",
                        "payload": {"text": text},
                        "expected_version": version,
                    },
                    "user",
                    0,
                )
                assert event["status"] == "accepted"
                assert post("/events", event, room_token) == {"status": "notified"}
                # This single demo driver publishes one question at a time and is
                # its ordered consumer. Never execute a turn in the HTTP handler.
                # The supervised service uses the durable authority inbox instead.
                result = agent.receive(event)
                assert result["producer"] == producer
                assert result["receipt"]["kind"] == "answer"
                transcript.append(
                    {
                        "input": text,
                        "producer": producer,
                        "answer": result["receipt"]["payload"]["text"],
                    }
                )
                return event, result

            first, completed = ask("ping", "program")
            assert store.get("model_calls") == 0
            assert post("/events", first, room_token) == {"status": "notified"}
            assert agent.receive(first) == completed
            ask("Interpret this open-ended English request", "llm")
            assert store.get("mode") == "program"
            ask("recover", "llm")
            deadline = time.monotonic() + 5
            while not (workspace / "repair-started").exists():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert not (workspace / "release-repair").exists()
            ask("ping", "llm")
            assert store.get("repair")["status"] == "running"
            (workspace / "release-repair").touch()
            assert agent.wait_for_repair(10)
            assert store.get("repair")["status"] == "installed", store.get("repair")
            assert store.get("program")["sha256"] != artifact.sha256
            ask("recover", "program")
            wake = {
                "id": "wake-demo",
                "action": "wake",
                "reason": "Room participant requests diagnosis",
            }
            first_wake = post("/controls", wake, user_token)
            calls = store.get("model_calls")
            assert post("/controls", wake, user_token) == first_wake
            assert store.get("model_calls") == calls
            post(
                "/controls",
                {
                    "id": "take-demo",
                    "action": "takeover",
                    "reason": "Keep model judgment active",
                },
                user_token,
            )
            ask("ping", "llm")
            post(
                "/controls",
                {
                    "id": "resume-demo",
                    "action": "resume",
                    "reason": "Validated program may resume",
                },
                user_token,
            )
            ask("ping", "program")
            calls_before_restart = store.get("model_calls")
            snapshot = store.latest_snapshot()
            assert snapshot is not None
            snapshot_before_restart = snapshot["sequence"]
            endpoint.close()
            agent.close()
            store.close()
            store = Store(workspace / "agent.sqlite3")
            agent = HybridAgent(
                store, Program(artifact), room, LocalGate(checker), provider, **options
            )
            endpoint = Endpoint(agent, tokens, notify=agent.notify)
            assert post("/controls", wake, user_token) == first_wake
            ask("ping", "program")
            assert store.get("model_calls") == calls_before_restart
            assert store.get("state") == {"count": 8}
            summary = {
                "status": "passed",
                "provider": "scripted command fixture; no live model or token measurement",
                "separate_authority_and_checker_processes": True,
                "http_events_and_controls": True,
                "http_events_are_notifications": True,
                "single_ordered_demo_consumer": True,
                "ordinary_first_turn_model_calls": 0,
                "model_calls_total": store.get("model_calls"),
                "model_operations": [
                    r["data"]["operation"]
                    for r in store.records()
                    if r["kind"] == "model_call"
                ],
                "turns": store.get("turns"),
                "state": store.get("state"),
                "snapshot_sequence_before_restart": snapshot_before_restart,
                "continued_while_repair_waited": True,
                "repair": store.get("repair")["status"],
                "journal_database": str(store.path),
                "transcript": transcript,
            }
            (workspace / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
            sys.stdout.write(json.dumps(summary, indent=2) + "\n")
        finally:
            try:
                if endpoint:
                    endpoint.close()
            finally:
                # A failed agent shutdown can leave repair work using its store.
                if agent:
                    agent.close()
                if store:
                    store.close()


if __name__ == "__main__":
    main()
