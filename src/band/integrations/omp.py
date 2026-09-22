"""OMP (oh-my-pi) vocabulary for the ACP client adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from uuid import uuid4

from band.runtime.tools import BAND_MCP_SERVER_NAME, canonicalize_mcp_tool_name

OMP_APPROVAL_MODE_FLAG = "--approval-mode"
OMP_APPROVAL_MODE_ALWAYS_ASK = "always-ask"
OMP_YOLO_FLAG = "--yolo"
OMP_AUTO_APPROVE_FLAG = "--auto-approve"
OMP_APPROVAL_MODE_WRITE = "write"
OMP_APPROVAL_MODE_YOLO = "yolo"

DEFAULT_OMP_ACP_COMMAND: tuple[str, ...] = (
    "omp",
    "acp",
    OMP_APPROVAL_MODE_FLAG,
    OMP_APPROVAL_MODE_ALWAYS_ASK,
)

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

OMP_FORM_APPROVE = "Approve"
OMP_FORM_DENY = "Deny"
OMP_APPROVE_OPTION_ID = "omp-approve"
OMP_DENY_OPTION_ID = "omp-deny"
OMP_APPROVAL_FORM_TOOL_NAME = "omp_approval_form"
OMP_ELICITATION_CALL_ID_PREFIX = "omp-elicitation:"

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
        if token in OMP_BARE_UNSAFE_APPROVAL_FLAGS:
            raise ValueError(f"Unsafe OMP flag {token!r} is not allowed")
        if token == OMP_APPROVAL_MODE_FLAG:
            mode = tokens[index + 1] if index + 1 < len(tokens) else ""
            joined = f"{OMP_APPROVAL_MODE_FLAG} {mode}".strip()
            if joined in OMP_UNSAFE_APPROVAL_FLAGS:
                raise ValueError(f"Unsafe OMP approval mode {mode!r} is not allowed")
            index += 2
            continue
        match = _APPROVAL_MODE_EQ.match(token)
        if (
            match
            and f"{OMP_APPROVAL_MODE_FLAG}={match.group(1)}"
            in OMP_UNSAFE_APPROVAL_FLAGS
        ):
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
    return f"{OMP_ELICITATION_CALL_ID_PREFIX}{session_id}:{uuid4()}"


def _schema_as_mapping(requested_schema: object) -> Mapping[str, object] | None:
    """Coerce a pydantic elicitation schema or plain dict into a mapping."""
    if isinstance(requested_schema, Mapping):
        return requested_schema
    dump = getattr(requested_schema, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def approve_deny_form_field(requested_schema: object) -> str | None:
    """The single Approve/Deny property name, or ``None`` if not that form."""
    schema = _schema_as_mapping(requested_schema)
    if schema is None:
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or len(properties) != 1:
        return None
    field_name, field_schema = next(iter(properties.items()))
    if not isinstance(field_name, str):
        return None
    field_map = _schema_as_mapping(field_schema)
    if field_map is None:
        return None
    if field_map.get("enum") != [OMP_FORM_APPROVE, OMP_FORM_DENY]:
        return None
    return field_name


def is_omp_approve_deny_form(requested_schema: object) -> bool:
    """True when ``requested_schema`` is exactly an Approve/Deny enum form."""
    return approve_deny_form_field(requested_schema) is not None


def _omp_mcp_remainder(name_or_path: str) -> str | None:
    """Strip ``xd://`` and/or ``mcp__``; ``None`` when not an OMP MCP spelling."""
    text = name_or_path.removeprefix(XD_URL_PREFIX)
    if not text.startswith(XD_MCP_PREFIX):
        return None
    remainder = text.removeprefix(XD_MCP_PREFIX)
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
            return [f"{server}-{tool}"]
        return []
    candidates = [remainder]
    prefix = f"{BAND_MCP_SERVER_NAME}_"
    if remainder.startswith(prefix) and remainder != prefix:
        candidates.append(f"{BAND_MCP_SERVER_NAME}-{remainder.removeprefix(prefix)}")
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
) -> tuple[str, dict[str, object]]:
    """Map OMP MCP device writes / ``mcp__`` titles to canonical Band tool names."""
    args = dict(arguments)
    path: str | None = None
    for key in ("path", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.startswith(XD_URL_PREFIX):
            path = value
            break

    if path is not None:
        canonical = normalize_omp_mcp_tool_name(path, own_names)
        if canonical is None:
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

    canonical = normalize_omp_mcp_tool_name(name, own_names)
    if canonical is None:
        return name, args
    return canonical, args
