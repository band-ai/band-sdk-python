"""Native OMP (``omp --mode rpc``) launch settings and the SDK-owned MCP plugin."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from band.integrations.omp import (
    OMP_CWD_FLAG,
    normalize_omp_mcp_device_call,
    normalize_omp_mcp_tool_name,
    omp_provider_env,
)
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)

DEFAULT_OMP_COMMAND: tuple[str, ...] = ("omp", "--mode", "rpc")

# The native RPC ask dialog and MCP status events need a newer OMP than the
# ACP bridge's pin.
OMP_NATIVE_PINNED_PACKAGE = "@oh-my-pi/pi-coding-agent@18.8.9"

# Plugin names prefix the MCP server names OMP mints, so the plugin root's
# basename must stay stable across starts; its sanitized form is the
# namespace every projected tool name carries.
OMP_PLUGIN_ROOT = "band-sdk"
OMP_PLUGIN_TOOL_NAMESPACE = f"{OMP_PLUGIN_ROOT.replace('-', '_')}_"


def omp_runtime_env(
    *,
    env: Mapping[str, str] | None,
    model: str | None,
    api_key: str | None,
) -> dict[str, str] | None:
    """Build the provider environment, preserving explicitly supplied overrides."""
    credentials = (
        omp_provider_env(model=model, api_key=api_key)
        if model and api_key is not None
        else {}
    )
    return {**credentials, **(env or {})} or None


def omp_command_in_workspace(command: Sequence[str], workspace: str) -> list[str]:
    """Select OMP's room workspace without changing the subprocess launch cwd."""
    return [*command, f"{OMP_CWD_FLAG}={workspace}"]


class OmpMcpServer(BaseModel):
    """An external MCP server projected into the SDK-owned OMP plugin."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    type: Literal["stdio", "http", "sse"] | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def _require_one_transport(self) -> Self:
        if bool(self.command) == bool(self.url):
            raise ValueError("MCP server needs exactly one of command or url")
        if self.command and self.type not in (None, "stdio"):
            raise ValueError("a command requires stdio transport")
        if self.url and self.type == "stdio":
            raise ValueError("a URL requires http or sse transport")
        return self


_MCP_STARTUP_EXTENSION = """
import { writeFileSync } from "node:fs";

export default function (pi) {
  const expected = new Set(config.servers);
  const statuses = new Map();
  let finish;
  const ready = new Promise(resolve => { finish = resolve; });
  const unsubscribe = pi.events.on("mcp:connection-status", event => {
    if (!expected.has(event.serverName) || statuses.has(event.serverName)) return;
    if (event.type !== "connected" && event.type !== "failed") return;
    statuses.set(event.serverName, event);
    if (event.type === "failed" || statuses.size === expected.size) {
      unsubscribe();
      finish();
    }
  });
  if (expected.size === 0) {
    unsubscribe();
    finish();
  }
  pi.registerCommand(config.command, {
    description: "Verify the host-configured MCP connections",
    handler: async (_args, ctx) => {
      await ready;
      const systemPromptReady = !config.systemPrompt
        || ctx.getSystemPrompt().some(block => block.includes(config.systemPrompt.trim()));
      writeFileSync(config.receipt, JSON.stringify({ systemPromptReady, servers: [...statuses.values()] }), { mode: 0o600 });
    },
  });
}
"""


class OmpStartupError(RuntimeError):
    """The required native connection context or MCP tools are not ready."""


class McpCommand(BaseModel):
    name: str


class McpCommands(BaseModel):
    commands: list[McpCommand]


class ConnectedMcpServer(BaseModel):
    type: Literal["connected"]
    serverName: str


class FailedMcpServer(BaseModel):
    type: Literal["failed"]
    serverName: str
    error: str


class McpStartupReceipt(BaseModel):
    systemPromptReady: bool = Field(strict=True)
    servers: list[ConnectedMcpServer | FailedMcpServer]


def _plugin_server_name(name: str) -> str:
    return f"{OMP_PLUGIN_ROOT}:{name}"


def omp_mcp_ready_command(plugin: str) -> str:
    """The slash command the plugin's startup extension registers for its receipt."""
    return f"{OMP_PLUGIN_ROOT}-mcp-{Path(plugin).parent.name}"


def omp_mcp_arguments(plugin: str) -> tuple[str, ...]:
    """Enable native MCP status events without changing the user's persisted settings."""
    return (
        "--plugin-dir",
        plugin,
        "--mode=rpc-ui",
        "--extension",
        str(Path(plugin) / "startup.js"),
        "--config",
        str(Path(plugin) / "startup.json"),
    )


async def omp_mcp_ready(
    runtime: OmpRpcClient,
    plugin: str,
    servers: Mapping[str, OmpMcpServer],
) -> None:
    """Require the connection extension and configured MCP servers before inference."""
    command = omp_mcp_ready_command(plugin)
    commands = McpCommands.model_validate(
        await runtime.request("get_available_commands")
    )
    if not any(item.name == command for item in commands.commands):
        raise OmpStartupError("OMP did not load the required SDK startup extension")

    async def on_event(event: OmpEvent) -> None:
        if event.type == "agent_start":
            raise OmpStartupError(
                "OMP unexpectedly invoked an agent during SDK startup"
            )

    async def on_tool(_call: OmpHostToolCall) -> OmpToolResult:
        raise OmpStartupError("OMP unexpectedly invoked a host tool during SDK startup")

    async def on_ui(_request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(cancelled=True)

    await runtime.prompt(
        f"/{command}",
        on_event=on_event,
        on_tool=on_tool,
        on_ui=on_ui,
    )
    try:
        receipt = McpStartupReceipt.model_validate_json(
            (Path(plugin) / "receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise OmpStartupError("OMP returned no valid SDK startup receipt") from error
    if not receipt.systemPromptReady:
        raise OmpStartupError(
            "OMP did not load the required Band connection instructions; "
            "rules may be disabled or omitted by the system prompt template"
        )
    expected = {_plugin_server_name(name) for name in servers}
    for status in receipt.servers:
        if status.serverName not in expected:
            raise OmpStartupError("OMP returned an unexpected MCP startup receipt")
        if isinstance(status, FailedMcpServer):
            raise OmpStartupError(
                f"OMP MCP server {status.serverName} failed: {status.error}"
            )
    if {status.serverName for status in receipt.servers} != expected or len(
        receipt.servers
    ) != len(expected):
        raise OmpStartupError("OMP did not acknowledge every configured MCP server")


def _projected_server(server: OmpMcpServer, workspace: str) -> dict[str, object]:
    payload = server.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if server.command:
        # Plugin discovery otherwise rebases launch paths into the temporary overlay.
        cwd = Path(os.path.abspath(Path(workspace) / (server.cwd or ".")))
        payload["cwd"] = str(cwd)
        if server.command.startswith(("./", "../", ".\\", "..\\")):
            payload["command"] = os.path.abspath(cwd / server.command)
    return payload


def _write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


@contextmanager
def omp_mcp_plugin(
    servers: Mapping[str, OmpMcpServer], *, workspace: str, system_prompt: str = ""
) -> Iterator[str | None]:
    """Project connection instructions and servers without changing native user configs."""
    if not servers and not system_prompt:
        yield None
        return
    projected = {
        name: _projected_server(server, workspace) for name, server in servers.items()
    }
    with tempfile.TemporaryDirectory(prefix="band-omp-") as temporary:
        root = Path(temporary) / OMP_PLUGIN_ROOT
        root.mkdir(mode=0o700)
        if system_prompt:
            rules = root / "rules"
            rules.mkdir(mode=0o700)
            _write_private(
                rules / f"band-connection-{uuid4().hex}.md",
                f"---\nalwaysApply: true\n---\n{system_prompt}",
            )
        startup = {
            "servers": [_plugin_server_name(name) for name in servers],
            "command": omp_mcp_ready_command(str(root)),
            "receipt": str(root / "receipt.json"),
            "systemPrompt": system_prompt,
        }
        _write_private(root / ".mcp.json", json.dumps({"mcpServers": projected}))
        # Quiet startup suppresses the native status bus, not just UI rendering.
        _write_private(root / "startup.json", json.dumps({"startup": {"quiet": False}}))
        _write_private(
            root / "startup.js",
            f"const config = {json.dumps(startup)};\n{_MCP_STARTUP_EXTENSION}",
        )
        yield str(root)


def normalize_native_mcp_tool_name(
    name_or_path: str, own_names: Collection[str]
) -> str | None:
    """Map a tool name OMP minted under the SDK plugin namespace to a Band tool."""
    return normalize_omp_mcp_tool_name(
        name_or_path, own_names, namespace=OMP_PLUGIN_TOOL_NAMESPACE
    )


def normalize_native_mcp_device_call(
    name: str,
    arguments: Mapping[str, object],
    own_names: Collection[str],
    *,
    kind: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Map a device call under the SDK plugin namespace to a canonical Band tool."""
    return normalize_omp_mcp_device_call(
        name, arguments, own_names, kind=kind, namespace=OMP_PLUGIN_TOOL_NAMESPACE
    )
