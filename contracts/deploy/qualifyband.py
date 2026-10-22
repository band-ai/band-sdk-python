#!/usr/bin/env python3
"""Qualify a sealed installed agent against three real, isolated Band nodes.

Requires the Band qualification database at 127.0.0.1:5488, already migrated.
All processes and credentials belong to the new private --state directory.
The script stops its own processes on success or failure; it never drops a DB.
"""

from __future__ import annotations

import argparse
import http.client as http_client
import json
import os
import secrets
import selectors
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import venv
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.deploy.bandprobes import (
    probe_access_and_creation,
    probe_invalid_requests,
    schema_checked_client,
    validate_response,
)
from contracts.deploy.environment import BuildSettings
from contracts.deploy.installation import install_release
from contracts.deploy.qualificationprocess import (
    install_termination_handler,
    qualification_environment,
    register_process,
    run_qualification,
    stop_process_group,
)
from contracts.deploy.releasecontract import file_source
from contracts.deploy.releaseverification import verify_release
from remote_agent.network import HTTPRoom
from remote_agent.store import Store

HERE = Path(__file__).resolve().parent
PORTS = [4511, 4512, 4513]
RESPONSE_SCHEMA: dict[str, Any] | None = None


T = TypeVar("T")


def private(path: Path, value: Any) -> str:
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(value if isinstance(value, str) else json.dumps(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(path)


def wait_for(fn: Callable[[], T], name: str, timeout: float = 20) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out: " + name)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def http(
    port: int,
    path: str,
    key: str,
    body: Any = None,
    expected: int = 200,
    method: str | None = None,
) -> Any:
    client = http_client.HTTPConnection("127.0.0.1", port, timeout=10)
    encoded = None if body is None else json.dumps(body).encode()
    try:
        client.request(
            method or ("GET" if body is None else "POST"),
            path,
            encoded,
            {"X-API-Key": key, "Content-Type": "application/json"},
        )
        response = client.getresponse()
        raw = response.read(4_194_305)
        assert response.status == expected, (path, response.status, expected)
        assert len(raw) <= 4_194_304
        value = json.loads(raw)
        if "/contracts" in path:
            if RESPONSE_SCHEMA is None:
                raise RuntimeError(
                    "Band response schema was not selected from the release"
                )
            validate_response(value, response.status, path, RESPONSE_SCHEMA)
        return value
    finally:
        client.close()


class LostAcknowledgement(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.armed = False
        self.accepted = threading.Event()
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.forward()

            def do_POST(self) -> None:
                self.forward()

            def forward(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                upstream = http_client.HTTPConnection("127.0.0.1", PORTS[0], timeout=5)
                try:
                    upstream.request(
                        self.command,
                        self.path,
                        body,
                        {
                            "X-API-Key": self.headers["X-API-Key"],
                            "Content-Type": "application/json",
                        },
                    )
                    response = upstream.getresponse()
                    raw, status = response.read(), response.status
                    if (
                        outer.armed
                        and self.path.endswith("/proposals")
                        and json.loads(body).get("kind") == "answer"
                    ):
                        assert status == 200 and json.loads(raw)["status"] == "accepted"
                        outer.armed = False
                        outer.accepted.set()
                        outer.release.wait(30)
                        self.close_connection = True
                        return  # Destination committed; client never receives this reply.
                except (OSError, http_client.HTTPException):
                    status, raw = 503, b'{"error":"unavailable"}'
                finally:
                    upstream.close()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass

        super().__init__(("127.0.0.1", 0), Handler)


def qualify(args: argparse.Namespace) -> None:
    install_termination_handler()

    global RESPONSE_SCHEMA
    root, release, platform = args.state, args.state / "release", args.platform
    RESPONSE_SCHEMA = read_json(
        release / "contracts/spec/schemas/protocol-v2.schema.json"
    )
    processes: list[subprocess.Popen[bytes]] = []
    hosts: dict[int, subprocess.Popen[bytes]] = {}
    handles = ExitStack()
    results: dict[str, Any] = {}
    names = [f"contract-qualification-{root.name}-{i}@127.0.0.1" for i in range(1, 4)]
    proxy = LostAcknowledgement()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    agent = None

    def launch(
        argv: Sequence[str],
        name: str,
        *,
        env: dict[str, str] | None = None,
        cwd: Path = root,
        stdout: int | BinaryIO | None = None,
    ) -> subprocess.Popen[bytes]:
        log = handles.enter_context((root / (name + ".log")).open("ab"))
        os.chmod(log.name, 0o600)
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=stdout or log,
            stderr=log,
            start_new_session=True,
        )
        processes.append(process)
        register_process(process)
        return process

    def stop(process: subprocess.Popen[bytes], crash: bool = False) -> None:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL if crash else signal.SIGTERM)
            code = process.wait(timeout=15)
            if not crash and process is agent:
                assert code == 0, ("agent shutdown", code)

    def status(i: int) -> dict[str, Any]:
        return read_json(root / f"status-{i}.json", {})

    def start_host(i: int) -> None:
        (root / f"status-{i}.json").unlink(missing_ok=True)
        env = dict(
            BuildSettings().environment([], mix_env="test"),
            ERL_FLAGS="+S 2:2",
            CONTRACT_LAB_STATE=str(root),
            CONTRACT_LAB_INDEX=str(i),
            CONTRACT_LAB_PORT=str(PORTS[i - 1]),
            CONTRACT_LAB_NODE=names[i - 1],
            CONTRACT_LAB_PEERS=",".join(n for n in names if n != names[i - 1]),
            CONTRACT_LAB_CONTRACT=str(release / "contracts/v2/hybrid-assistant.band"),
        )
        process = launch(
            ["mix", "run", "--no-start", "--no-halt", str(HERE / "band_host.exs")],
            f"host-{i}",
            env=env,
            cwd=platform,
        )
        hosts[i] = process

        def ready() -> bool:
            assert process.poll() is None, (
                f"host {i} exited",
                process.returncode,
                f"host-{i}.log",
            )
            return status(i).get("pool") == "DBConnection.ConnectionPool"

        wait_for(ready, f"host {i} normal database pool", 60)

    def agent_start(config: dict[str, Any]) -> subprocess.Popen[bytes]:
        process = launch(
            [
                str(Path(sys.executable).parent / "contract-service"),
                "serve",
                private(root / "agent.json", config),
            ],
            "agent",
            env=qualification_environment({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}),
            stdout=subprocess.PIPE,
        )
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(15), "agent startup deadline"
            line = process.stdout.readline()
        assert line and json.loads(line)["event"] == "service_ready", (
            "agent failed to start; inspect private log"
        )
        return process

    def state() -> dict[str, Any]:
        with sqlite3.connect(
            f"file:{root}/agent/state.sqlite3?mode=ro", uri=True
        ) as db:
            return {
                key: json.loads(value)
                for key, value in db.execute("SELECT key,value FROM current_state")
            }

    try:
        for i in range(1, 4):
            start_host(i)
        wait_for(
            lambda: all(
                sorted(status(i).get("peers", []))
                == sorted(n for n in names if n != names[i - 1])
                for i in range(1, 4)
            ),
            "full three-node mesh",
        )
        data = read_json(root / "fixture.json")
        session, room = data["session"], data["room"]
        base = f"/api/v1/agent/chats/{room}/contracts"
        agent_base = f"/api/v1/agent/chats/{room}/contracts/{session}"
        request = {
            "id": session,
            "source": data["source"],
            "config": {
                "bindings": {
                    "requester": "Agent:" + data["requester"],
                    "assistant": "Agent:" + data["agent"],
                },
                "inactivity": "accepted",
                "max_messages": 100,
                "max_bytes": 16384,
            },
        }
        identity = http(PORTS[0], base, data["requester_key"], request, expected=201)
        results["access_and_creation"] = probe_access_and_creation(
            http, PORTS, data, request, identity
        )
        results["invalid_requests"] = probe_invalid_requests(http, PORTS, data, request)
        clients = [
            schema_checked_client(
                HTTPRoom(
                    f"http://127.0.0.1:{port}{base}/{session}",
                    data["requester_key"],
                    authentication="band-api-key",
                ),
                RESPONSE_SCHEMA,
            )
            for port in PORTS
        ]
        client = clients[0]
        actor = "Agent:" + data["requester"]
        for i, port in enumerate(PORTS, 1):
            output = private(root / f"socket-{i}.jsonl", "")
            launch(
                [
                    "node",
                    str(HERE / "band_socket.mjs"),
                    str(root / "fixture.json"),
                    str(port),
                    output,
                ],
                f"socket-{i}",
                cwd=platform,
            )
            wait_for(
                lambda output=output: '"status":"ok"' in Path(output).read_text(),
                f"node {i} authenticated websocket join",
            )

        def question(id: str, version: int, text: str = "ping") -> dict[str, Any]:
            return {
                "id": id,
                "session": session,
                "kind": "question",
                "payload": {"text": text},
                "expected_version": version,
            }

        def version(n: int) -> bool:
            return wait_for(
                lambda: client.view(actor)["view"]["version"] == n,
                f"authority version {n}",
            )

        # Concurrent requests cross three BEAM nodes and independent PG sessions.
        q1 = question("q1", 0)
        start_us = time.time_ns() // 1000
        with ThreadPoolExecutor(max_workers=12) as pool:
            decisions = list(
                pool.map(lambda n: clients[n % 3].publish(q1, actor), range(12))
            )
        accepted = [d for d in decisions if d["status"] == "accepted"]
        assert (
            len(accepted) == 1
            and sum(
                d == {"status": "duplicate", "original": accepted[0]} for d in decisions
            )
            == 11
        )
        results["concurrent_duplicate"] = {
            "requests": 12,
            "accepted": 1,
            "identical_duplicates": 11,
        }
        deliveries = []
        for i in range(1, 4):
            path = root / f"socket-{i}.jsonl"

            def received(path: Path = path) -> list[dict[str, Any]]:
                return [
                    j
                    for j in map(json.loads, path.read_text().splitlines())
                    if j["event"] == "event_created"
                ]

            frames = wait_for(received, f"node {i} real receipt notification")
            assert len(frames) == 1
            raw = json.dumps(frames[0]["payload"])
            assert (
                "Contract event accepted." in raw
                and "hybrid-assistant" not in raw
                and "payload" not in raw
            )
            deliveries.append({"node": i, "latency_us": frames[0]["at_us"] - start_us})
        results["three_node_websocket"] = deliveries

        provider = private(
            root / "provider.py",
            "import json,sys\nr=json.load(sys.stdin)\nb=r['bundle']\nprint(json.dumps({'state':{'count':b['state']['count']+1},'message':{'kind':'answer','payload':{'request':b['input']['id'],'text':'scripted transport fixture answer'}},'scope':'turn'}))\n",
        )
        config = {
            "profile": "band-contract/service/1",
            "kind": "agent",
            "store": {"path": str(root / "agent/state.sqlite3")},
            "poll_interval_ms": 100,
            "engine": {
                "command": [str(release / "engine/bin/contract-engine")],
                "contract": str(release / "contracts/v2/hybrid-assistant.band"),
                "format": "frontmatter",
            },
            "http": {
                "host": "127.0.0.1",
                "port": 0,
                "tokens_file": private(
                    root / "tokens.json",
                    {"o" * 32: {"actor": "operator", "permissions": ["observe"]}},
                ),
            },
            "agent": {
                "actor": "Agent:" + data["agent"],
                "program_source": str(release / "examples/partial_program.py"),
                "artifact_directory": str(root / "agent/artifacts"),
                "max_repairs": 0,
                "room": {
                    "url": f"http://127.0.0.1:{proxy.server_port}{agent_base}",
                    "token_file": private(root / "band.token", data["agent_key"]),
                    "authentication": "band-api-key",
                    "timeout_ms": 2000,
                },
                "provider": {"command": [sys.executable, provider]},
            },
        }
        agent = agent_start(config)
        version(2)
        canary = "role-private-qualification-" + secrets.token_hex(8)
        client.publish(question("q2", 2, canary), actor)
        version(4)
        client.publish(question("q3", 4), actor)
        version(6)
        wait_for(lambda: state().get("turns") == 3, "third local turn commit")
        assert (state()["state"], state()["model_calls"]) == ({"count": 3}, 1)
        stop(agent)
        client.publish(question("q4", 6), actor)
        agent = agent_start(config)
        version(8)
        wait_for(lambda: state().get("turns") == 4, "offline inbox turn commit")
        results["installed_routes_and_offline_inbox"] = {
            "turns": 4,
            "program_turns": 3,
            "scripted_model_calls": 1,
            "state_count": state()["state"]["count"],
        }
        assert state()["model_calls"] == 1 and state()["state"] == {"count": 4}
        mirrors = http(
            PORTS[1], f"/api/v1/me/chats/{room}/messages", data["observer_key"]
        )
        assert canary not in json.dumps(mirrors)
        for i in range(1, 4):
            socket_log = root / f"socket-{i}.jsonl"
            wait_for(
                lambda socket_log=socket_log: (
                    sum(
                        json.loads(line)["event"] == "event_created"
                        for line in socket_log.read_text().splitlines()
                    )
                    == 8
                ),
                f"node {i} eight receipt notifications",
            )
            assert canary not in socket_log.read_text()
        results["cross_tenant_nonmember_denied_and_timeline_private"] = (
            results["access_and_creation"]["denial_probes"] == 48
        )

        # Force a real commit followed by a lost response, then kill all authorities.
        proxy.armed = True
        client.publish(question("q5", 8, "free-form lost reply fixture"), actor)
        assert proxy.accepted.wait(10), "lost-response fault not reached"
        wait_for(
            lambda: status(1).get("receipts") == 10,
            "durable accepted answer before crash",
        )
        results["hosts_before_crash"] = [
            {
                k: v
                for k, v in status(i).items()
                if k in ("node", "peers", "pool", "port", "telemetry")
            }
            for i in range(1, 4)
        ]
        for process in hosts.values():
            stop(process, crash=True)
        stop(agent)
        proxy.release.set()
        with Store(root / "agent/state.sqlite3") as store:
            assert store.get("turns") == 4 and store.get("staged")["sends"] == 1
            assert store.get("model_calls") == 2
            pending = store.get("staged")["envelope"]
        for i in range(1, 4):
            start_host(i)
        wait_for(
            lambda: all(len(status(i).get("peers", [])) == 2 for i in range(1, 4)),
            "restored mesh",
        )
        agent = agent_start(config)
        wait_for(lambda: state().get("turns") == 5, "pending outbox recovered")
        assert (
            state()["staged"] is None
            and state()["state"] == {"count": 5}
            and state()["model_calls"] == 2
        )
        agent_client = schema_checked_client(
            HTTPRoom(
                f"http://127.0.0.1:{PORTS[2]}{agent_base}",
                data["agent_key"],
                authentication="band-api-key",
            ),
            RESPONSE_SCHEMA,
        )
        retried = agent_client.publish(pending, "Agent:" + data["agent"])
        assert retried == {
            "status": "duplicate",
            "original": state()["input:q5"]["result"]["receipt"],
        }
        wait_for(
            lambda: status(1).get("receipts") == status(1).get("messages") == 10,
            "exactly ten durable effects",
        )
        assert status(1)["mirrors_safe"]
        results["all_nodes_crash_after_commit_before_reply"] = {
            "room_receipts": 10,
            "chat_messages": 10,
            "agent_turns": 5,
            "model_calls_before": 2,
            "model_calls_after": state()["model_calls"],
            "original_receipt_recovered": True,
        }
        stop(agent)

        command_id = str(uuid.uuid4())
        private(root / "command-2.json", {"id": command_id, "operation": "revoke"})
        wait_for(
            lambda: read_json(root / "command-done-2.json", {}).get("id") == command_id,
            "membership revocation committed",
        )
        for port in PORTS:
            http(port, agent_base + "/v1/identity", data["agent_key"], expected=404)
            http(
                port,
                agent_base + "/v1/inbox",
                data["agent_key"],
                {"after": 0, "limit": 100},
                expected=404,
            )
            http(
                port,
                agent_base + "/v1/proposals",
                data["agent_key"],
                pending,
                expected=404,
            )
        results["revocation_all_nodes_including_duplicate"] = True
        # Timer check without view/publish activity after creation.
        expiring = json.loads(json.dumps(request))
        expiring["id"] = str(uuid.uuid4())
        expiring["source"] = file_source(
            release / "contracts/v2/hybrid-assistant.band", clock="1s"
        )
        command_id = str(uuid.uuid4())
        private(root / "command-2.json", {"id": command_id, "operation": "restore"})
        wait_for(
            lambda: read_json(root / "command-done-2.json", {}).get("id") == command_id,
            "membership restored",
        )
        http(PORTS[0], base, data["requester_key"], expiring, expected=201)
        wait_for(
            lambda: any(
                s["id"] == expiring["id"] and s["status"] == "terminal"
                for s in status(3).get("sessions", [])
            ),
            "idle timer persisted expiry",
        )
        results["background_expiry_normal_pool"] = True
        results["hosts"] = [
            {
                k: v
                for k, v in status(i).items()
                if k in ("node", "peers", "pool", "port", "telemetry")
            }
            for i in range(1, 4)
        ]
        results["release"] = {
            k: v
            for k, v in read_json(release / "manifest.json").items()
            if k in ("source_commit", "source_dirty", "target", "engine_build")
        }
        results["band_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=platform, text=True
        ).strip()
        private(root / "result.json", results)
        sys.stdout.write(json.dumps(results, indent=2) + "\n")
        sys.stdout.flush()
    finally:
        proxy.release.set()
        proxy.shutdown()
        proxy.server_close()
        for process in reversed(processes):
            stop_process_group(process)
            if process.stdout:
                process.stdout.close()
        handles.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.release, args.platform, args.state = (
        args.release.absolute(),
        args.platform.resolve(),
        args.state.resolve(),
    )
    verify_release(args.release)
    if args.installed:
        qualify(args)
        return
    args.state.mkdir(mode=0o700, parents=True, exist_ok=False)
    for port in PORTS:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
    shutil.copytree(args.release, args.state / "release")
    private(args.state / "cookie", secrets.token_hex(32))
    venv.create(args.state / "venv", with_pip=True)
    python = args.state / "venv/bin/python"
    install_release(args.state / "release", python)
    run_qualification(
        [
            str(python),
            "-I",
            str(Path(__file__).resolve()),
            "--release",
            str(args.release),
            "--platform",
            str(args.platform),
            "--state",
            str(args.state),
            "--installed",
        ],
        check=True,
        timeout=300,
    )


if __name__ == "__main__":
    main()
