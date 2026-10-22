#!/usr/bin/env python3
"""Qualify a sealed release in owned, transient system-manager service units.

Requires noninteractive sudo and pre-created contract-room/contract-agent users
and groups. Uses their shipped static identities, unique /opt and StateDirectory
paths, only loopback listeners, synthetic credentials, and scripted responses.
The private state directory retains logs/databases; owned host paths are removed.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

if sys.platform != "win32":
    import grp
    import pwd

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.deploy.installation import installation_commands
from contracts.deploy.releaseverification import verify_release
from contracts.deploy.unitconfig import UnitParser

T = TypeVar("T")


def required_protections(kind: str) -> dict[str, str]:
    """Independent acceptance policy, in systemctl-show's effective spelling."""
    required = {
        "User": "contract-" + kind,
        "Group": "contract-" + kind,
        "DynamicUser": "no",
        "StateDirectoryMode": "0700",
        "UMask": "0077",
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "PrivateDevices": "yes",
        "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6"
        + (" AF_NETLINK" if kind == "agent" else ""),
        "MemoryMax": str((512 if kind == "room" else 1024) * 1024 * 1024),
        "TasksMax": "128",
        "LimitNOFILE": "1024",
        "LimitCORE": "0",
    }
    if kind == "room":
        required.update(
            {
                "RestrictNamespaces": "yes",
                "ProtectKernelTunables": "yes",
                "ProtectKernelModules": "yes",
                "ProtectKernelLogs": "yes",
                "ProtectControlGroups": "yes",
                "ProtectClock": "yes",
                "ProtectHostname": "yes",
                "RestrictRealtime": "yes",
                "CapabilityBoundingSet": "",
                "AmbientCapabilities": "",
                "SystemCallFilter": "@system-service",
                "SystemCallErrorNumber": "1",
            }
        )
    return required


@functools.cache
def system_service_syscalls() -> frozenset[str]:
    """Expand this host's systemd groups, not a version-specific syscall list."""
    pending, groups, calls = ["@system-service"], set(), set()
    while pending:
        group = pending.pop()
        if group in groups:
            continue
        groups.add(group)
        output = run(["systemd-analyze", "syscall-filter", group]).stdout
        for line in re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines():
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            if name.startswith("@"):
                if name != group:
                    pending.append(name)
            else:
                calls.add(name)
    if not calls:
        raise AssertionError("SystemCallFilter: could not expand @system-service")
    return frozenset(calls)


def assert_effective_protections(kind: str, actual: dict[str, str]) -> None:
    for key, expected in required_protections(kind).items():
        observed = actual.get(key)
        matches = (
            set(observed.split()) == set(expected.split())
            if key == "RestrictAddressFamilies" and observed is not None
            else observed == expected
        )
        if key == "SystemCallFilter" and not matches and observed:
            matches = (
                not observed.startswith("~")
                and set(observed.split()) == system_service_syscalls()
            )
        if not matches:
            raise AssertionError(
                f"{kind}: {key}: expected {expected!r}, got {observed!r}"
            )


def static_identities() -> dict[str, tuple[int, int]]:
    """Fail before privileged changes; qualification never provisions accounts."""
    if sys.platform == "win32":
        raise RuntimeError(
            "Static service qualification requires POSIX users and groups"
        )
    try:
        identities = {
            kind: (
                pwd.getpwnam("contract-" + kind).pw_uid,
                grp.getgrnam("contract-" + kind).gr_gid,
            )
            for kind in ("room", "agent")
        }
    except KeyError as exc:
        raise RuntimeError(
            "Qualification requires pre-created static contract-room and "
            "contract-agent users and groups"
        ) from exc
    if (
        any(uid == 0 or gid == 0 for uid, gid in identities.values())
        or identities["room"][0] == identities["agent"][0]
    ):
        raise RuntimeError("Qualification requires distinct non-root static users")
    return identities


def run(
    argv: Sequence[str | Path],
    *,
    root: bool = False,
    timeout: float = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (["sudo", "-n"] if root else []) + list(map(str, argv)),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def private(path: Path, value: Any) -> str:
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(0o600)
    return str(path)


def wait_for(callback: Callable[[], T], label: str, timeout: float = 20) -> T:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = callback()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out: " + label)


def request(
    port: int, path: str, token: str | None = None, body: Any = None
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            json.dumps(body) if body is not None else None,
            headers,
        )
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw)
    finally:
        connection.close()


PROBES = """
# Qualification probes run inside the installed callback boundary.
_original_handle = handle
def handle(event, state, context):
    import os, socket, subprocess
    from pathlib import Path
    assert os.environ.get('QUALIFICATION_PROVIDER_CANARY') is None
    assert not Path('__AGENT_DB__').exists()
    assert not Path('__ROOM_DB__').exists()
    assert not Path('__HOST_PID__').exists()
    for operation in (lambda: socket.socket(),
                      lambda: subprocess.run(['/usr/bin/true']),
                      lambda: Path('/program/program.py').write_text('changed')):
        try:
            operation()
        except OSError:
            pass
        else:
            raise AssertionError('Isolation probe unexpectedly succeeded')
    try:
        allocation = bytearray(300 * 1024 * 1024)
    except MemoryError:
        pass
    else:
        raise AssertionError('Callback memory ceiling not enforced')
    return _original_handle(event, state, context)
"""

PROVIDER = """
import json, os, pathlib, sys, time
r = json.load(sys.stdin)
root = pathlib.Path.cwd()
assert os.environ['QUALIFICATION_PROVIDER_CANARY'] == 'synthetic-only'
with (root / 'provider-calls.jsonl').open('a') as output:
    output.write(json.dumps({'operation': r['operation']}) + '\\n')
    output.flush()
if r['operation'] == 'repair':
    (root / 'repair-dispatched').write_text('started')
    time.sleep(2)
    source = r['program_source'].replace('Message("question", {"request": event["id"], "text": "recovered"})',
                                        'Message("answer", {"request": event["id"], "text": "recovered"})')
    assert source != r['program_source']
    print(json.dumps({'source': source, 'resume': True}))
else:
    b = r['bundle']
    text = 'recovered' if b['input']['payload']['text'] == 'recover' else 'scripted language answer'
    print(json.dumps({'state': {'count': b['state']['count'] + 1},
                      'message': {'kind': 'answer', 'payload': {'request': b['input']['id'], 'text': text}},
                      'scope': 'turn'}))
"""

# Host-owned fixture validator checks the only permitted repair byte-for-byte.
# Controller validation separately executes and journals candidate history replay.
VALIDATOR = """
import hashlib, json, pathlib, sys
r = json.load(sys.stdin)
initial = pathlib.Path(sys.argv[1]).read_text()
expected = initial.replace('Message("question", {"request": event["id"], "text": "recovered"})',
                           'Message("answer", {"request": event["id"], "text": "recovered"})')
actual = pathlib.Path(r['candidate']['path']).read_bytes()
ok = actual == expected.encode() and hashlib.sha256(actual).hexdigest() == r['candidate']['sha256']
print(json.dumps({'ok': ok, 'checks': ['exact independently specified fixture repair', 'content hash']}))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--unit-directory",
        type=Path,
        help="Explicit unit templates to check before packaging; recorded separately from the sealed runtime",
    )
    args = parser.parse_args()
    release, state = args.release.absolute(), args.state.absolute()
    unit_directory = (
        args.unit_directory.resolve()
        if args.unit_directory
        else release / "contracts/deploy/systemd"
    )
    manifest = verify_release(release).manifest
    identities = static_identities()
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    name = "cq-" + uuid.uuid4().hex[:12]
    install = Path("/opt") / name
    units = {kind: name + "-" + kind for kind in ("room", "agent")}
    ports = {}
    for kind in units:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports[kind] = listener.getsockname()[1]
    assert ports["room"] != ports["agent"]
    databases = {
        kind: "/var/lib/" + unit + "/state.sqlite3" for kind, unit in units.items()
    }
    python = install / "venv/bin/python"
    contract = install / "release/contracts/v2/hybrid-assistant.band"
    initial = install / "initial-program.py"
    probes = PROBES.replace("__AGENT_DB__", databases["agent"]).replace(
        "__ROOM_DB__", databases["room"]
    )
    probes = probes.replace("__HOST_PID__", "/proc/" + str(os.getpid()) + "/status")
    (state / "initial-program.py").write_text(
        (release / "examples/partial_program.py").read_text() + probes
    )
    (state / "provider.py").write_text(PROVIDER)
    (state / "validator.py").write_text(VALIDATOR)
    room_token, agent_token, observer_token = (
        uuid.uuid4().hex + uuid.uuid4().hex for _ in range(3)
    )
    private(
        state / "room-tokens.json",
        {
            room_token: {"actor": "user", "permissions": ["room", "observe"]},
            agent_token: {"actor": "agent", "permissions": ["room"]},
        },
    )
    private(
        state / "agent-tokens.json",
        {observer_token: {"actor": "operator", "permissions": ["observe"]}},
    )
    private(state / "room.token", agent_token)
    configs = {}
    for kind, unit in units.items():
        credentials = "/var/lib/" + unit + "/"
        config = {
            "profile": "band-contract/service/1",
            "kind": kind,
            "engine": {
                "command": [str(install / "release/engine/bin/contract-engine")],
                "contract": str(contract),
                "format": "frontmatter",
            },
            "store": {"path": databases[kind]},
            "poll_interval_ms": 100,
            "http": {
                "host": "127.0.0.1",
                "port": ports[kind],
                "tokens_file": credentials + "tokens",
            },
        }
        if kind == "room":
            config["room"] = {
                "session": "systemd-qualification",
                "bindings": {"requester": "user", "assistant": "agent"},
            }
        else:
            config["agent"] = {
                "actor": "agent",
                "program_source": str(initial),
                "artifact_directory": "/var/lib/" + unit + "/artifacts",
                "room": {
                    "url": "http://127.0.0.1:" + str(ports["room"]),
                    "token_file": credentials + "room-token",
                },
                "provider": {"command": [str(python), str(install / "provider.py")]},
                "validator": {
                    "command": [
                        str(python),
                        str(install / "validator.py"),
                        str(initial),
                    ]
                },
                "max_repairs": 2,
            }
        configs[kind] = private(state / (kind + ".json"), config)
    result: dict[str, Any] = {
        "release_commit": manifest["source_commit"],
        "manifest_sha256": hashlib.sha256(
            (release / "manifest.json").read_bytes()
        ).hexdigest(),
        "scope": "Actual system manager; shipped static users and unit protections, synthetic provider; no public deployment",
        "unit_template_hashes": {
            kind: hashlib.sha256(
                (unit_directory / ("contract-" + kind + ".service")).read_bytes()
            ).hexdigest()
            for kind in units
        },
        "unit_template_override": args.unit_directory is not None,
        "host": {
            "kernel": os.uname().release,
            "systemd": run(["systemctl", "--version"]).stdout.splitlines()[0],
        },
    }
    owned, started = False, []

    def properties(kind: str) -> dict[str, str]:
        parser = UnitParser(interpolation=None, strict=False)
        parser.read(unit_directory / ("contract-" + kind + ".service"))
        ignored = {"StateDirectory", "WorkingDirectory", "ExecStart", "EnvironmentFile"}
        values = {k: v for k, v in parser["Service"].items() if k not in ignored}
        values.update(
            {
                "StateDirectory": units[kind],
                "WorkingDirectory": "/var/lib/" + units[kind],
                "Environment": "QUALIFICATION_PROVIDER_CANARY=synthetic-only",
            }
        )
        for key in ("StartLimitIntervalSec", "StartLimitBurst"):
            values[key] = parser["Unit"][key]
        return values

    def show(kind: str, *fields: str) -> dict[str, str]:
        command = ["systemctl", "show", units[kind] + ".service"]
        command.extend("--property=" + field for field in fields)
        return dict(line.split("=", 1) for line in run(command).stdout.splitlines())

    def start(kind: str) -> None:
        if kind in started and show(kind, "LoadState").get("LoadState") != "not-found":
            run(["systemctl", "start", units[kind] + ".service"], root=True)
        else:
            command = ["systemd-run", "--quiet", "--unit=" + units[kind]]
            for key, value in properties(kind).items():
                command.extend(["--property", key + "=" + value])
            for credential, path in [
                ("config", configs[kind]),
                ("tokens", state / (kind + "-tokens.json")),
            ]:
                command.extend(
                    ["--property", "LoadCredential=" + credential + ":" + str(path)]
                )
            if kind == "agent":
                command.extend(
                    [
                        "--property",
                        "LoadCredential=room-token:" + str(state / "room.token"),
                    ]
                )
            # systemd credentials are root:service 0440. The service's documented
            # configuration contract is private 0600, so provision that contract
            # under its own UID before running the unchanged installed entrypoint.
            bootstrap = (
                "import os,pathlib,sys; from remote_agent.cli import main; "
                "root=pathlib.Path.cwd(); source=pathlib.Path(os.environ['CREDENTIALS_DIRECTORY']); "
                "[(p.write_bytes((source/p.name).read_bytes()),p.chmod(0o600)) "
                "for p in (root/x.name for x in source.iterdir())]; "
                "sys.argv=['contract-service','serve',str(root/'config')]; sys.exit(main())"
            )
            command.extend([str(python), "-c", bootstrap])
            run(command, root=True)
            if kind not in started:
                started.append(kind)

        def ready() -> bool:
            try:
                return (
                    request(
                        ports[kind],
                        "/health/ready",
                        room_token if kind == "room" else observer_token,
                    )[0]
                    == 200
                )
            except (OSError, http.client.HTTPException):
                return False

        wait_for(ready, kind + " ready")

    def stop(kind: str) -> None:
        run(["systemctl", "stop", units[kind] + ".service"], root=True, timeout=20)
        status = show(kind, "ExecMainStatus", "Result", "ActiveState")
        assert status == {
            "Result": "success",
            "ExecMainStatus": "0",
            "ActiveState": "inactive",
        }, status

    def state_values() -> dict[str, Any]:
        code = (
            "import json,sqlite3,sys; d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); "
            "wanted={'turns','state','repair','model_calls','repair_calls','mode','quarantined','program'}; "
            "print(json.dumps({k:json.loads(v) for k,v in d.execute('SELECT key,value FROM current_state') if k in wanted}))"
        )
        return json.loads(
            run(["/usr/bin/python3", "-c", code, databases["agent"]], root=True).stdout
        )

    def publish(identifier: str, text: str, version: int) -> dict[str, Any]:
        status, value = request(
            ports["room"],
            "/v1/proposals",
            room_token,
            {
                "id": identifier,
                "session": "systemd-qualification",
                "kind": "question",
                "payload": {"text": text},
                "expected_version": version,
            },
        )
        assert status == 200 and value["status"] == "accepted", value
        return value

    def turns(expected: int) -> None:
        def reached() -> bool:
            status, value = request(ports["agent"], "/metrics", observer_token)
            assert status == 200
            return value["backend"]["turns"] == expected

        wait_for(reached, str(expected) + " committed turns")

    try:
        assert not install.exists()
        run(["install", "-d", "-m", "755", install], root=True)
        owned = True
        run(["cp", "-a", release, install / "release"], root=True)
        for fixture in ("initial-program.py", "provider.py", "validator.py"):
            run(["install", "-m", "644", state / fixture, install / fixture], root=True)
        run(["chown", "-R", "root:root", install], root=True)
        run(["/usr/bin/python3.12", "-m", "venv", install / "venv"], root=True)
        for command in installation_commands(
            verify_release(install / "release"), python
        ):
            run(command, root=True)
        start("room")
        start("agent")
        result["units"] = {}
        for kind in units:
            status = show(kind, "MainPID", "ControlGroup", *required_protections(kind))
            assert_effective_protections(kind, status)
            pid = status.pop("MainPID")
            proc = dict(
                line.split(":", 1)
                for line in run(
                    ["cat", "/proc/" + pid + "/status"], root=True
                ).stdout.splitlines()
            )
            uid = int(proc["Uid"].split()[0])
            gid = int(proc["Gid"].split()[0])
            assert (uid, gid) == identities[kind], (kind, uid, gid)
            assert (
                uid != 0
                and proc["NoNewPrivs"].strip() == "1"
                and int(proc["CapEff"], 16) == 0
            )
            control = Path("/sys/fs/cgroup" + status["ControlGroup"])
            memory_max, tasks_max = (
                (control / "memory.max").read_text().strip(),
                (control / "pids.max").read_text().strip(),
            )
            assert int(memory_max) == (512 if kind == "room" else 1024) * 1024 * 1024
            assert tasks_max == "128"
            result["units"][kind] = {
                "uid": uid,
                "effective_capabilities": 0,
                "kernel_no_new_privileges": True,
                "kernel_memory_max": int(memory_max),
                "kernel_tasks_max": int(tasks_max),
                "settings": status,
            }
        assert result["units"]["room"]["uid"] != result["units"]["agent"]["uid"]
        first = publish("q1", "ping", 0)
        turns(1)
        assert state_values()["model_calls"] == 0
        result["sandbox"] = {
            "installed_program_completed": True,
            "provider_environment_hidden": True,
            "both_databases_hidden": True,
            "host_pid_hidden": True,
            "network_spawn_write_denied": True,
            "memory_allocation_denied": True,
        }
        publish("q2", "needs free-form interpretation", 2)
        turns(2)
        assert state_values()["model_calls"] == 1
        publish("q3", "recover", 4)
        turns(3)
        marker = "/var/lib/" + units["agent"] + "/repair-dispatched"
        wait_for(
            lambda: run(["test", "-f", marker], root=True, check=False).returncode == 0,
            "repair dispatch",
        )
        stop("agent")
        pending = state_values()
        assert pending["repair"]["status"] == "running" and pending["model_calls"] == 3
        assert pending["repair_calls"] == 1
        publish("q4", "offline free-form question", 6)
        start("agent")
        turns(4)
        wait_for(
            lambda: state_values()["repair"]["status"] == "installed",
            "recovered repair install",
        )
        repaired = state_values()
        assert repaired["model_calls"] == 4 and repaired["repair_calls"] == 1
        assert repaired["mode"] == "program" and repaired["quarantined"] is False
        publish("q5", "recover", 8)
        turns(5)
        completed = state_values()
        assert completed["model_calls"] == 4 and completed["state"] == {"count": 5}
        calls = [
            json.loads(line)["operation"]
            for line in run(
                ["cat", "/var/lib/" + units["agent"] + "/provider-calls.jsonl"],
                root=True,
            ).stdout.splitlines()
        ]
        assert calls.count("repair") == 1 and calls.count("respond") == 3
        result["repair_and_restart"] = {
            "stopped_during_generation": True,
            "captured_result_reused": True,
            "repair_calls": 1,
            "model_calls": 4,
            "offline_inbox_completed": True,
            "validated_program_installed": True,
            "repaired_case_handled_without_model": True,
            "turns": 5,
        }
        stop("agent")
        stop("room")
        start("room")
        status, retry = request(
            ports["room"],
            "/v1/proposals",
            room_token,
            {
                "id": "q1",
                "session": "systemd-qualification",
                "kind": "question",
                "payload": {"text": "ping"},
                "expected_version": 0,
            },
        )
        assert status == 200 and retry == {"status": "duplicate", "original": first}
        stop("room")
        result["room_restart_original_receipt"] = True
        result["graceful_stop_exit_status"] = 0
    finally:
        for kind in reversed(started):
            run(
                ["systemctl", "stop", units[kind] + ".service"],
                root=True,
                check=False,
                timeout=25,
            )
            log = run(
                [
                    "journalctl",
                    "--no-pager",
                    "-n",
                    "200",
                    "--unit=" + units[kind] + ".service",
                ],
                root=True,
                check=False,
            )
            private(state / (kind + ".log"), log.stdout)
            source = Path("/var/lib") / units[kind]
            if run(["test", "-d", source], root=True, check=False).returncode == 0:
                run(["cp", "-a", source, state / (kind + "-state")], root=True)
                run(
                    [
                        "chown",
                        "-R",
                        str(os.getuid()) + ":" + str(os.getgid()),
                        state / (kind + "-state"),
                    ],
                    root=True,
                )
            run(
                ["systemctl", "reset-failed", units[kind] + ".service"],
                root=True,
                check=False,
            )
        if owned:
            run(["rm", "-rf", install], root=True)
            for unit in units.values():
                run(
                    [
                        "rm",
                        "-rf",
                        Path("/var/lib/private") / unit,
                        Path("/var/lib") / unit,
                    ],
                    root=True,
                )
    result["owned_host_paths_removed"] = not install.exists() and all(
        not Path("/var/lib", unit).exists() for unit in units.values()
    )
    result["cgroups_removed"] = all(
        not Path(
            "/sys/fs/cgroup", data["settings"]["ControlGroup"].lstrip("/")
        ).exists()
        for data in result["units"].values()
    )
    assert result["owned_host_paths_removed"] and result["cgroups_removed"]
    private(state / "result.json", result)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
