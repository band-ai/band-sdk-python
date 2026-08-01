"""
Anthropic adapter using SimpleAdapter pattern.

Extracted from band.integrations.anthropic.agent.BandAnthropicAgent.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar, Unpack, cast

from anthropic import AsyncAnthropic
from anthropic.types import ToolParam

from band.core.turn.facade import (
    NativeProviderAdapter,
    anthropic_dicts_from_model_messages,
    make_custom_tool_executor,
    model_messages_from_anthropic,
)
from band.core.turn.native import NativeToolLoopBackend
from band.core.contracts import ModelMessage
from band.core.instructions import Instruction, InstructionPolicy
from band.core.options import UNSET, reject_removed_kwargs
from band.core.protocols import AgentToolsProtocol
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
)
from band.providers.anthropic import AnthropicProvider
from band.providers.types import AnthropicProviderOptions
from band.converters.anthropic import AnthropicHistoryConverter, AnthropicMessages
from band.core.tools import (
    FunctionTool,
    normalize_additional_tools,
    tool_spec_to_anthropic_schema,
)
from band.runtime.custom_tools import (
    CustomToolDef,
)

logger = logging.getLogger(__name__)


class AnthropicAdapter(NativeProviderAdapter[AnthropicMessages, list[ToolParam]]):
    """
    Anthropic SDK adapter using SimpleAdapter pattern.

    Native-kind ``SimpleAdapter``: owns a ``NativeToolLoopBackend`` +
    ``AnthropicProvider``. Shared turn body is a private helper
    (``NativeProviderAdapter``); this class supplies Anthropic schemas and
    bootstrap projection only.

    Example:
        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5-20250929",
            instructions="You are a helpful assistant.",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY},
                emit={Emit.EXECUTION},
            ),
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        provider_key: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        max_tool_rounds: int | None = None,
        history_converter: AnthropicHistoryConverter | None = None,
        additional_tools: list[FunctionTool | Callable[..., Any]] | None = None,
        features: AdapterFeatures | None = None,
        include_base_instructions: bool = True,
        *,
        instructions: str | Instruction | None = None,
        raw_options: dict[str, Any] | None = None,
        **provider_options: Unpack[AnthropicProviderOptions],
    ):
        self._instructions = instructions

        super().__init__(
            history_converter=history_converter or AnthropicHistoryConverter(),
            features=features,
        )

        self.model = model
        self._include_base_instructions = include_base_instructions

        reject_removed_kwargs(
            provider_options, also={"anthropic_api_key": "provider_key"}
        )

        # Provider owns the Anthropic client; adapter is a compatibility façade.
        self._provider = AnthropicProvider(
            model=model,
            api_key=provider_key,
            temperature=temperature if temperature is not None else UNSET,
            max_output_tokens=(
                max_output_tokens if max_output_tokens is not None else UNSET
            ),
            raw_options=raw_options,
            **provider_options,
        )
        # Back-compat alias used by existing call sites / tests.
        self.max_tokens = self._provider.sampling.max_output_tokens or 4096

        # Per-room conversation history (Anthropic SDK is stateless)
        # Rendered system prompt (set after start)
        self._system_prompt: str = ""
        # Custom tools (user-provided)
        normalized_tools = normalize_additional_tools(additional_tools)
        self._function_tools: list[FunctionTool] = normalized_tools
        self._custom_tools: list[CustomToolDef] = [
            function_tool.as_custom_tool_def() for function_tool in normalized_tools
        ]
        # Unbounded by default: Claude ends a turn by answering, and capping
        # rounds would truncate long legitimate tool chains that worked before.
        # Pass ``max_tool_rounds`` to bound a runaway loop.
        self.max_tool_rounds = max_tool_rounds
        self._backend = NativeToolLoopBackend(
            provider=self._provider,
            max_tool_rounds=max_tool_rounds,
            execute_override=make_custom_tool_executor(self._custom_tools),
        )

    # --- Copied from BandAnthropicAgent._on_started ---
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt after agent metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._system_prompt = InstructionPolicy(
            include_base_instructions=self._include_base_instructions,
            features=self.features,
        ).render(
            agent_name=agent_name,
            agent_description=agent_description,
            instructions=self._instructions,
        )
        self._backend.system = self._system_prompt
        logger.info("Anthropic adapter started for agent: %s", agent_name)

    def _build_tools(self, tools: AgentToolsProtocol) -> list[ToolParam]:
        """Platform schemas in Anthropic shape, plus this adapter's own tools."""
        raw_schemas = tools.get_anthropic_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        )
        schemas: list[ToolParam] = (
            list(raw_schemas) if isinstance(raw_schemas, (list, tuple)) else []
        )
        schemas.extend(
            cast(
                list[ToolParam],
                [
                    tool_spec_to_anthropic_schema(function_tool.spec())
                    for function_tool in self._function_tools
                ],
            )
        )
        return schemas

    def _seed_session(self, history: AnthropicMessages) -> list[ModelMessage]:
        return model_messages_from_anthropic(list(history) if history else [])

    def session_history(self, room_id: str) -> AnthropicMessages:
        """The room's conversation, in Anthropic's message shape.

        Projected from the backend's session on demand — the tool loop owns
        the one copy, so there is nothing here to fall out of step with it.
        """
        return anthropic_dicts_from_model_messages(self._backend.session(room_id))

    @property
    def client(self) -> AsyncAnthropic:
        """Anthropic SDK client (owned by :attr:`_provider`)."""
        return self._provider.client

    @client.setter
    def client(self, value: AsyncAnthropic) -> None:
        self._provider.client = value

    # --- Copied from BaseFrameworkAgent._report_error ---
