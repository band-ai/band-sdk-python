"""Immutable room-scoped MCP descriptions and secret-safe native Codex launches.

These are live runtime objects, not adapter configuration. Pydantic serialization
excludes secret fields; stdlib dataclass serialization refuses their frozen maps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from types import MappingProxyType
from typing import Annotated, Any, NoReturn, SupportsIndex, TypeAlias
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field

logger = logging.getLogger(__name__)

__all__ = [
    "CodexMcpHttpServer",
    "CodexMcpLaunch",
    "CodexMcpRedactor",
    "CodexMcpResolver",
    "CodexMcpServer",
    "CodexMcpSnapshot",
    "CodexMcpStartupError",
    "CodexMcpStdioServer",
    "freeze_mcp_servers",
]

_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]+")
# Codex manages this server separately from caller-configured transports.
# Native source: codex-rs/codex-mcp/src/mcp/mod.rs, CODEX_APPS_MCP_SERVER_NAME.
_RESERVED_SERVER_NAMES = frozenset({"codex_apps"})
# Preserve native stdio filtering even after alias remapping. This is the union
# in protocol/src/shell_environment.rs for Codex 0.160.0 and 0.162.1.
_NON_INHERITABLE_STDIO_ENV = frozenset(
    {
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "CODEX_GUARDIAN_DECISIONS_API_KEY",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    }
)
_REDACTED = "[REDACTED]"
_STARTUP_STAGES = frozenset(
    {
        "startup",
        "retirement",
        "resolution",
        "snapshot",
        "launch",
        "connect",
        "initialize",
        "skills",
        "model",
        "readiness",
        "thread/start",
        "thread/resume",
    }
)
_VALIDATION_MESSAGES = {
    "unsupported-transport": "unsupported transport (only stdio and streamable HTTP are supported)",
    "unsupported-platform": "secret-bearing stdio MCP servers require POSIX exec",
    "invalid-name": "invalid server name",
    "duplicate-name": "duplicate server name",
    "reserved-name": "reserved server name",
    "invalid-descriptor": "invalid server descriptor",
}
_TOML_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
# Native env_vars forwards same-named values, never aliases (rmcp-client/src/utils.rs).
# POSIX exec preserves native ownership while removing consumed secret aliases.
_STDIO_ENV_EXEC = (
    "import json,os,sys; "
    "aliases=json.loads(sys.argv[1]); env=os.environ.copy(); "
    "values={name:env.pop(source) for name,source in aliases.items()}; "
    "env.update(values); argv=sys.argv[2:]; os.execvpe(argv[0],argv,env)"
)


class CodexMcpStartupError(RuntimeError):
    """A startup stage or owned validation category, never callback diagnostics."""

    def __init__(self, stage: str) -> None:
        known = type(stage) is str and (
            stage in _STARTUP_STAGES or stage in _VALIDATION_MESSAGES
        )
        self.stage = stage if known else "startup"
        category = _VALIDATION_MESSAGES.get(self.stage)
        message = (
            f"Codex MCP startup failed: {category}."
            if category is not None
            else f"Codex MCP startup failed during {self.stage}."
        )
        super().__init__(message)


def _mapping_entries(
    mapping: Mapping[Any, Any], stage: str
) -> tuple[tuple[Any, Any], ...]:
    if not isinstance(mapping, Mapping):
        raise CodexMcpStartupError(stage)
    try:
        return tuple((name, value) for name, value in mapping.items())
    except Exception:
        # Mapping iteration and access are caller code, not owned validation.
        safe = CodexMcpStartupError(stage)
        logger.debug("Codex MCP mapping refused", exc_info=(type(safe), safe, None))
        raise safe from None


def _valid_text(value: Any, *, nonempty: bool = False) -> bool:
    return (
        type(value) is str
        and (bool(value) or not nonempty)
        and "\0" not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _public_http_url(value: str) -> bool:
    # URL overrides are public argv, unlike protected bearer/header references.
    # Refuse every query and fragment rather than guessing credential names.
    if "?" in value or "#" in value:
        return False
    try:
        endpoint = urlsplit(value)
        return (
            endpoint.scheme in ("http", "https")
            and bool(endpoint.hostname)
            and endpoint.username is None
            and endpoint.password is None
        )
    except ValueError:
        return False


def _string_mapping(
    mapping: Mapping[str, str], stage: str, *, environment: bool = False
) -> dict[str, str]:
    copied: dict[str, str] = {}
    for name, value in _mapping_entries(mapping, stage):
        if (
            not _valid_text(name, nonempty=True)
            or not _valid_text(value)
            or (environment and "=" in name)
        ):
            raise CodexMcpStartupError(stage)
        copied[name] = value
    return copied


def _argv(
    sequence: Sequence[str], stage: str, *, nonempty: bool = False
) -> tuple[str, ...]:
    if not isinstance(sequence, Sequence) or isinstance(sequence, str | bytes):
        raise CodexMcpStartupError(stage)
    try:
        copied = tuple(sequence)
    except Exception:
        safe = CodexMcpStartupError(stage)
        logger.debug("Codex MCP arguments refused", exc_info=(type(safe), safe, None))
        raise safe from None
    if (nonempty and not copied) or any(not _valid_text(item) for item in copied):
        raise CodexMcpStartupError(stage)
    return copied


@dataclass(frozen=True, slots=True)
class CodexMcpStdioServer:
    """A native stdio server; env is a frozen copy of child-only secret values."""

    command: str
    args: tuple[str, ...] = ()
    env: Annotated[Mapping[str, str], Field(exclude=True)] = field(
        default_factory=dict, repr=False, compare=False
    )
    cwd: str | None = None

    def __post_init__(self) -> None:
        if not _valid_text(self.command, nonempty=True) or (
            self.cwd is not None and not _valid_text(self.cwd)
        ):
            raise CodexMcpStartupError("invalid-descriptor")
        object.__setattr__(self, "args", _argv(self.args, "invalid-descriptor"))
        child_env = _string_mapping(self.env, "invalid-descriptor", environment=True)
        if any(name.upper() in _NON_INHERITABLE_STDIO_ENV for name in child_env):
            raise CodexMcpStartupError("invalid-descriptor")
        object.__setattr__(self, "env", MappingProxyType(child_env))


@dataclass(frozen=True, slots=True)
class CodexMcpHttpServer:
    """A public HTTP endpoint with protected bearer and header credentials."""

    url: str
    bearer_token: Annotated[str | None, Field(exclude=True)] = field(
        default=None, repr=False, compare=False
    )
    headers: Annotated[Mapping[str, str], Field(exclude=True)] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.url, nonempty=True)
            or not _public_http_url(self.url)
            or (self.bearer_token is not None and not _valid_text(self.bearer_token))
        ):
            raise CodexMcpStartupError("invalid-descriptor")
        object.__setattr__(
            self,
            "headers",
            MappingProxyType(_string_mapping(self.headers, "invalid-descriptor")),
        )


CodexMcpServer: TypeAlias = CodexMcpStdioServer | CodexMcpHttpServer
CodexMcpResolver: TypeAlias = Callable[[str], Awaitable[Mapping[str, CodexMcpServer]]]


def _escaped_secrets(secret: str) -> set[str]:
    variants = {secret}
    frontier = {secret}
    # JSON payloads can themselves appear as escaped strings in RPC diagnostics.
    for _ in range(2):
        escaped: set[str] = set()
        for value in frontier:
            for rendered in (
                json.dumps(value, ensure_ascii=True),
                json.dumps(value, ensure_ascii=False),
                repr(value),
                ascii(value),
            ):
                escaped.add(rendered)
                escaped.add(rendered[1:-1])
            escaped.add(value.encode("unicode_escape").decode("ascii"))
            escaped.add(json.dumps(value, ensure_ascii=False)[1:-1].replace("/", "\\/"))
        frontier = escaped - variants
        variants.update(escaped)
    return variants


class CodexMcpRedactor:
    """Exact-value redaction scoped to one snapshot and its owned child launches."""

    __slots__ = ("_secrets", "_variants")

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> NoReturn:
        raise TypeError(
            "Codex MCP redactors are runtime-only and cannot be serialized."
        )

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        self._variants: tuple[str, ...] = ()
        self._register(secrets)

    def _register(self, secrets: Iterable[str]) -> None:
        previous_count = len(self._secrets)
        for secret in secrets:
            if type(secret) is not str:
                raise CodexMcpStartupError("snapshot")
            if secret:
                self._secrets.add(secret)
        if len(self._secrets) == previous_count:
            return
        variants = {
            variant for secret in self._secrets for variant in _escaped_secrets(secret)
        }
        # Secret-bearing regexes would outlive clear() in Python's global regex cache.
        self._variants = tuple(sorted(variants, key=len, reverse=True))

    def text(self, text: str) -> str:
        matches = [
            (start, -len(secret), secret)
            for secret in self._variants
            if (start := text.find(secret)) >= 0
        ]
        if not matches:
            return text
        heapify(matches)
        parts: list[str] = []
        cursor = 0
        while matches:
            start, negative_length, secret = heappop(matches)
            if start >= cursor:
                parts.extend((text[cursor:start], _REDACTED))
                cursor = start - negative_length
            next_start = text.find(secret, cursor)
            if next_start >= 0:
                heappush(matches, (next_start, negative_length, secret))
        parts.append(text[cursor:])
        return "".join(parts)

    def value(self, value: Any) -> Any:
        """Copy nested diagnostic data, including keys, without formatting unknown objects."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                safe_key = self.value(key)
                unique_key = safe_key
                suffix = 2
                while unique_key in redacted:
                    unique_key = f"{safe_key}#{suffix}"
                    suffix += 1
                redacted[unique_key] = self.value(item)
            return redacted
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        if (
            value is None
            or type(value) is bool
            or type(value) is int
            or type(value) is float
        ):
            return value
        return _REDACTED

    def clear(self) -> None:
        """Release tracked values only after the owner has torn down its children."""
        self._secrets.clear()
        self._variants = ()


@dataclass(frozen=True, slots=True)
class CodexMcpLaunch:
    """A private command/env copy for one native child, with no secret serialization."""

    command: tuple[str, ...]
    env: Annotated[Mapping[str, str] | None, Field(exclude=True)] = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "command", _argv(self.command, "launch", nonempty=True)
        )
        if self.env is not None:
            object.__setattr__(
                self,
                "env",
                MappingProxyType(_string_mapping(self.env, "launch", environment=True)),
            )


def _toml_string(value: str) -> str:
    return (
        '"'
        + "".join(
            _TOML_ESCAPES.get(char, f"\\u{ord(char):04x}")
            if ord(char) < 0x20 or ord(char) == 0x7F or char in _TOML_ESCAPES
            else char
            for char in value
        )
        + '"'
    )


def _toml_value(value: Any) -> str:
    match value:
        case str():
            return _toml_string(value)
        case bool():
            return "true" if value else "false"
        case Mapping():
            return (
                "{"
                + ", ".join(
                    f"{_toml_string(key)} = {_toml_value(item)}"
                    for key, item in value.items()
                )
                + "}"
            )
        case tuple() | list():
            return "[" + ", ".join(_toml_value(item) for item in value) + "]"
        case _:
            raise CodexMcpStartupError("launch")


def _server_secret_values(servers: Mapping[str, CodexMcpServer]) -> Iterable[str]:
    for server in servers.values():
        match server:
            case CodexMcpStdioServer():
                yield from server.env.values()
            case CodexMcpHttpServer():
                if server.bearer_token is not None:
                    yield server.bearer_token
                yield from server.headers.values()


@dataclass(frozen=True, slots=True)
class CodexMcpSnapshot:
    """Frozen servers, expected native names, and a pre-registered diagnostic boundary."""

    servers: Mapping[str, CodexMcpServer]
    expected_names: frozenset[str] = field(init=False)
    redactor: CodexMcpRedactor = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        entries = _mapping_entries(self.servers, "snapshot")
        frozen: dict[str, CodexMcpServer] = {}
        for name, server in entries:
            if type(name) is not str or _SERVER_NAME.fullmatch(name) is None:
                raise CodexMcpStartupError("invalid-name")
            if name in _RESERVED_SERVER_NAMES:
                raise CodexMcpStartupError("reserved-name")
            if name in frozen:
                raise CodexMcpStartupError("duplicate-name")
            if (
                type(server) is not CodexMcpStdioServer
                and type(server) is not CodexMcpHttpServer
            ):
                raise CodexMcpStartupError("unsupported-transport")
            match server:
                case CodexMcpStdioServer():
                    if server.env and os.name != "posix":
                        raise CodexMcpStartupError("unsupported-platform")
                    frozen[name] = CodexMcpStdioServer(
                        server.command, server.args, server.env, server.cwd
                    )
                case CodexMcpHttpServer():
                    frozen[name] = CodexMcpHttpServer(
                        server.url, server.bearer_token, server.headers
                    )
        object.__setattr__(self, "servers", MappingProxyType(frozen))
        object.__setattr__(self, "expected_names", frozenset(frozen))
        object.__setattr__(
            self, "redactor", CodexMcpRedactor(_server_secret_values(frozen))
        )

    def launch(
        self, command: Sequence[str], env: Mapping[str, str] | None = None
    ) -> CodexMcpLaunch:
        """Build private overrides with aliases reserved against the full child env."""
        argv = list(_argv(command, "launch", nonempty=True))
        child_env = (
            _string_mapping(env, "launch", environment=True) if env is not None else {}
        )
        self.redactor._register(child_env.values())
        namespace = uuid4().hex.upper()
        used_names = {name.upper() for name in os.environ}
        used_names.update(name.upper() for name in child_env)
        counter = 0

        def secret_ref(value: str) -> str:
            nonlocal counter
            while True:
                name = f"BAND_CODEX_MCP_{namespace}_{counter}"
                counter += 1
                if name not in used_names:
                    break
            used_names.add(name)
            child_env[name] = value
            return name

        for name, server in self.servers.items():
            settings: dict[str, Any] = {
                "enabled": True,
                "required": True,
                "startup_readiness": "connection",
            }
            match server:
                case CodexMcpStdioServer():
                    settings.update(
                        command=server.command, args=server.args, env_vars=[]
                    )
                    if server.cwd is not None:
                        settings["cwd"] = server.cwd
                    if server.env:
                        aliases = {
                            key: secret_ref(value) for key, value in server.env.items()
                        }
                        settings.update(
                            command=sys.executable,
                            args=(
                                "-I",
                                "-c",
                                _STDIO_ENV_EXEC,
                                json.dumps(aliases, ensure_ascii=False),
                                server.command,
                                *server.args,
                            ),
                            env_vars=list(aliases.values()),
                        )
                case CodexMcpHttpServer():
                    settings["url"] = server.url
                    settings["env_http_headers"] = {
                        key: secret_ref(value) for key, value in server.headers.items()
                    }
                    if server.bearer_token is not None:
                        settings["bearer_token_env_var"] = secret_ref(
                            server.bearer_token
                        )
            override = f"mcp_servers.{name}={_toml_value(settings)}"
            try:
                parsed = tomllib.loads(override)
            except (tomllib.TOMLDecodeError, ValueError):
                raise CodexMcpStartupError("launch") from None
            if parsed["mcp_servers"][name] != json.loads(json.dumps(settings)):
                raise CodexMcpStartupError("launch")
            argv.extend(("-c", override))
        return CodexMcpLaunch(
            tuple(argv), child_env if env is not None or child_env else None
        )


def freeze_mcp_servers(servers: Mapping[str, CodexMcpServer]) -> CodexMcpSnapshot:
    """Validate and snapshot one resolver result before constructing a native client."""
    return CodexMcpSnapshot(servers)
