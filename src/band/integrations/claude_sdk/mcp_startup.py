"""Connection-time MCP servers for Claude sessions: one frozen snapshot per new
connection, written to a private file, and positive per-server readiness.

Nothing here renders callback values, native status payloads or native error
text: they can carry credentials. Errors name the stage, a known server name,
a fixed reason and the exception type only, and only errors created here are
trusted to say so -- one raised by a callback, even a ``ClaudeMcpStartupError``,
is replaced.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ForwardRef, TypeAlias

if TYPE_CHECKING:
    from claude_agent_sdk.types import McpServerConfig

    McpServerResolver: TypeAlias = Callable[
        [str], Awaitable[Mapping[str, McpServerConfig]]
    ]
else:
    # A real alias, so annotations such as ``McpServerResolver | None``
    # evaluate at runtime; the native config type is named by reference and
    # resolved from the SDK only when introspected, keeping the SDK optional.
    McpServerResolver = Callable[
        [str],
        Awaitable[
            Mapping[str, ForwardRef("McpServerConfig", module="claude_agent_sdk.types")]
        ],
    ]
"""``resolve_mcp_servers(workspace)``: the complete external MCP servers for a
new Claude connection in ``workspace``, as native ``McpServerConfig`` values."""

MCP_READINESS_TIMEOUT_SECONDS = 30.0
_STATUS_POLL_SECONDS = 0.25
_REMOTE_TYPES = frozenset({"http", "sse"})
_FAILED_STATUSES = frozenset({"failed", "needs-auth", "disabled"})
_KNOWN_STATUSES = _FAILED_STATUSES | {"connected", "pending"}


class ClaudeMcpStartupError(RuntimeError):
    """A Claude session's MCP servers could not be resolved, launched, confirmed
    connected or cleaned up. Carries no callback, native status or error text.

    ``resume_eligible`` is set only when the native CLI failed to start, the one
    case a resumed session may be retried as a fresh one.
    """

    def __init__(
        self,
        stage: str,
        reason: str,
        *,
        server: str | None = None,
        error_type: str | None = None,
        resume_eligible: bool = False,
    ) -> None:
        self.stage = stage
        self.reason = reason
        self.server = server
        self.error_type = error_type
        self.resume_eligible = resume_eligible
        parts = [f"Claude MCP startup failed at {stage}: {reason}"]
        if server is not None:
            parts.append(f"server {server!r}")
        if error_type is not None:
            parts.append(f"({error_type})")
        super().__init__(" ".join(parts))


class _OwnedStartupError(ClaudeMcpStartupError):
    """Built here from owner-known metadata only."""


def startup_error(
    stage: str,
    reason: str,
    error: BaseException | None = None,
    *,
    server: str | None = None,
    resume_eligible: bool = False,
) -> ClaudeMcpStartupError:
    """A fresh safe error for ``error``, whatever its type; raise it ``from
    None`` so the original text, which may hold credentials, never joins the
    chain. The error's type is named only when it is a builtin or comes from a
    library this adapter runs: a callback can name its own classes anything."""
    return _OwnedStartupError(
        stage,
        reason,
        server=server,
        error_type=_known_type_name(error),
        resume_eligible=resume_eligible,
    )


# Literal labels for exactly these classes. An error is matched by the
# identity of its class alone -- never its name, module or other metadata,
# which a callback's own class can set (or compute) to anything.
_LABELLED_ERRORS: tuple[tuple[type[BaseException], str], ...] = (
    (LookupError, "LookupError"),
    (KeyError, "KeyError"),
    (IndexError, "IndexError"),
    (TypeError, "TypeError"),
    (ValueError, "ValueError"),
    (RuntimeError, "RuntimeError"),
    (OSError, "OSError"),
    (PermissionError, "PermissionError"),
    (FileNotFoundError, "FileNotFoundError"),
    (ConnectionError, "ConnectionError"),
    (TimeoutError, "TimeoutError"),
)


def _known_type_name(error: BaseException | None) -> str | None:
    if error is None:
        return None
    cls = type(error)
    for known, label in _LABELLED_ERRORS:
        if cls is known:
            return label
    return None


def is_safe_startup_error(error: BaseException) -> bool:
    """Whether ``error`` was built by the owner, not handed in by a callback."""
    return isinstance(error, _OwnedStartupError)


_NOT_A_MAPPING = object()


def freeze_external_servers(
    resolved: object, protected: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """An owned deep copy of the callback's servers, validated for the native CLI.

    Later changes to the mappings, lists or headers the callback returned can't
    reach the launch. Reading the callback's objects runs their code, so every
    exception raised while copying them -- whatever its type -- is replaced by
    a safe error; only the plain copy is then validated, and in-process ``sdk``
    servers and names Band reserves are refused by name.
    """
    return _validate(_copy_servers(resolved), protected)


def _copy_servers(resolved: object) -> dict[str, object]:
    """Plain JSON values copied out of the callback's result.

    Every operation on the callback's objects -- the type check, iterating its
    items, unpacking each item, reading and serializing each config -- runs
    callback code, so any exception from one of them, whatever its type, is
    replaced by a safe error. Only exact ``str`` names and plain copies leave;
    no callback code runs after this returns.
    """
    pairs: list[tuple[object, object]] | None = None
    try:
        if isinstance(resolved, Mapping):
            pairs = []
            for item in resolved.items():
                name, config = item
                pairs.append((name, config))
    except Exception as error:  # noqa: BLE001 -- untrusted, whatever its type
        raise startup_error(
            "resolution", "the resolved servers could not be read", error
        ) from None
    if pairs is None:
        raise startup_error(
            "resolution", "resolve_mcp_servers must return a mapping of servers"
        )
    copied: dict[str, object] = {}
    for name, config in pairs:
        # Exactly ``str`` (checked without calling into it): a subclass could
        # render or compare itself however it likes.
        if type(name) is not str or not name:
            raise startup_error("resolution", "server names must be strings")
        if name in copied:
            raise startup_error("resolution", "duplicate server name", server=name)
        try:
            if isinstance(config, Mapping):
                plain: object = json.loads(json.dumps(dict(config)))
            else:
                plain = _NOT_A_MAPPING
        except (TypeError, ValueError) as error:
            raise startup_error(
                "resolution", "server config is not serializable", error, server=name
            ) from None
        except Exception as error:  # noqa: BLE001 -- untrusted, whatever its type
            raise startup_error(
                "resolution",
                "the resolved servers could not be read",
                error,
                server=name,
            ) from None
        copied[name] = plain
    return copied


def _validate(
    copied: dict[str, object], protected: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    frozen: dict[str, dict[str, Any]] = {}
    for name, config in copied.items():
        if name in protected:
            raise startup_error(
                "resolution", "the name is reserved for Band", server=name
            )
        if not isinstance(config, dict):
            raise startup_error(
                "resolution", "server config must be a mapping", server=name
            )
        kind = config.get("type", "stdio")
        if not isinstance(kind, str):
            raise startup_error(
                "resolution", "server type must be a string", server=name
            )
        if kind == "sdk":
            raise startup_error(
                "resolution", "in-process sdk servers are not supported", server=name
            )
        if kind not in _REMOTE_TYPES and kind != "stdio":
            raise startup_error("resolution", "unsupported server type", server=name)
        required = "command" if kind == "stdio" else "url"
        if not isinstance(config.get(required), str):
            raise startup_error(
                "resolution", f"server config needs a string {required}", server=name
            )
        frozen[name] = config
    return frozen


@dataclass
class McpConfigFile:
    """The private native ``--mcp-config`` file one connection launches with.

    ``allocate`` creates only its private directory, so the caller owns it
    before anything can fail; ``write`` then fills it. Owned until its child's
    exit is confirmed; then ``release`` removes it. A release that fails raises
    and leaves it owned, for a later retry.
    """

    directory: Path
    path: Path

    @classmethod
    def allocate(cls) -> McpConfigFile:
        directory = Path(tempfile.mkdtemp(prefix="band-claude-mcp-"))
        return cls(directory=directory, path=directory / "mcp.json")

    def write(self, servers: Mapping[str, Any]) -> None:
        os.chmod(self.directory, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"mcpServers": dict(servers)}, handle)

    def release(self) -> None:
        try:
            shutil.rmtree(self.directory)
        except FileNotFoundError:
            return
        except OSError as error:
            raise startup_error(
                "cleanup", "the private MCP file could not be removed", error
            ) from None


def _check_status(
    response: object, expected: frozenset[str]
) -> tuple[bool, str | None]:
    """``(ready, waiting_on)``; raises when the evidence is unusable or a
    server failed. Only a unique ``connected`` entry per expected name counts."""
    servers = response.get("mcpServers") if isinstance(response, Mapping) else None
    if not isinstance(servers, list):
        raise startup_error("status", "malformed status response")
    seen: dict[str, str] = {}
    for entry in servers:
        if not isinstance(entry, Mapping):
            raise startup_error("status", "malformed status entry")
        name, status = entry.get("name"), entry.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            raise startup_error("status", "malformed status entry")
        if name not in expected:
            continue
        if name in seen:
            raise startup_error("status", "duplicate status", server=name)
        if status not in _KNOWN_STATUSES:
            raise startup_error("status", "unknown status", server=name)
        if status in _FAILED_STATUSES:
            raise startup_error("status", f"server is {status}", server=name)
        seen[name] = status
    for name in sorted(expected):
        if seen.get(name) != "connected":
            return False, name
    return True, None


async def await_mcp_ready(
    get_status: Callable[[], Awaitable[object]],
    expected: frozenset[str],
    *,
    timeout: float = MCP_READINESS_TIMEOUT_SECONDS,
) -> None:
    """Wait until every expected server reports ``connected``, within one total
    deadline that also bounds each status request."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    waiting_on: str | None = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise startup_error(
                "status", "not connected before the deadline", server=waiting_on
            )
        try:
            async with asyncio.timeout(remaining) as budget:
                response = await get_status()
        except TimeoutError:
            raise startup_error(
                "status", "not connected before the deadline", server=waiting_on
            ) from None
        except Exception as error:  # noqa: BLE001 -- any native status failure is a typed startup failure
            raise startup_error("status", "status request failed", error) from None
        if budget.expired():
            # A callback may catch the timer's cancellation and return anyway.
            raise startup_error(
                "status", "not connected before the deadline", server=waiting_on
            )
        ready, waiting_on = _check_status(response, expected)
        if ready:
            return
        await asyncio.sleep(min(_STATUS_POLL_SECONDS, max(0.0, deadline - loop.time())))
