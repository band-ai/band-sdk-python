"""Gemini adapter using the official google-genai SDK."""

from __future__ import annotations

from band.core.backends.facade import (
    NativeProviderAdapter,
    gemini_contents_from_model_messages,
    make_custom_tool_executor,
    model_messages_from_gemini,
)
from band.core.backends.history import GeminiHistoryPolicy
from band.core.contracts import ModelMessage
from band.core.backends.native import NativeToolLoopBackend
from band.core.exceptions import MissingDependencyError
from band.core.options import UNSET, reject_removed_kwargs
from band.providers.gemini import GeminiProvider
from band.providers.types import GeminiProviderOptions

import logging
from typing import Any, Callable, ClassVar, Unpack


try:
    from google import genai  # type: ignore[missing-module-attribute]
    from google.genai import types  # type: ignore[missing-import]
except ImportError as e:
    raise MissingDependencyError(
        "google-genai is required for Gemini adapter.\n"
        "Install with: pip install 'band-sdk[gemini]'\n"
        "Or: uv add google-genai"
    ) from e

from band.core.instructions import Instruction, InstructionPolicy
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import sanitize_tool_schema
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
)
from band.converters.gemini import GeminiHistoryConverter, GeminiMessages
from band.core.tools import FunctionTool, normalize_additional_tools
from band.runtime.custom_tools import (
    CustomToolDef,
)

logger = logging.getLogger(__name__)


class GeminiAdapter(NativeProviderAdapter[GeminiMessages, list[types.Tool]]):
    """
    Gemini SDK adapter using SimpleAdapter pattern.

    Uses the official google-genai Python SDK with explicit tool-loop control.

    Example:
        adapter = GeminiAdapter(
            model="gemini-2.5-flash",
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
        model: str = "gemini-2.5-flash",
        provider_key: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        max_tool_rounds: int = 20,
        max_retries: int = 2,
        retry_base_delay_s: float = 1.0,
        max_history_messages: int = 200,
        history_converter: GeminiHistoryConverter | None = None,
        additional_tools: list[FunctionTool | Callable[..., Any]] | None = None,
        features: AdapterFeatures | None = None,
        include_base_instructions: bool = True,
        *,
        instructions: str | Instruction | None = None,
        raw_options: dict[str, Any] | None = None,
        **provider_options: Unpack[GeminiProviderOptions],
    ) -> None:
        self._instructions = instructions

        super().__init__(
            history_converter=history_converter or GeminiHistoryConverter(),
            features=features,
        )

        self.model = model
        self._include_base_instructions = include_base_instructions
        self.max_tool_rounds = max_tool_rounds
        self.max_history_messages = max_history_messages

        reject_removed_kwargs(provider_options, also={"gemini_api_key": "provider_key"})

        self._provider = GeminiProvider(
            model=model,
            api_key=provider_key,
            temperature=temperature if temperature is not None else UNSET,
            max_output_tokens=(
                max_output_tokens if max_output_tokens is not None else UNSET
            ),
            max_retries=max_retries,
            retry_base_delay_s=retry_base_delay_s,
            raw_options=raw_options,
            **provider_options,
        )
        # Back-compat mirrors of provider sampling defaults.
        self.max_output_tokens = self._provider.sampling.max_output_tokens
        self.temperature = self._provider.sampling.temperature
        self.max_retries = max_retries
        self.retry_base_delay_s = retry_base_delay_s
        self._system_prompt: str = ""
        normalized_tools = normalize_additional_tools(additional_tools)
        self._function_tools: list[FunctionTool] = normalized_tools
        self._custom_tools: list[CustomToolDef] = [
            function_tool.as_custom_tool_def() for function_tool in normalized_tools
        ]
        self._history_policy = GeminiHistoryPolicy(
            max_history_messages=max_history_messages
        )
        self._backend = NativeToolLoopBackend(
            provider=self._provider,
            history_policy=self._history_policy,
            max_tool_rounds=self.max_tool_rounds,
            on_max_rounds="raise",
            execute_override=make_custom_tool_executor(self._custom_tools),
        )

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
        logger.info("Gemini adapter started for agent: %s", agent_name)

    def _seed_session(self, history: GeminiMessages) -> list[ModelMessage]:
        return model_messages_from_gemini(list(history) if history else [])

    def session_history(self, room_id: str) -> GeminiMessages:
        """The room's conversation, in Gemini's ``Content`` shape.

        Projected from the backend's session on demand — the tool loop owns
        the one copy, so there is nothing here to fall out of step with it.
        """
        return gemini_contents_from_model_messages(self._backend.session(room_id))

    @property
    def client(self) -> genai.Client | None:
        """Gemini SDK client (owned by :attr:`_provider`; may be lazy)."""
        return self._provider.client

    @client.setter
    def client(self, value: genai.Client | None) -> None:
        self._provider.client = value

    def _build_tools(self, tools: AgentToolsProtocol) -> list[types.Tool]:
        """Build Gemini function declarations from platform and custom tools."""
        declarations: list[types.FunctionDeclaration] = []

        openai_schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        )
        for schema in openai_schemas:
            function = schema.get("function", {})
            name = function.get("name")
            if not name:
                continue
            parameters = sanitize_tool_schema(
                function.get("parameters", {"type": "object", "properties": {}}),
                drop_numeric_bounds=True,
                drop_additional_properties=True,
            )
            declarations.append(
                types.FunctionDeclaration(
                    name=name,
                    description=function.get("description", "") or "",
                    parameters_json_schema=parameters,
                )
            )

        for function_tool in self._function_tools:
            spec = function_tool.spec()
            schema = sanitize_tool_schema(
                dict(spec.parameters),
                drop_numeric_bounds=True,
                drop_additional_properties=True,
            )
            declarations.append(
                types.FunctionDeclaration(
                    name=spec.name,
                    description=spec.description,
                    parameters_json_schema=schema,
                )
            )

        if not declarations:
            return []
        return [types.Tool(function_declarations=declarations)]
