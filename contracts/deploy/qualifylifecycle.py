#!/usr/bin/env python3
"""Exercise immutable release upgrade/rollback and live backup of an unknown effect."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import selectors
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import venv
from collections.abc import Callable
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.deploy.installation import install_release
from contracts.deploy.qualificationprocess import (
    install_termination_handler,
    qualification_environment,
    register_process,
    run_qualification,
    stop_process_group,
)
from contracts.deploy.releaseverification import verify_release
from remote_agent.network import HTTPRoom
from remote_agent.store import Store

T = TypeVar("T")


def private(path: Path, value: Any) -> str:
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(0o600)
    return str(path)


def wait_for(fn: Callable[[], T], label: str, timeout: float = 15) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := fn():
            return value
        time.sleep(0.03)
    raise AssertionError("Timed out: " + label)


class FaultProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.port: int | None = None
        self.armed = False
        self.accepted, self.release = threading.Event(), threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.forward()

            def do_POST(self) -> None:
                self.forward()

            def forward(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if outer.port is None:
                    raise RuntimeError("Qualification proxy has no upstream port")
                client = http.client.HTTPConnection("127.0.0.1", outer.port, timeout=5)
                try:
                    client.request(
                        self.command,
                        self.path,
                        body,
                        {
                            "Authorization": self.headers["Authorization"],
                            "Content-Type": "application/json",
                        },
                    )
                    response = client.getresponse()
                    raw, status = response.read(), response.status
                    if (
                        outer.armed
                        and self.path == "/v1/proposals"
                        and json.loads(body).get("kind") == "answer"
                    ):
                        assert status == 200 and json.loads(raw)["status"] == "accepted"
                        outer.armed = False
                        outer.accepted.set()
                        outer.release.wait(30)
                        self.close_connection = True
                        return
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (OSError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    client.close()

        super().__init__(("127.0.0.1", 0), Handler)


def installed(args: argparse.Namespace) -> None:
    install_termination_handler()

    root = args.state
    venv.create(root / "service-venv", with_pip=True)
    python = root / "service-venv/bin/python"
    cli = root / "service-venv/bin/contract-service"
    current = root / "current"
    contract = root / "session.band"
    shutil.copy2(args.candidate / "contracts/v2/hybrid-assistant.band", contract)
    contract.chmod(0o644)
    program = root / "initial-program.py"
    shutil.copy2(args.candidate / "examples/partial_program.py", program)
    program.chmod(0o644)
    for name in ("contracts/v2/hybrid-assistant.band", "examples/partial_program.py"):
        assert (args.baseline / name).read_bytes() == (
            args.candidate / name
        ).read_bytes()
    provider = private(
        root / "provider.py",
        "import json,sys,time,pathlib\nr=json.load(sys.stdin); b=r['bundle']; root=pathlib.Path(sys.argv[1]); id=b['input']['id']\nwith (root/'calls.jsonl').open('a') as f: f.write(json.dumps({'id':id})+'\\n'); f.flush()\nif (root/'slow').exists(): time.sleep(1.5)\nprint(json.dumps({'state':{'count':b['state']['count']+1},'message':{'kind':'answer','payload':{'request':id,'text':'recorded fixture answer '+id}},'scope':'turn'}))\n",
    )
    processes: list[subprocess.Popen[bytes]] = []
    logs = ExitStack()
    proxy = FaultProxy()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    engine = {
        "command": [str(current / "engine/bin/contract-engine")],
        "contract": str(contract),
        "format": "frontmatter",
    }
    room_config = {
        "profile": "band-contract/service/1",
        "kind": "room",
        "engine": engine,
        "store": {"path": str(root / "room/state.sqlite3")},
        "poll_interval_ms": 100,
        "http": {
            "host": "127.0.0.1",
            "port": 0,
            "tokens_file": private(
                root / "room-tokens.json",
                {
                    "u" * 32: {"actor": "user", "permissions": ["room"]},
                    "a" * 32: {"actor": "agent", "permissions": ["room"]},
                },
            ),
        },
        "room": {
            "session": "lifecycle",
            "bindings": {"requester": "user", "assistant": "agent"},
        },
    }
    agent_config = {
        "profile": "band-contract/service/1",
        "kind": "agent",
        "engine": engine,
        "store": {"path": str(root / "agent/state.sqlite3")},
        "poll_interval_ms": 100,
        "http": {
            "host": "127.0.0.1",
            "port": 0,
            "tokens_file": private(
                root / "agent-tokens.json",
                {"o" * 32: {"actor": "operator", "permissions": ["observe"]}},
            ),
        },
        "agent": {
            "actor": "agent",
            "program_source": str(program),
            "artifact_directory": str(root / "artifacts"),
            "room": {
                "url": f"http://127.0.0.1:{proxy.server_port}",
                "token_file": private(root / "room.token", "a" * 32),
                "timeout_ms": 2500,
            },
            "provider": {"command": [str(python), provider, str(root)]},
            "max_repairs": 0,
        },
    }
    active_db = Path(agent_config["store"]["path"])

    def install(release: Path) -> None:
        assert all(p.poll() is not None for p in processes), (
            "Stop both owners before changing the installation"
        )
        install_release(release, python, force=True)
        current.unlink(missing_ok=True)
        current.symlink_to(release, target_is_directory=True)

    def launch(
        config: dict[str, Any], name: str
    ) -> tuple[subprocess.Popen[bytes], str]:
        log = logs.enter_context((root / f"{name}.log").open("ab"))
        os.chmod(log.name, 0o600)
        process = subprocess.Popen(
            [str(cli), "serve", private(root / f"{name}.json", config)],
            stdout=subprocess.PIPE,
            stderr=log,
            env=qualification_environment({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}),
            start_new_session=True,
        )
        processes.append(process)
        register_process(process)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(15), name + " startup timeout"
            raw = process.stdout.readline()
        assert raw, name + " startup failed; inspect private log"
        ready = json.loads(raw)
        assert ready["event"] == "service_ready"
        if name == "room":
            proxy.port = urlsplit(ready["url"]).port
        return process, ready["url"]

    def stop(process: subprocess.Popen[bytes]) -> None:
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0

    def state() -> dict[str, Any]:
        with sqlite3.connect(f"file:{active_db}?mode=ro", uri=True) as db:
            return {
                k: json.loads(v)
                for k, v in db.execute("SELECT key,value FROM current_state")
            }

    def hashes() -> list[str]:
        with sqlite3.connect(f"file:{active_db}?mode=ro", uri=True) as db:
            return [
                h for (h,) in db.execute("SELECT hash FROM journal ORDER BY sequence")
            ]

    def calls() -> list[dict[str, Any]]:
        p = root / "calls.jsonl"
        return (
            [json.loads(line) for line in p.read_text().splitlines()]
            if p.exists()
            else []
        )

    def question(id: str, n: int) -> dict[str, Any]:
        return {
            "id": id,
            "session": "lifecycle",
            "kind": "question",
            "payload": {"text": "free-form " + id},
            "expected_version": n,
        }

    def operator(*argv: str | Path, code: int = 0) -> Any:
        result = subprocess.run(
            [str(cli), *map(str, argv)],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            check=False,
        )
        assert result.returncode == code, ("operator", argv[0], result.returncode)
        return json.loads(result.stdout) if result.stdout else None

    result: dict[str, Any] = {
        "releases": {
            k: json.loads((v / "manifest.json").read_text())["source_commit"]
            for k, v in [("baseline", args.baseline), ("candidate", args.candidate)]
        }
    }
    try:
        install(args.baseline)
        room, url = launch(room_config, "room")
        client = HTTPRoom(url, "u" * 32)
        agent, _ = launch(agent_config, "agent")
        private(root / "slow", "1")
        q1 = question("q1", 0)
        first = client.publish(q1, "user")
        wait_for(lambda: len(calls()) == 1, "baseline model dispatched")
        stop(agent)
        assert (
            state()["staged"]["sends"] == 0
            and state()["model_calls"] == 1
            and state()["turns"] == 0
        )
        before_upgrade = hashes()
        stop(room)
        install(args.candidate)
        room, url = launch(room_config, "room")
        client = HTTPRoom(url, "u" * 32)
        agent, _ = launch(agent_config, "agent")
        wait_for(lambda: state()["turns"] == 1, "upgrade retained result")
        assert state()["state"] == {"count": 1} and len(calls()) == 1
        assert hashes()[: len(before_upgrade)] == before_upgrade
        assert client.publish(q1, "user") == {"status": "duplicate", "original": first}
        result["upgrade"] = {
            "pending_model_result_reused": True,
            "journal_prefix_preserved": True,
            "model_calls": 1,
            "accepted_retry_preserved": True,
        }
        q2 = question("q2", 2)
        second = client.publish(q2, "user")
        wait_for(lambda: len(calls()) == 2, "candidate model dispatched")
        stop(agent)
        assert (
            state()["staged"]["sends"] == 0
            and state()["model_calls"] == 2
            and state()["turns"] == 1
        )
        before_rollback = hashes()
        stop(room)
        install(args.baseline)
        room, url = launch(room_config, "room")
        client = HTTPRoom(url, "u" * 32)
        agent, _ = launch(agent_config, "agent")
        wait_for(lambda: state()["turns"] == 2, "rollback retained result")
        assert state()["state"] == {"count": 2} and len(calls()) == 2
        assert hashes()[: len(before_rollback)] == before_rollback
        assert client.publish(q2, "user") == {"status": "duplicate", "original": second}
        assert client.publish(q1, "user") == {"status": "duplicate", "original": first}
        result["rollback"] = {
            "latest_database_retained": True,
            "pending_model_result_reused": True,
            "journal_prefix_preserved": True,
            "model_calls": 2,
            "both_accepted_retries_preserved": True,
        }
        stop(agent)
        stop(room)
        install(args.candidate)
        (root / "slow").unlink()
        room, url = launch(room_config, "room")
        client = HTTPRoom(url, "u" * 32)
        agent, _ = launch(agent_config, "agent")
        proxy.armed = True
        client.publish(question("q3", 4), "user")
        assert proxy.accepted.wait(10), "accepted-before-acknowledgement fault reached"
        assert client.view("user")["view"]["version"] == 6
        assert state()["staged"]["sends"] == 1 and state()["turns"] == 2
        pending = state()["staged"]["envelope"]
        # The agent still holds its live ownership lock and is waiting on HTTP.
        backup = root / "backup.sqlite3"
        backed = operator("backup", active_db, backup)
        backup_head = operator("inspect", backup)["journal"]["head"]
        stop(agent)
        proxy.release.set()
        original_db = active_db
        active_db = root / "restored/state.sqlite3"
        operator("restore", backup, active_db)
        operator("restore", backup, active_db, code=2)
        assert operator("inspect", active_db)["journal"]["head"] == backup_head
        agent_config["store"]["path"] = str(active_db)
        agent, _ = launch(agent_config, "restored-agent")
        wait_for(lambda: state()["turns"] == 3, "restored unknown outbox reconciled")
        assert (
            state()["state"] == {"count": 3}
            and len(calls()) == state()["model_calls"] == 3
        )
        assert state()["staged"] is None
        agent_client = HTTPRoom(url, "a" * 32)
        assert agent_client.publish(pending, "agent") == {
            "status": "duplicate",
            "original": state()["input:q3"]["result"]["receipt"],
        }
        assert client.view("user")["view"]["version"] == 6
        stop(agent)
        # Recorded replay remains possible after moving the source/provider away.
        archive = root / "journal.jsonl"
        operator("export", active_db, archive)
        with Store(active_db) as store:
            recorded = store.records()
            program_id = next(
                r["data"]["id"] for r in recorded if r["kind"] == "program_input"
            )
            model_id = next(
                r["data"]["id"] for r in recorded if r["kind"] == "model_input"
            )
        (root / "artifacts").rename(root / "artifacts-retained")
        Path(provider).rename(root / "provider-retained.py")
        operator("replay", archive, program_id, root / "program-replay.json")
        operator(
            "replay", archive, model_id, root / "model-replay.json", "--kind", "model"
        )
        assert len(calls()) == 3
        with Store(original_db) as store:
            assert (
                store.get("turns") == 2 and store.get("staged")["envelope"] == pending
            )
        result["restore"] = {
            "live_backup_records": backed["records"],
            "same_backup_head": True,
            "original_database_preserved": True,
            "no_overwrite_enforced": True,
            "unknown_effect_recovered_once": True,
            "agent_turns": 3,
            "room_version": 6,
            "model_calls": 3,
            "replay_without_program_or_provider": True,
        }
        result["configuration"] = {
            "schema": 1,
            "stable_venv_and_artifact_paths": True,
            "both_owners_stopped_for_binary_switch": True,
            "installed_services_without_mix_or_elixir_on_path": True,
        }
        private(root / "result.json", result)
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    finally:
        proxy.release.set()
        proxy.shutdown()
        proxy.server_close()
        for process in reversed(processes):
            stop_process_group(process)
            if process.stdout is not None:
                process.stdout.close()
        logs.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.baseline, args.candidate, args.state = (
        args.baseline.absolute(),
        args.candidate.absolute(),
        args.state.resolve(),
    )
    for release in (args.baseline, args.candidate):
        verify_release(release)
    if args.installed:
        installed(args)
        return
    args.state.mkdir(mode=0o700, parents=True, exist_ok=False)
    venv.create(args.state / "driver-venv", with_pip=True)
    python = args.state / "driver-venv/bin/python"
    install_release(args.candidate, python)
    run_qualification(
        [
            str(python),
            "-I",
            str(Path(__file__).resolve()),
            "--baseline",
            str(args.baseline),
            "--candidate",
            str(args.candidate),
            "--state",
            str(args.state),
            "--installed",
        ],
        check=True,
        timeout=180,
    )


if __name__ == "__main__":
    main()
