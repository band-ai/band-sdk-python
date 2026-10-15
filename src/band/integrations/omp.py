"""OMP native RPC launch settings and MCP tool-name normalization."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from band.core.defaultmodels import OPENAI_MODEL
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.tools import (
    BAND_MCP_SERVER_NAME,
    canonicalize_mcp_tool_name,
    mcp_tool_spelling,
)

if TYPE_CHECKING:
    from band.integrations.omprpc import OmpRpcClient

OMP_APPROVAL_MODE_FLAG = "--approval-mode"
OMP_APPROVAL_MODE_ALWAYS_ASK = "always-ask"
OMP_YOLO_FLAG = "--yolo"
OMP_AUTO_APPROVE_FLAG = "--auto-approve"
OMP_APPROVAL_MODE_WRITE = "write"
OMP_APPROVAL_MODE_YOLO = "yolo"

DEFAULT_OMP_COMMAND: tuple[str, ...] = ("omp", "--mode", "rpc")

OMP_BARE_UNSAFE_APPROVAL_FLAGS: frozenset[str] = frozenset(
    {OMP_YOLO_FLAG, OMP_AUTO_APPROVE_FLAG}
)

OMP_UNSAFE_APPROVAL_FLAGS: frozenset[str] = frozenset(
    {
        *OMP_BARE_UNSAFE_APPROVAL_FLAGS,
        f"{OMP_APPROVAL_MODE_FLAG} {OMP_APPROVAL_MODE_WRITE}",
        f"{OMP_APPROVAL_MODE_FLAG} {OMP_APPROVAL_MODE_YOLO}",
        f"{OMP_APPROVAL_MODE_FLAG}={OMP_APPROVAL_MODE_WRITE}",
        f"{OMP_APPROVAL_MODE_FLAG}={OMP_APPROVAL_MODE_YOLO}",
    }
)

XD_URL_PREFIX = "xd://"
XD_MCP_PREFIX = "mcp__"
# OMP's own test (xdev.ts HELP_CONTENT_RE) for a device write that shows the
# tool's docs instead of running it.
_XD_HELP_CONTENT = re.compile(r"^\s*(\?|help)?\s*$", re.IGNORECASE)


OMP_PINNED_PACKAGE = "@oh-my-pi/pi-coding-agent@18.8.9"
OMP_MIN_BUN = "1.3.14"

DEFAULT_OMP_MODEL = f"openai/{OPENAI_MODEL}"

# Documented OMP model-provider credential routes (not Vertex / GOOGLE_*).
_OMP_PROVIDER_API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
}

_APPROVAL_MODE_EQ = re.compile(
    rf"^{re.escape(OMP_APPROVAL_MODE_FLAG)}=(.+)$", re.IGNORECASE
)


def omp_model_provider(model: str) -> str:
    """Return the provider segment of a provider-qualified OMP model id."""
    trimmed = model.strip()
    if not trimmed:
        raise ValueError("OMP model must be a non-empty provider-qualified id")
    provider, _, _rest = trimmed.partition("/")
    if not provider or not _rest:
        raise ValueError(f'OMP model must look like "provider/model", got "{model}"')
    return provider.lower()


def omp_provider_api_key_env(provider: str) -> str:
    """The child-process env var OMP expects for one model provider."""
    key = _OMP_PROVIDER_API_KEY_ENV.get(provider.lower())
    if key is None:
        supported = ", ".join(sorted(_OMP_PROVIDER_API_KEY_ENV))
        raise ValueError(
            f"Unsupported OMP model provider {provider!r}; supported: {supported}"
        )
    return key


def omp_provider_env(*, model: str, api_key: str) -> dict[str, str]:
    """Build OMP child env with the selected provider's API key."""
    provider = omp_model_provider(model)
    env_key = omp_provider_api_key_env(provider)
    return {env_key: api_key}


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


def validate_omp_command(command: Sequence[str]) -> None:
    """Reject unsafe approval flags in a command; the selected mode is configured separately."""
    tokens = list(command)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in OMP_BARE_UNSAFE_APPROVAL_FLAGS:
            raise ValueError(f"Unsafe OMP flag {token!r} is not allowed")
        if token == OMP_APPROVAL_MODE_FLAG:
            mode = tokens[index + 1] if index + 1 < len(tokens) else ""
            joined = f"{OMP_APPROVAL_MODE_FLAG} {mode.lower()}".strip()
            if joined in OMP_UNSAFE_APPROVAL_FLAGS:
                raise ValueError(f"Unsafe OMP approval mode {mode!r} is not allowed")
            index += 2
            continue
        match = _APPROVAL_MODE_EQ.match(token)
        if (
            match
            and f"{OMP_APPROVAL_MODE_FLAG}={match.group(1).lower()}"
            in OMP_UNSAFE_APPROVAL_FLAGS
        ):
            raise ValueError(
                f"Unsafe OMP approval mode {match.group(1)!r} is not allowed"
            )
        index += 1


def finalize_omp_command(
    command: Sequence[str],
    *,
    model: str | None = None,
    approval_mode: str = OMP_APPROVAL_MODE_ALWAYS_ASK,
) -> list[str]:
    """Validate ``command`` and append the model and the selected approval mode last."""
    validate_omp_command(command)
    if approval_mode not in (OMP_APPROVAL_MODE_ALWAYS_ASK, OMP_APPROVAL_MODE_YOLO):
        raise ValueError(f"Unsupported OMP approval mode {approval_mode!r}")
    finalized = list(command)
    if model is not None:
        finalized.append(f"--model={model}")
    finalized.extend((OMP_APPROVAL_MODE_FLAG, approval_mode))
    return finalized


def omp_command_in_workspace(command: Sequence[str], workspace: str) -> list[str]:
    """Keep the process on posix_spawn while OMP selects its room workspace."""
    return [*command, f"--cwd={workspace}"]


class OmpMcpServer(BaseModel):
    """An external MCP server projected into an SDK-owned OMP plugin."""

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


class _McpCommand(BaseModel):
    name: str


class _McpCommands(BaseModel):
    commands: list[_McpCommand]


class _ConnectedMcpServer(BaseModel):
    type: Literal["connected"]
    serverName: str


class _FailedMcpServer(BaseModel):
    type: Literal["failed"]
    serverName: str
    error: str


class _McpStartupReceipt(BaseModel):
    systemPromptReady: bool = Field(strict=True)
    servers: list[_ConnectedMcpServer | _FailedMcpServer]


def _mcp_ready_command(plugin: str) -> str:
    return f"band-sdk-mcp-{Path(plugin).parent.name}"


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
    command = _mcp_ready_command(plugin)
    commands = _McpCommands.model_validate(
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
        receipt = _McpStartupReceipt.model_validate_json(
            (Path(plugin) / "receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise OmpStartupError("OMP returned no valid SDK startup receipt") from error
    if not receipt.systemPromptReady:
        raise OmpStartupError(
            "OMP did not load the required Band connection instructions; "
            "rules may be disabled or omitted by the system prompt template"
        )
    expected = {f"band-sdk:{name}" for name in servers}
    for status in receipt.servers:
        if status.serverName not in expected:
            raise OmpStartupError("OMP returned an unexpected MCP startup receipt")
        if isinstance(status, _FailedMcpServer):
            raise OmpStartupError(
                f"OMP MCP server {status.serverName} failed: {status.error}"
            )
    if {status.serverName for status in receipt.servers} != expected or len(
        receipt.servers
    ) != len(expected):
        raise OmpStartupError("OMP did not acknowledge every configured MCP server")


@contextmanager
def omp_mcp_plugin(
    servers: Mapping[str, OmpMcpServer], *, workspace: str, system_prompt: str = ""
) -> Iterator[str | None]:
    """Project connection instructions and servers without changing native user configs."""
    if not servers and not system_prompt:
        yield None
        return
    projected: dict[str, dict[str, object]] = {}
    for name, server in servers.items():
        payload = server.model_dump(
            mode="json", exclude_none=True, exclude_defaults=True
        )
        if server.command:
            # Plugin discovery otherwise rebases launch paths into this temporary overlay.
            cwd = Path(os.path.abspath(Path(workspace) / (server.cwd or ".")))
            payload["cwd"] = str(cwd)
            if server.command.startswith(("./", "../", ".\\", "..\\")):
                payload["command"] = os.path.abspath(cwd / server.command)
        projected[name] = payload
    with tempfile.TemporaryDirectory(prefix="band-omp-") as temporary:
        # Plugin names prefix MCP server names; keep this basename stable across starts.
        root = Path(temporary) / "band-sdk"
        root.mkdir(mode=0o700)
        if system_prompt:
            rules = root / "rules"
            rules.mkdir(mode=0o700)
            rule = rules / f"band-connection-{uuid4().hex}.md"
            fd = os.open(rule, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as policy:
                policy.write(f"---\nalwaysApply: true\n---\n{system_prompt}")
        startup = {
            "servers": [f"band-sdk:{name}" for name in servers],
            "command": _mcp_ready_command(str(root)),
            "receipt": str(root / "receipt.json"),
            "systemPrompt": system_prompt,
        }
        files = (
            (".mcp.json", json.dumps({"mcpServers": projected})),
            # Quiet startup suppresses the native status bus, not just UI rendering.
            ("startup.json", json.dumps({"startup": {"quiet": False}})),
            (
                "startup.js",
                f"const config = {json.dumps(startup)};\n{_MCP_STARTUP_EXTENSION}",
            ),
        )
        for filename, content in files:
            fd = os.open(root / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as config:
                config.write(content)
        yield str(root)


def _omp_mcp_remainder(name_or_path: str) -> str | None:
    """Strip ``xd://`` and/or ``mcp__``; ``None`` when not an OMP MCP spelling."""
    text = name_or_path.removeprefix(XD_URL_PREFIX)
    if not text.startswith(XD_MCP_PREFIX):
        return None
    # SDK-owned plugin roots namespace the configured server before OMP mints its tool name.
    remainder = text.removeprefix(XD_MCP_PREFIX).removeprefix("band_sdk_")
    return remainder or None


def _omp_mcp_wire_candidates(remainder: str) -> list[str]:
    """Wire-name candidates that invert OMP's ``createMCPToolName`` mint.

    OMP mints ``mcp__${server}_${toolWithoutRedundantServerPrefix}``. For Band
    that yields ``mcp__band_send_message`` whose remainder *is* the canonical
    tool name. Double-underscore ``mcp__server__tool`` and unstripped
    ``mcp__band_band_*`` remainders still map through the hyphen form
    ``canonicalize_mcp_tool_name`` already understands.
    """
    if "__" in remainder:
        server, tool = remainder.split("__", 1)
        if server and tool:
            return [mcp_tool_spelling(server, tool)]
        return []
    candidates = [remainder]
    prefix = f"{BAND_MCP_SERVER_NAME}_"
    if remainder.startswith(prefix) and remainder != prefix:
        candidates.append(
            mcp_tool_spelling(BAND_MCP_SERVER_NAME, remainder.removeprefix(prefix))
        )
    return candidates


def normalize_omp_mcp_tool_name(
    name_or_path: str, own_names: Collection[str]
) -> str | None:
    """Map an OMP ``mcp__…`` title or ``xd://mcp__…`` path to a Band tool name."""
    remainder = _omp_mcp_remainder(name_or_path)
    if remainder is None:
        return None
    for candidate in _omp_mcp_wire_candidates(remainder):
        canonical = canonicalize_mcp_tool_name(candidate, own_names)
        if canonical in own_names:
            return canonical
    return None


def normalize_omp_mcp_device_call(
    name: str,
    arguments: Mapping[str, object],
    own_names: Collection[str],
    *,
    kind: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Map OMP MCP device writes / ``mcp__`` titles to canonical Band tool names.

    Only a call OMP executes is the tool. ``kind`` distinguishes an executed
    device write from a read; a read, and a write asking for docs (empty, ``?``
    or ``help`` content), are left as they are. An
    executed write whose payload is missing or not a JSON object keeps the
    tool's name with the raw arguments: the tool refuses it, and that failed
    attempt must still read as the tool's (a failed reply is not narration).
    """
    args = dict(arguments)
    path: str | None = None
    for key in ("path", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.startswith(XD_URL_PREFIX):
            path = value
            break

    if path is not None:
        canonical = normalize_omp_mcp_tool_name(path, own_names)
        content = args.get("content")
        if canonical is None or kind == "read":
            return name, args
        if not isinstance(content, str):
            return (canonical, args) if kind == "execute" else (name, args)
        if _XD_HELP_CONTENT.match(content):
            return name, args
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return canonical, args
        if not isinstance(parsed, dict):
            return canonical, args
        return canonical, parsed

    canonical = normalize_omp_mcp_tool_name(name, own_names)
    if canonical is None:
        return name, args
    return canonical, args
