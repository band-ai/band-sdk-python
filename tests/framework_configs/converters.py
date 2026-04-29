"""Converter configuration registry for parameterized conformance tests.

Each ConverterConfig describes a framework's converter properties, behavioral
flags, and factory function so that conformance tests can run identical logic
across all registered converters.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from tests.framework_configs.output_adapters import OutputAdapter

from tests.framework_configs._sentinel import IN_CI

__all__ = [
    "ConverterConfig",
    "CONVERTER_CONFIGS",
    "CONVERTER_EXCLUDED_MODULES",
    "SenderBehavior",
]

# Populated lazily via __getattr__ to avoid top-level converter imports.
CONVERTER_CONFIGS: list[ConverterConfig]


class SenderBehavior(StrEnum):
    """How a converter handles empty or missing sender_name."""

    CONTENT_AS_IS = "content_as_is"  # content returned without prefix
    BRACKETS_EMPTY = "brackets_empty"  # "[]: content"
    UNKNOWN_PREFIX = "unknown_prefix"  # "[Unknown]: content"


@dataclass(frozen=True)
class ConverterConfig:
    """Describes a framework converter for parameterized testing."""

    # Identity
    framework_id: str
    display_name: str

    # Factory callable: (**kwargs) -> converter instance
    converter_factory: Callable[..., Any]

    # Output shape
    empty_result: Any  # [] or ""

    # Output adapter for uniform assertions (required, no default)
    output_adapter: OutputAdapter

    # Behavioral flags
    filters_own_messages: bool = True
    skips_tool_events: bool = False

    # How empty/missing sender_name is handled
    empty_sender_behavior: SenderBehavior = SenderBehavior.CONTENT_AS_IS
    missing_sender_behavior: SenderBehavior = SenderBehavior.CONTENT_AS_IS

    # Edge case flags
    skips_empty_content: bool = False
    has_role_concept: bool = True

    # Output shape flags
    has_sender_metadata: bool = False  # output includes sender/sender_type fields
    other_agent_output_role: str = "user"  # role assigned to other agents' messages


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def _anthropic_factory(**kw: Any) -> Any:
    from thenvoi.converters.anthropic import AnthropicHistoryConverter

    return AnthropicHistoryConverter(**kw)


def _langchain_factory(**kw: Any) -> Any:
    from thenvoi.converters.langchain import LangChainHistoryConverter

    return LangChainHistoryConverter(**kw)


def _crewai_factory(**kw: Any) -> Any:
    from thenvoi.converters.crewai import CrewAIHistoryConverter

    return CrewAIHistoryConverter(**kw)


def _claude_sdk_factory(**kw: Any) -> Any:
    from thenvoi.converters.claude_sdk import ClaudeSDKHistoryConverter

    return ClaudeSDKHistoryConverter(**kw)


def _pydantic_ai_factory(**kw: Any) -> Any:
    from thenvoi.converters.pydantic_ai import PydanticAIHistoryConverter

    return PydanticAIHistoryConverter(**kw)


def _parlant_factory(**kw: Any) -> Any:
    from thenvoi.converters.parlant import ParlantHistoryConverter

    return ParlantHistoryConverter(**kw)


def _gemini_factory(**kw: Any) -> Any:
    from thenvoi.converters.gemini import GeminiHistoryConverter

    return GeminiHistoryConverter(**kw)


def _google_adk_factory(**kw: Any) -> Any:
    from thenvoi.converters.google_adk import GoogleADKHistoryConverter

    return GoogleADKHistoryConverter(**kw)


# ---------------------------------------------------------------------------
# Registry  (built lazily to avoid top-level converter imports)
# ---------------------------------------------------------------------------


def _build_anthropic_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import DictListOutputAdapter

    return ConverterConfig(
        framework_id="anthropic",
        display_name="Anthropic",
        converter_factory=_anthropic_factory,
        empty_result=[],
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        output_adapter=DictListOutputAdapter(),
    )


def _build_langchain_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import LangChainOutputAdapter

    return ConverterConfig(
        framework_id="langchain",
        display_name="LangChain",
        converter_factory=_langchain_factory,
        empty_result=[],
        empty_sender_behavior=SenderBehavior.BRACKETS_EMPTY,
        # LangChain uses hist.get("sender_name", ""), so a *missing* key
        # produces the same "[]: content" as an empty string (brackets_empty).
        missing_sender_behavior=SenderBehavior.BRACKETS_EMPTY,
        output_adapter=LangChainOutputAdapter(),
    )


def _build_crewai_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import SenderDictListAdapter

    return ConverterConfig(
        framework_id="crewai",
        display_name="CrewAI",
        converter_factory=_crewai_factory,
        empty_result=[],
        skips_tool_events=True,
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        has_sender_metadata=True,
        # CrewAI treats other agents as peers (assistant role) rather than
        # remapping them to user, because its crew workflow expects all agent
        # outputs to carry the "assistant" role.
        other_agent_output_role="assistant",
        output_adapter=SenderDictListAdapter(),
    )


def _build_claude_sdk_config() -> ConverterConfig:
    from thenvoi.converters.claude_sdk import ClaudeSDKSessionState
    from tests.framework_configs.output_adapters import ClaudeSDKOutputAdapter

    return ConverterConfig(
        framework_id="claude_sdk",
        display_name="ClaudeSDK",
        converter_factory=_claude_sdk_factory,
        empty_result=ClaudeSDKSessionState(text=""),
        empty_sender_behavior=SenderBehavior.BRACKETS_EMPTY,
        missing_sender_behavior=SenderBehavior.UNKNOWN_PREFIX,
        skips_empty_content=True,
        has_role_concept=False,
        output_adapter=ClaudeSDKOutputAdapter(),
    )


def _build_pydantic_ai_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import PydanticAIOutputAdapter

    return ConverterConfig(
        framework_id="pydantic_ai",
        display_name="PydanticAI",
        converter_factory=_pydantic_ai_factory,
        empty_result=[],
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        output_adapter=PydanticAIOutputAdapter(),
    )


def _build_parlant_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import SenderDictListAdapter

    return ConverterConfig(
        framework_id="parlant",
        display_name="Parlant",
        converter_factory=_parlant_factory,
        empty_result=[],
        filters_own_messages=False,
        skips_tool_events=True,
        skips_empty_content=True,
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        has_sender_metadata=True,
        # Parlant keeps other agents as "assistant" because its server-side
        # session model treats all bot-originated messages uniformly; remapping
        # to "user" would break the Parlant conversation contract.
        other_agent_output_role="assistant",
        output_adapter=SenderDictListAdapter(),
    )


def _build_gemini_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import GeminiOutputAdapter

    return ConverterConfig(
        framework_id="gemini",
        display_name="Gemini",
        converter_factory=_gemini_factory,
        empty_result=[],
        filters_own_messages=False,  # Gemini keeps own-agent text as model role for turn alternation
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        output_adapter=GeminiOutputAdapter(),
    )


# Converter modules intentionally excluded from conformance tests.
# _tool_parsing and _utils are internal utility modules (shared helpers, not converters).
# a2a / a2a_gateway use the A2A protocol which has a different message schema.
# acp_client / acp_server use ACP protocol session updates, not standard convert().
# codex, letta, and opencode are metadata-only converters that extract session state
# from task event metadata rather than converting message history. They don't implement
# the standard convert() -> framework-format contract that conformance tests validate.
# crewai_flow is a metadata-only converter for orchestration state reconstructed from
# task events; same exception as codex/letta/opencode.
CONVERTER_EXCLUDED_MODULES: frozenset[str] = frozenset(
    {
        "_tool_parsing",
        "_utils",
        "a2a",
        "a2a_gateway",
        "acp_client",
        "acp_server",
        "codex",
        "crewai_flow",
        "letta",
        "opencode",
    }
)


def _build_google_adk_config() -> ConverterConfig:
    from tests.framework_configs.output_adapters import GoogleADKOutputAdapter

    return ConverterConfig(
        framework_id="google_adk",
        display_name="GoogleADK",
        converter_factory=_google_adk_factory,
        empty_result=[],
        empty_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        missing_sender_behavior=SenderBehavior.CONTENT_AS_IS,
        output_adapter=GoogleADKOutputAdapter(),
    )


_CONVERTER_CONFIG_BUILDERS: list[Callable[[], ConverterConfig]] = [
    _build_anthropic_config,
    _build_langchain_config,
    _build_crewai_config,
    _build_claude_sdk_config,
    _build_pydantic_ai_config,
    _build_parlant_config,
    _build_gemini_config,
    _build_google_adk_config,
]


@functools.lru_cache(maxsize=1)
def _build_converter_configs() -> list[ConverterConfig]:
    """Build configs lazily so converter imports happen only when needed.

    Each framework config is built independently so that an import failure
    in one framework does not prevent the remaining frameworks from being
    tested.  In CI, failures are raised immediately to surface broken configs.
    """
    import logging

    logger = logging.getLogger(__name__)
    configs: list[ConverterConfig] = []
    for builder in _CONVERTER_CONFIG_BUILDERS:
        try:
            configs.append(builder())
        except Exception as exc:
            if IN_CI:
                raise RuntimeError(
                    f"Converter config builder {builder.__name__} failed in CI: {exc}"
                ) from exc
            logger.warning(
                "Skipping converter config from %s: %s", builder.__name__, exc
            )
    return configs


def __getattr__(name: str) -> Any:
    if name == "CONVERTER_CONFIGS":
        return _build_converter_configs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
