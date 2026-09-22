"""OMP (oh-my-pi) vocabulary for the ACP client adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from uuid import uuid4

from band.runtime.tools import BAND_MCP_SERVER_NAME, canonicalize_mcp_tool_name

DEFAULT_OMP_ACP_COMMAND: tuple[str, ...] = (
    "omp",
    "acp",
    "--approval-mode",
    "always-ask",
)

OMP_APPROVAL_MODE_FLAG = "--approval-mode"
OMP_APPROVAL_MODE_ALWAYS_ASK = "always-ask"

OMP_UNSAFE_APPROVAL_FLAGS: frozenset[str] = frozenset(
    {
        "--yolo",
        "--auto-approve",
        "--approval-mode write",
        "--approval-mode yolo",
        "--approval-mode=write",
        "--approval-mode=yolo",
    }
)

XD_URL_PREFIX = "xd://"

OMP_FORM_APPROVE = "Approve"
OMP_FORM_DENY = "Deny"

OMP_PINNED_PACKAGE = "@oh-my-pi/pi-coding-agent@18.2.8"
OMP_MIN_BUN = "1.3.14"

DEFAULT_OMP_MODEL = "google/gemini-2.5-flash"

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
    "cohere": "COHERE_API_KEY",
}

_APPROVAL_MODE_EQ = re.compile(r"^--approval-mode=(.+)$", re.IGNORECASE)


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
    """Build OMP child env with ``OMP_MODEL`` and the provider API key only."""
    provider = omp_model_provider(model)
    env_key = omp_provider_api_key_env(provider)
    return {"OMP_MODEL": model, env_key: api_key}


def validate_omp_command(command: Sequence[str]) -> None:
    """Reject spawn commands that disable OMP's always-ask safety."""
    tokens = list(command)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("--yolo", "--auto-approve"):
            raise ValueError(f"Unsafe OMP flag {token!r} is not allowed")
        if token == OMP_APPROVAL_MODE_FLAG:
            mode = tokens[index + 1] if index + 1 < len(tokens) else ""
            joined = f"{OMP_APPROVAL_MODE_FLAG} {mode}".strip()
            if joined in OMP_UNSAFE_APPROVAL_FLAGS:
                raise ValueError(f"Unsafe OMP approval mode {mode!r} is not allowed")
            index += 2
            continue
        match = _APPROVAL_MODE_EQ.match(token)
        if match and f"--approval-mode={match.group(1)}" in OMP_UNSAFE_APPROVAL_FLAGS:
            raise ValueError(
                f"Unsafe OMP approval mode {match.group(1)!r} is not allowed"
            )
        index += 1


def finalize_omp_command(command: Sequence[str]) -> list[str]:
    """Validate ``command`` and append a final ``always-ask`` override."""
    validate_omp_command(command)
    finalized = list(command)
    finalized.extend((OMP_APPROVAL_MODE_FLAG, OMP_APPROVAL_MODE_ALWAYS_ASK))
    return finalized


def omp_elicitation_call_id(session_id: str) -> str:
    """Stable synthetic tool-call id namespace for declined OMP forms."""
    return f"omp-elicitation:{session_id}:{uuid4()}"


def _schema_as_mapping(requested_schema: object) -> Mapping[str, object] | None:
    """Coerce a pydantic elicitation schema or plain dict into a mapping."""
    if isinstance(requested_schema, Mapping):
        return requested_schema
    dump = getattr(requested_schema, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def is_omp_approve_deny_form(requested_schema: object) -> bool:
    """True when ``requested_schema`` is exactly an Approve/Deny enum form."""
    schema = _schema_as_mapping(requested_schema)
    if schema is None:
        return False
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or len(properties) != 1:
        return False
    field_name, field_schema = next(iter(properties.items()))
    if not isinstance(field_name, str):
        return False
    field_map = _schema_as_mapping(field_schema)
    if field_map is None:
        return False
    enum = field_map.get("enum")
    return enum == [OMP_FORM_APPROVE, OMP_FORM_DENY]


def approve_deny_form_field(requested_schema: object) -> str | None:
    """The single property name for a validated Approve/Deny form schema."""
    if not is_omp_approve_deny_form(requested_schema):
        return None
    schema = _schema_as_mapping(requested_schema)
    assert schema is not None
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    return str(next(iter(properties)))


def _xd_mcp_wire_tool_name(path: str) -> str | None:
    """Translate an OMP ``xd://mcp__…`` device path into Band's ``server-tool`` wire.

    OMP registers MCP tools as ``mcp__<server>_<tool>`` (single underscore after
    the server) and may also surface the double-underscore ``mcp__server__tool``
    spelling. Both map to the hyphen-joined form ``canonicalize_mcp_tool_name``
    already understands for the Band loopback server.
    """
    if not path.startswith(XD_URL_PREFIX):
        return None
    tail = path[len(XD_URL_PREFIX) :]
    if not tail.startswith("mcp__"):
        return None
    remainder = tail.removeprefix("mcp__")
    if "__" in remainder:
        server, tool = remainder.split("__", 1)
        if server and tool:
            return f"{server}-{tool}"
        return None
    # ``mcp__band_band_send_message`` → ``band-band_send_message``
    prefix = f"{BAND_MCP_SERVER_NAME}_"
    if remainder.startswith(prefix) and remainder != prefix:
        return f"{BAND_MCP_SERVER_NAME}-{remainder.removeprefix(prefix)}"
    return None


def normalize_omp_mcp_device_call(
    name: str,
    arguments: Mapping[str, object],
    own_names: Collection[str],
) -> tuple[str, dict[str, object]]:
    """Map OMP ``xd://mcp__…`` write calls to canonical Band MCP tool names."""
    args = dict(arguments)
    path: str | None = None
    for key in ("path", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.startswith(XD_URL_PREFIX):
            path = value
            break
    if path is None:
        return name, args

    wire_name = _xd_mcp_wire_tool_name(path)
    if wire_name is None:
        return name, args

    canonical = canonicalize_mcp_tool_name(wire_name, own_names)
    if canonical not in own_names:
        return name, args

    content = args.get("content")
    if not isinstance(content, str):
        return name, args
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return name, args
    if not isinstance(parsed, dict):
        return name, args
    return canonical, parsed
