"""Strict local configuration and supervised room/agent service lifecycle."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path

from remote_agent.authority import DurableRoom
from remote_agent.capabilities import require_linux, require_runtime
from remote_agent.controller import HybridAgent
from remote_agent.network import AgentAPI, HTTPRoom, HTTPService, RoomAPI, validate_url
from remote_agent.openaiprovider import OpenAIProvider
from remote_agent.program import Artifact, Program
from remote_agent.protocol import decode
from remote_agent.providers import CommandProvider, CommandValidator
from remote_agent.storage import digest
from remote_agent.store import Store
from remote_agent.transport import (
    EngineProcess,
    InboxCursorConflict,
    LocalGate,
    TransportError,
)
from remote_agent.types import JsonObject, SourcePath

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    pass


def read_file(
    path: SourcePath, *, private: bool = True, maximum: int = 65_536
) -> bytes:
    require_linux()
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        forbidden = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in (0, os.getuid())
            or before.st_nlink != 1
            or before.st_mode & forbidden
            or before.st_size > maximum
        ):
            raise ConfigurationError(
                "Configuration requires a bounded, privately owned regular file"
            )
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            encoded = source.read(maximum + 1)
            after = os.fstat(source.fileno())
        if len(encoded) > maximum or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ConfigurationError("Configuration changed while being read")
        return encoded
    except OSError as error:
        raise ConfigurationError(
            "Cannot read configuration or credential file"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


# A host capability name such as contract.replies/1.
CAPABILITY = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*/[1-9][0-9]{0,3}")


def _object(
    value: JsonObject, required: Iterable[str], defaults: JsonObject | None = None
) -> JsonObject:
    defaults = defaults or {}
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - (set(required) | defaults.keys())
    ):
        raise ConfigurationError("Missing or unknown configuration field")
    if any(item is None for item in value.values()):
        raise ConfigurationError(
            "Omit optional configuration fields instead of setting null"
        )
    return defaults | value


def _integer(value: object, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ConfigurationError("Configuration integer is outside its allowed range")


def _path(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or not Path(value).is_absolute()
    ):
        raise ConfigurationError("Configuration paths must be absolute")


def _command(value: list[str]) -> None:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 64
        or any(not isinstance(v, str) or not v or "\x00" in v for v in value)
    ):
        raise ConfigurationError("Command configuration must be a fixed argv")
    _path(value[0])


def _callback(value: JsonObject) -> JsonObject:
    result = _object(value, {"command"}, {"timeout_ms": 30_000})
    _command(result["command"])
    _integer(result["timeout_ms"], 1, 120_000)
    return result


def _provider(value: JsonObject) -> JsonObject:
    if isinstance(value, dict) and value.get("kind") == "openai":
        result = _object(
            value,
            {"kind", "model"},
            {
                "timeout_ms": 30_000,
                "max_output_tokens": 2048,
                "max_request_bytes": 65_536,
                "pricing": None,
            },
        )
        OpenAIProvider.validate_options(
            **{k: v for k, v in result.items() if k != "kind"}
        )
        return result
    return _callback(value)


def load_config(path: SourcePath) -> JsonObject:
    try:
        config = _object(
            decode(read_file(path)),
            {"profile", "kind", "store", "engine", "http"},
            {"poll_interval_ms": 1000, "room": None, "agent": None},
        )
        if config["profile"] != "band-contract/service/1" or config["kind"] not in (
            "room",
            "agent",
        ):
            raise ConfigurationError("Unsupported service profile or kind")
        _integer(config["poll_interval_ms"], 100, 60_000)
        store = config["store"] = _object(
            config["store"],
            {"path"},
            {
                "max_records": 100_000,
                "max_record_bytes": 1_048_576,
                "max_database_bytes": 67_108_864,
                "durable": True,
            },
        )
        _path(store["path"])
        if type(store["durable"]) is not bool or (
            not store["durable"] and config["kind"] != "agent"
        ):
            raise ConfigurationError(
                "Ephemeral storage is available only for a remote agent, never the room authority"
            )
        _integer(store["max_records"], 100, 10_000_000)
        _integer(store["max_record_bytes"], 4096, 4_194_304)
        _integer(store["max_database_bytes"], 1_048_576, 1_073_741_824)
        engine = config["engine"] = _object(
            config["engine"],
            {"command", "contract"},
            {"format": "frontmatter", "timeout_ms": 10_000, "max_bytes": 2_097_152},
        )
        _path(engine["contract"])
        _command(engine["command"])
        if engine["format"] not in ("frontmatter", "standard", "file-source"):
            raise ConfigurationError("Unsupported contract source format")
        _integer(engine["timeout_ms"], 1, 120_000)
        _integer(engine["max_bytes"], 65_536, 16_777_216)
        http = config["http"] = _object(
            config["http"],
            {"host", "port", "tokens_file"},
            {
                "tls_certificate": None,
                "tls_key": None,
                "max_body_bytes": 65_536,
                "max_connections": 8,
                "read_timeout_ms": 5000,
            },
        )
        _path(http["tokens_file"])
        _integer(http["port"], 0, 65_535)
        _integer(http["max_body_bytes"], 1024, 1_048_576)
        _integer(http["max_connections"], 1, 128)
        _integer(http["read_timeout_ms"], 1, 120_000)
        if (
            not isinstance(http["host"], str)
            or not http["host"]
            or (http["host"] != "127.0.0.1" and not http["tls_certificate"])
            or bool(http["tls_certificate"]) != bool(http["tls_key"])
        ):
            raise ConfigurationError(
                "Public HTTP binds require a complete TLS configuration"
            )
        for name in ("tls_certificate", "tls_key"):
            if http[name] is not None:
                _path(http[name])
        if config["kind"] == "room":
            if config["agent"] is not None:
                raise ConfigurationError(
                    "Room services cannot contain agent configuration"
                )
            room = config["room"] = _object(
                config["room"],
                {"session", "bindings"},
                {
                    "inactivity": "accepted",
                    "max_messages": 100,
                    "max_bytes": 16_384,
                    "snapshot_every": 20,
                    "capabilities": None,
                },
            )
            if (
                not isinstance(room["session"], str)
                or not 1 <= len(room["session"]) <= 160
                or not isinstance(room["bindings"], dict)
                or not 1 <= len(room["bindings"]) <= 32
                or any(
                    not isinstance(v, str) or not 1 <= len(v) <= 160
                    for v in room["bindings"].values()
                )
                or room["inactivity"] not in ("accepted", "transition", "marked")
            ):
                raise ConfigurationError("Invalid room identity or inactivity policy")
            _integer(room["max_messages"], 1, 100_000)
            _integer(room["max_bytes"], 1024, 1_048_576)
            _integer(room["snapshot_every"], 1, 1000)
            capabilities = room["capabilities"]
            if capabilities is not None and (
                not isinstance(capabilities, list)
                or len(capabilities) > 16
                or len(set(map(str, capabilities))) != len(capabilities)
                or any(
                    not isinstance(c, str) or not CAPABILITY.fullmatch(c)
                    for c in capabilities
                )
            ):
                raise ConfigurationError(
                    "Room capabilities must be a list of distinct capability names"
                )
        else:
            if config["room"] is not None:
                raise ConfigurationError(
                    "Agent services cannot contain room configuration"
                )
            agent = config["agent"] = _object(
                config["agent"],
                {"actor", "program_source", "artifact_directory", "room", "provider"},
                {
                    "validator": None,
                    "control_actors": {},
                    "program_timeout_ms": 2000,
                    "model_timeout_ms": 30_000,
                    "snapshot_every": 10,
                    "max_pending": 16,
                    "max_model_calls": 100,
                    "max_repairs": 0,
                    "max_attempts": 3,
                    "initial_route": None,
                },
            )
            if agent["initial_route"] not in (None, "program", "llm"):
                raise ConfigurationError("Invalid initial execution route")
            for name in ("program_source", "artifact_directory"):
                _path(agent[name])
            if (
                not isinstance(agent["actor"], str)
                or not 1 <= len(agent["actor"]) <= 160
            ):
                raise ConfigurationError("Invalid agent identity")
            remote = agent["room"] = _object(
                agent["room"],
                {"url", "token_file"},
                {"ca_file": None, "timeout_ms": 10_000, "authentication": "bearer"},
            )
            if remote["authentication"] not in ("bearer", "band-api-key"):
                raise ValueError("Unsupported room authentication scheme")
            validate_url(remote["url"])
            _path(remote["token_file"])
            if remote["ca_file"] is not None:
                _path(remote["ca_file"])
            _integer(remote["timeout_ms"], 1, 120_000)
            for name, low, high in (
                ("program_timeout_ms", 1, 30_000),
                ("model_timeout_ms", 1, 120_000),
                ("snapshot_every", 1, 1000),
                ("max_pending", 1, 256),
                ("max_model_calls", 0, 100_000),
                ("max_repairs", 0, 1000),
                ("max_attempts", 1, 10),
            ):
                _integer(agent[name], low, high)
            agent["provider"] = _provider(agent["provider"])
            if agent["validator"] is not None:
                agent["validator"] = _callback(agent["validator"])
            if agent["max_repairs"] and agent["validator"] is None:
                raise ConfigurationError(
                    "Repair requires an explicit host quality validator"
                )
            controls = agent["control_actors"]
            if (
                not isinstance(controls, dict)
                or len(controls) > 256
                or any(
                    not isinstance(actions, list)
                    or any(
                        action
                        not in (
                            "wake",
                            "takeover",
                            "resume",
                            "retry",
                            "repair",
                            "reconcile",
                        )
                        for action in actions
                    )
                    for actions in controls.values()
                )
            ):
                raise ConfigurationError("Invalid control action grants")
        return config
    except ConfigurationError:
        raise
    except Exception as error:
        raise ConfigurationError("Invalid configuration encoding or value") from error


class Service:
    def __init__(
        self, config: JsonObject, *, stopping: threading.Event | None = None
    ) -> None:
        require_runtime()
        self.config, self.stopping = config, stopping or threading.Event()
        self.poll_wake = threading.Event()
        self.resources = ExitStack()
        self.agent: HybridAgent | None = None
        self.room: DurableRoom | HTTPRoom
        self.backend: RoomAPI | AgentAPI
        self.held = False
        self.poll_failures = 0
        self.last_fault: str | None = None
        self._pending_inbox_hold: JsonObject | None = None
        try:
            http = dict(config["http"])
            tokens = decode(read_file(http.pop("tokens_file")))
            if http["tls_key"]:
                read_file(http["tls_key"], maximum=65_536)
            http["read_timeout"] = http.pop("read_timeout_ms") / 1000
            engine = dict(config["engine"])
            engine["timeout"] = engine.pop("timeout_ms") / 1000
            self.engine = EngineProcess(**engine)
            self.resources.callback(self.engine.close)
            self.store = self.resources.enter_context(Store(**config["store"]))
            if config["kind"] == "room":
                self.room = DurableRoom(self.store, self.engine, **config["room"])
                self.backend = RoomAPI(self.room)
            else:
                options = dict(config["agent"])
                remote = dict(options.pop("room"))
                token = (
                    read_file(remote.pop("token_file"), maximum=2048)
                    .decode("ascii")
                    .strip()
                )
                remote["timeout"] = remote.pop("timeout_ms") / 1000
                self.room = HTTPRoom(token=token, require_inbox=True, **remote)
                source = read_file(
                    options.pop("program_source"), private=False, maximum=262_144
                ).decode("utf-8")
                artifact = Artifact.publish(source, options["artifact_directory"])
                program = Program(
                    artifact, timeout=options.pop("program_timeout_ms") / 1000
                )
                provider = options.pop("provider")
                validator = options.pop("validator")
                pin = {
                    "profile": "band-contract/service-agent/1",
                    "provider": digest(provider),
                    "validator": digest(validator),
                    "storage_limits": {
                        k: v
                        for k, v in config["store"].items()
                        if k != "path" and (k != "durable" or v is False)
                    },
                }
                prior = self.store.get("service_config")
                if prior is not None and prior != pin:
                    raise ConfigurationError(
                        "Pinned service behavior changed; migration is required"
                    )
                if provider.get("kind") == "openai":
                    native = {
                        k: v
                        for k, v in provider.items()
                        if k not in ("kind", "timeout_ms")
                    }
                    provider = OpenAIProvider(
                        options["artifact_directory"],
                        timeout_ms=provider["timeout_ms"],
                        **native,
                    )
                else:
                    provider = CommandProvider(
                        provider["command"],
                        options["artifact_directory"],
                        timeout=provider["timeout_ms"] / 1000,
                    )
                validator = (
                    CommandValidator(
                        validator["command"], timeout=validator["timeout_ms"] / 1000
                    )
                    if validator is not None
                    else None
                )
                options["model_timeout"] = options.pop("model_timeout_ms") / 1000
                self.agent = HybridAgent(
                    self.store,
                    program,
                    self.room,
                    LocalGate(self.engine),
                    provider,
                    validator=validator,
                    **options,
                )
                self.resources.callback(self.agent.close)
                if prior is None:
                    self.store.commit(
                        "service_config",
                        pin,
                        updates={"service_config": pin, "inbox_cursor": 0},
                    )
                self.backend = AgentAPI(self.agent, notify=self.notify)
            self.http = HTTPService(self, tokens, **http)
            self.resources.callback(self.http.close)
        except BaseException:
            self.resources.close()
            raise

    @property
    def controller(self) -> HybridAgent:
        if self.agent is None:
            raise ConfigurationError("This operation requires an agent service")
        return self.agent

    def ready(self) -> bool:
        return (
            not self.stopping.is_set()
            and self.last_fault is None
            and self.backend.ready()
            and not self._inbox_hold()
            and not self.engine.closed
            and self.engine.process.poll() is None
        )

    def _inbox_hold(self) -> JsonObject | None:
        if self.agent is None:
            return None
        return self.store.get("inbox_hold") or getattr(
            self, "_pending_inbox_hold", None
        )

    def metrics(self) -> JsonObject:
        return dict(
            self.backend.metrics(),
            ready=self.ready(),
            inbox_held=self.held or bool(self._inbox_hold()),
            inbox_reconciliation_required=bool(self._inbox_hold()),
            poll_failures=self.poll_failures,
        )

    def dispatch(
        self, method: str, path: str, grant: JsonObject, data: JsonObject | None
    ) -> tuple[int, JsonObject]:
        if self.agent is not None and path == "/v1/controls":
            with self.agent.lock:
                self._persist_inbox_hold()
                result = self.backend.dispatch(method, path, grant, data)
                if not self._inbox_hold():
                    self.held = False  # Only explicit reconciliation clears a durable inbox hold.
                return result
        result = self.backend.dispatch(method, path, grant, data)
        return result

    def wake(self) -> None:
        """Coalesce polling hints; the service loop remains the sole consumer."""
        self.poll_wake.set()

    def notify(self, event: JsonObject) -> JsonObject:
        result = self.controller.notify(event)
        if result["status"] == "notified":
            self.wake()
        return result

    def cycle(self) -> None:
        if self.engine.closed or self.engine.process.poll() is not None:
            raise TransportError("Engine requires service recovery")
        if isinstance(self.room, DurableRoom):
            self.room.tick()
            return
        self._agent_cycle()

    def _persist_inbox_hold(self) -> None:
        pending = getattr(self, "_pending_inbox_hold", None)
        if pending is not None:
            self.store.commit("inbox_held", pending, updates={"inbox_hold": pending})
            self._pending_inbox_hold = None

    def _agent_cycle(self) -> None:
        agent = self.controller
        # Bound each cycle, but do not sleep between pages of an existing backlog.
        for _ in range(16):
            # Serialize each poll, its turns and cursor commit with controls;
            # release between pages so takeover/retry need not wait for all 16.
            # A control reset is then observed before the next page is fetched.
            with agent.lock:
                if self.stopping.is_set() or not self._agent_page():
                    break

    def _agent_page(self) -> bool | None:
        agent = self.controller
        room = self.room
        if not isinstance(room, HTTPRoom):
            raise ConfigurationError("Agent polling requires a remote room")
        self._persist_inbox_hold()
        if self._inbox_hold():
            self.held = True
            return
        cursor = self.store.get("inbox_cursor", 0)
        try:
            page = room.inbox(cursor, limit=128)
        except InboxCursorConflict:
            self.held = True
            self._pending_inbox_hold = {
                "session": room.session,
                "actor": agent.actor,
                "cursor": cursor,
                "reason": "cursor_ahead",
            }
            # Latch in memory before persistence: a failed commit must not allow
            # a later successful page to bypass the observed restore conflict.
            self._persist_inbox_hold()
            logger.error(
                "%s", json.dumps({"event": "inbox_held", "reason": "cursor_ahead"})
            )
            return
        if self.held and not self.store.get("queue"):
            self.held = False
        staged = self.store.get("staged")
        if not self.held or (staged and staged["sends"] < agent.limits["max_attempts"]):
            self.held = agent.recover()["status"] == "held"
        if self.held:
            return
        for event in page["events"]:
            if self.stopping.is_set():
                return
            if agent.receive(event)["status"] == "held":
                self.held = True
                return
        if page["cursor"] != cursor:
            self.store.commit(
                "inbox_cursor",
                {"cursor": page["cursor"]},
                updates={"inbox_cursor": page["cursor"]},
            )
        return (
            cursor
            < page["cursor"]
            < page.get("head", {}).get("sequence", page["cursor"])
        )

    def run(self) -> None:
        while not self.stopping.is_set():
            # Clear before the cycle: a hint arriving during it must survive
            # until the wait, rather than being erased after polling.
            self.poll_wake.clear()
            try:
                self.cycle()
                if self.last_fault is not None:
                    logger.info("%s", json.dumps({"event": "poll_recovered"}))
                self.last_fault = None
            except Exception as error:
                self.poll_failures += 1
                category = type(error).__name__
                if self.last_fault != category:
                    logger.error(
                        "%s", json.dumps({"event": "poll_failed", "category": category})
                    )
                self.last_fault = category
                if (
                    self.agent is None
                    or self.engine.closed
                    or self.engine.process.poll() is not None
                ):
                    raise
            if not self.stopping.is_set():
                self.poll_wake.wait(self.config["poll_interval_ms"] / 1000)

    def request_stop(self) -> None:
        self.stopping.set()
        self.wake()
        if self.agent is not None:
            self.agent.request_stop()

    def close(self) -> None:
        self.request_stop()
        # Preserve the open store if a bounded worker did not finish. Its final
        # result must never race a closed connection; the supervisor owns exit.
        self.http.close()
        if self.agent is not None:
            self.agent.close()
        self.resources.close()
