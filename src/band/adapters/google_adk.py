"""
Google ADK adapter using SimpleAdapter pattern.

Integrates with the Google Agent Development Kit (ADK) to run Gemini-powered
agents on the Band platform. Uses ADK's built-in Runner for tool loop
management and session handling.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

import httpx
from band_sdk_core import AgentFailure
from pydantic import Field, PositiveInt, ValidationError
from typing_extensions import Unpack

from band.converters.google_adk import GoogleADKHistoryConverter, GoogleADKMessages
from band.core.adapterconfig import DEFAULT_MAX_TOOL_ROUNDS, BaseAdapterConfig
from band.core.defaultmodels import GEMINI_MODEL
from band.core.exceptions import (
    HOST_CONTROL_ERRORS,
    ProviderRunError,
    ToolRoundsExceededError,
)
from band.core.protocols import AgentToolsProtocol, generic_provider_failure
from band.core.simple_adapter import BatchInputAdapter
from band.core.tool_filter import sanitize_tool_schema
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
    ids_of,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tools_to_schemas,
    declared_effect,
    find_custom_tool,
    run_custom_tool,
)
from band.runtime.formatters import format_current_inputs
from band.runtime.prompts import SYSTEM_UPDATE_PREFIX, render_system_prompt
from band.runtime.tools import (
    BandTool,
    image_block_placeholder,
    is_image_passthrough_result,
    redact_tool_call_args,
)
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.schema import (
    format_tool_validation_error,
    is_failed_tool_output,
    tool_execution_failure,
)
from band.runtime.tools.types import TurnEffect

if TYPE_CHECKING:
    from google.adk.runners import InMemoryRunner
    from google.adk.tools import ToolContext
    from google.genai import Client

    from band.integrations.google_adk.decline import DeclineBatch

logger = logging.getLogger(__name__)

_APP_NAME = "band"
_PROVIDER = "google_adk"

HISTORY_CONTEXT_START: Final[str] = "[Previous conversation context]"
HISTORY_CONTEXT_END: Final[str] = "[End of previous context]"

# Extra HTTP attempts per generation request (total attempts = max_retries + 1).
GOOGLE_ADK_MAX_GENERATION_RETRIES: Final[int] = 10

# Candidate method names that google-adk BaseTool may use to expose tool
# declarations.  The bridge overrides every match found on the installed
# version so it keeps working if ADK renames the internal API.
_DECLARATION_CANDIDATES: tuple[str, ...] = (
    "_get_declaration",  # google-adk 1.x (current internal API)
    "get_declaration",  # likely public rename candidate
)


@dataclass(frozen=True)
class TurnPrompt:
    """Carry rendered input and transcript sizes for orchestration diagnostics."""

    text: str
    original_transcript_chars: int
    retained_transcript_chars: int


def _redacted_function_response_output(tool_name: str, response: Any) -> str:
    """The text a tool_result event reports for one ADK function response.

    ``run_async`` always ``json.dumps`` a non-str tool result before
    returning it (ADK requires a plain string or dict return); ADK's own
    ``__build_response_event`` then wraps a non-dict result as
    ``{"result": <that json string>}`` since its spec requires a dict.
    ``str()``ing that wrapper for ``band_read_room_file``'s image branch
    would embed the full base64 payload, so unwrap and check it first.
    """
    if not response:
        return ""
    if tool_name == BandTool.READ_ROOM_FILE and isinstance(response, dict):
        wrapped = response.get("result")
        if isinstance(wrapped, str):
            try:
                parsed = json.loads(wrapped)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and is_image_passthrough_result(
                tool_name, parsed
            ):
                return image_block_placeholder(len(parsed["content"]))
    return str(response)


def _sanitize_adk_agent_name(agent_name: str) -> str:
    """Return an ADK-valid internal agent name.

    Band display names may contain spaces, punctuation, start with digits,
    or use ADK-reserved words. Google ADK requires ``Agent.name`` to be a
    Python identifier and reserves ``user`` for end-user input, so this
    internal name is normalized separately from the public Band name.
    """
    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", agent_name or "band_agent")
    if not safe_name.isidentifier() or safe_name == "user":
        safe_name = f"band_{safe_name}"
    return safe_name


@functools.lru_cache(maxsize=1)
def _require_adk() -> tuple[type, type, type, type, Any]:
    """Import Google ADK dependencies lazily.

    Cached after the first successful call so repeated access is free.
    Only triggered when ``GoogleADKAdapter`` is instantiated, not at
    module import time.

    Returns:
        (ADKAgent, InMemoryRunner, BaseTool, Gemini, types) tuple.

    Raises:
        ImportError: If google-adk is not installed.
    """
    try:
        from google import (  # noqa: PLC0415 -- optional google_adk extra
            genai,
        )
        from google.adk import (  # noqa: PLC0415 -- genuinely deferred; google_adk extra kept out of this module's unconditional import surface
            Agent as ADKAgent,
        )
        from google.adk.models.google_llm import (  # noqa: PLC0415 -- optional google_adk extra
            Gemini,
        )
        from google.adk.runners import (  # noqa: PLC0415 -- genuinely deferred; google_adk extra kept out of this module's unconditional import surface
            InMemoryRunner,
        )
        from google.adk.tools import (  # noqa: PLC0415 -- genuinely deferred; google_adk extra kept out of this module's unconditional import surface
            BaseTool,
        )
    except ImportError as exc:
        raise ImportError(
            "google-adk is required for GoogleADKAdapter. "
            "Install with: uv add band-sdk[google_adk]"
        ) from exc

    class BoundedGemini(Gemini):
        @functools.cached_property
        def httpx_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient()

        @functools.cached_property
        def api_client(self) -> Client:
            tracking = self._tracking_headers
            headers: dict[str, str] = tracking() if callable(tracking) else tracking
            client_options: dict[str, Any] = {}
            if self.model.startswith("projects/"):
                client_options["vertexai"] = True
            # aiohttp reconnects outside genai's configured attempt budget.
            return genai.Client(
                **client_options,
                http_options=genai.types.HttpOptions(
                    headers=headers,
                    retry_options=self.retry_options,
                    httpx_async_client=self.httpx_client,
                ),
            )

        async def aclose(self) -> None:
            client = self.__dict__.get("api_client")
            httpx_client = self.__dict__.get("httpx_client")
            try:
                if client is not None:
                    await client.aio.aclose()
            finally:
                try:
                    if httpx_client is not None:
                        await httpx_client.aclose()
                finally:
                    if client is not None:
                        client.close()

    return ADKAgent, InMemoryRunner, BaseTool, BoundedGemini, genai.types


@functools.lru_cache(maxsize=1)
def _get_tool_bridge_class() -> type:
    """Build the ``_BandToolBridge`` class lazily.

    Defined inside a factory because it needs ``BaseTool`` as its base
    class, which requires ``google-adk`` to be installed.  Cached so the
    class is created only once.

    The factory probes ``BaseTool`` for every name in
    ``_DECLARATION_CANDIDATES`` and overrides all that exist, so the
    bridge keeps working if ADK renames or publicises the method.  At
    least one candidate must be present.

    After building the class a smoke-test instantiation verifies that
    the declaration mechanism works end-to-end (not just that the method
    exists), catching signature changes that ``hasattr`` alone would miss.
    """
    _, _, BaseTool, _, types = _require_adk()

    # Detect which declaration methods the installed BaseTool exposes.
    active_methods = [
        name
        for name in _DECLARATION_CANDIDATES
        if callable(getattr(BaseTool, name, None))
    ]

    if not active_methods:
        raise RuntimeError(
            "google-adk BaseTool has no known declaration method "
            f"(tried: {', '.join(_DECLARATION_CANDIDATES)}). "
            "This adapter relies on overriding the declaration method "
            "(pinned to google-adk >=1.10,<3). Your installed version "
            "may be incompatible."
        )

    logger.debug(
        "google-adk BaseTool declaration method(s) detected: %s",
        ", ".join(active_methods),
    )

    class _BandToolBridge(BaseTool):
        """Bridges a Band platform tool to Google ADK.

        Wraps a tool schema from AgentToolsProtocol into a BaseTool that ADK
        can register with its agent. Execution delegates to the platform's
        execute_tool_call method.
        """

        # Inherited from BaseTool (declared for pyrefly visibility)
        name: str
        description: str

        def __init__(
            self,
            tool_name: str,
            tool_description: str,
            parameters_schema: dict[str, Any],
            tools: AgentToolsProtocol,
            custom_tools: list[CustomToolDef],
            batch: DeclineBatch | None = None,
        ):
            super().__init__(name=tool_name, description=tool_description)
            self._parameters_schema = parameters_schema
            self._tools = tools
            self._custom_tools = custom_tools
            self._batch = batch

            # Eagerly build and cache the declaration so schema errors
            # surface at construction time rather than mid-conversation.
            sanitized = sanitize_tool_schema(
                parameters_schema,
                drop_numeric_bounds=True,
                drop_additional_properties=True,
            )
            try:
                # The `parameters` field is Gemini's restricted OpenAPI Schema
                # subset, which rejects JSON-Schema ``$ref``/``$defs`` — and
                # Pydantic emits exactly those for enum-typed params (e.g.
                # band_list_memories' scope/system/type). Schema.from_json_schema
                # dereferences them into an inline, ref-free Schema that is valid
                # on both the Gemini Developer API and Vertex AI (unlike
                # parameters_json_schema, whose $ref support on Vertex is not
                # guaranteed).
                self._cached_declaration = types.FunctionDeclaration(
                    name=tool_name,
                    description=tool_description,
                    parameters=types.Schema.from_json_schema(
                        json_schema=types.JSONSchema(**sanitized)
                    ),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to build FunctionDeclaration for tool '{tool_name}': {exc}"
                ) from exc

        def _build_declaration(self) -> types.FunctionDeclaration:
            """Return the eagerly-built FunctionDeclaration.

            All declaration method overrides delegate here so there is a
            single code path regardless of which ADK method name is active.
            """
            return self._cached_declaration

        async def run_async(
            self,
            *,
            args: dict[str, Any],
            tool_context: ToolContext,
        ) -> Any:
            """Execute the tool via Band's AgentToolsProtocol."""
            effect = TurnEffect.OBSERVE
            round_outcome = self._batch.capture() if self._batch is not None else None
            try:
                custom_tool = find_custom_tool(self._custom_tools, self.name)
                if custom_tool:
                    effect = declared_effect(custom_tool[1]) or TurnEffect.OBSERVE
                    outcome = await run_custom_tool(
                        custom_tool, args, tools=self._tools
                    )
                else:
                    effect = turn_effect(self.name)
                    outcome = await self._tools.execute_tool_call_structured(
                        self.name, args
                    )
                result, succeeded = outcome.value, outcome.ok
            except HOST_CONTROL_ERRORS:
                raise
            except ValidationError as exc:
                # Platform validation can raise before a structured outcome exists.
                result = format_tool_validation_error(self.name, exc)
                succeeded = False
                logger.error("Tool %s validation failed", self.name)
            except ValueError as exc:
                logger.error("Invalid arguments for %s: %s", self.name, exc)
                result, succeeded = str(exc), False
            except Exception as exc:
                logger.exception("Tool %s failed", self.name)
                result = tool_execution_failure(self.name, exc)
                succeeded = False

            if succeeded and not isinstance(result, str):
                result = json.dumps(result, default=str)

            if self._batch is not None:
                self._batch.completed(
                    tool_context.invocation_id,
                    self.name,
                    succeeded,
                    effect,
                    round_outcome=round_outcome,
                )
            if not succeeded:
                return {"ok": False, "error": result}
            return result

    # Override every detected declaration method so the bridge works
    # even if ADK renames the internal API in a future minor release.
    for method_name in active_methods:
        setattr(
            _BandToolBridge,
            method_name,
            _BandToolBridge._build_declaration,
        )

    # Smoke-test: verify the declaration mechanism works end-to-end.
    # Catches signature changes that hasattr alone would miss.
    try:
        _probe = _BandToolBridge(
            tool_name="_probe",
            tool_description="probe",
            parameters_schema={},
            tools=None,  # type: ignore[arg-type]
            custom_tools=[],
        )
        _decl = getattr(_probe, active_methods[0])()
        if _decl is None or not hasattr(_decl, "name"):
            raise RuntimeError("Declaration probe returned unexpected value")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "google-adk BaseTool declaration smoke-test failed. "
            f"Method '{active_methods[0]}' exists but did not return a valid "
            "FunctionDeclaration. The adapter is pinned to google-adk "
            ">=1.10,<3 — your installed version may be incompatible."
        ) from exc

    return _BandToolBridge


class GoogleADKAdapterConfig(BaseAdapterConfig):
    """Settings for a Google ADK agent.

    Attributes:
        model: Gemini model name passed to the ADK agent.
        system_prompt: Replaces the rendered Band system prompt entirely.
        custom_section: Extra instructions appended to the rendered prompt.
        max_tool_rounds: Model calls a single turn may make before it fails.
        max_retries: Extra HTTP attempts per provider generation request;
            never retries a tool or turn. Zero disables recovery. Capped at
            :data:`GOOGLE_ADK_MAX_GENERATION_RETRIES`.
        max_history_messages: Most recent room messages replayed as a
            transcript at the start of each turn.
        max_transcript_chars: Character cap on that transcript; older lines
            are dropped first.
    """

    model: str = GEMINI_MODEL
    system_prompt: str | None = None
    custom_section: str | None = None
    # ADK's RunConfig rejects ``sys.maxsize`` as ``max_llm_calls``.
    max_tool_rounds: int = Field(default=DEFAULT_MAX_TOOL_ROUNDS, gt=0, lt=sys.maxsize)
    max_retries: int = Field(
        default=2,
        ge=0,
        le=GOOGLE_ADK_MAX_GENERATION_RETRIES,
    )
    max_history_messages: PositiveInt = 50
    max_transcript_chars: PositiveInt = 100_000


class GoogleADKAdapter(BatchInputAdapter[GoogleADKMessages]):
    """
    Google ADK adapter using SimpleAdapter pattern.

    Uses Google's Agent Development Kit with Gemini models for agent
    interactions, with automatic tool bridging and session management.

    Tool bridges are created per turn with direct references
    to the current ``AgentToolsProtocol`` and custom tools, so each
    invocation is self-contained and safe for concurrent use.

    Example:
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(custom_section="You are a helpful assistant.")
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.TOOL_CALLS, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: GoogleADKAdapterConfig | None = None,
        *,
        history_converter: GoogleADKHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ):
        """Create an adapter that runs a Google ADK agent per turn.

        Args:
            config: Model, prompt and transcript settings; see
                :class:`GoogleADKAdapterConfig`.
            history_converter: Optional custom history converter.
            additional_tools: Optional portable ``CustomToolDef``
                (InputModel, handler) tuples.
        """
        # Validate google-adk is installed early (cached, so cheap on repeat).
        _require_adk()

        super().__init__(
            history_converter=history_converter or GoogleADKHistoryConverter(),
            **features,
        )

        self.config = config or GoogleADKAdapterConfig()

        # Custom tools (user-provided)
        self._custom_tools: list[CustomToolDef] = additional_tools or []

        # Effective system prompt (rendered in on_started)
        self._system_prompt: str = ""

        # Per-room accumulated message history for transcript injection.
        # A fresh InMemoryRunner is created per message, so continuity comes
        # from injecting the accumulated transcript, not from runner state.
        # Thread-safety: the runtime's ExecutionContext guarantees that
        # turns run sequentially per room (single asyncio.Task per
        # room with an asyncio.Queue), so no lock is needed here.
        self._room_history: dict[str, GoogleADKMessages] = {}

        # Per-room session IDs for logging/debugging.
        self._room_sessions: dict[str, str] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt and create ADK agent after metadata is fetched.

        Prompt precedence (matches Anthropic/Gemini):
          1. If ``config.system_prompt`` is set, it wins —
             ``custom_section`` and ``features``-based capability sections are
             ignored. The override is passed through verbatim.
          2. Otherwise, ``render_system_prompt`` renders the SDK base prompt
             plus ``custom_section``, with capability sections gated on
             ``features.capabilities``.
        """
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self.config.system_prompt or render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.config.custom_section or "",
            features=self.features,
        )

        logger.info("Google ADK adapter started for agent: %s", agent_name)

    def _build_adk_tools(
        self, tools: AgentToolsProtocol, batch: DeclineBatch | None = None
    ) -> list[Any]:
        """Build ADK tool bridges from Band tool schemas."""
        ToolBridge = _get_tool_bridge_class()
        openai_schemas = tools.get_openai_tool_schemas(
            capabilities=self.features.capabilities,
        )

        adk_tools: list[Any] = []
        for schema in openai_schemas:
            func_def = schema["function"]
            adk_tools.append(
                ToolBridge(
                    tool_name=func_def["name"],
                    tool_description=func_def.get("description", ""),
                    parameters_schema=func_def.get("parameters", {}),
                    tools=tools,
                    custom_tools=self._custom_tools,
                    batch=batch,
                )
            )

        # Add custom tool bridges
        if self._custom_tools:
            custom_schemas = custom_tools_to_schemas(self._custom_tools, "openai")
            for schema in custom_schemas:
                func_def = schema["function"]
                adk_tools.append(
                    ToolBridge(
                        tool_name=func_def["name"],
                        tool_description=func_def.get("description", ""),
                        parameters_schema=func_def.get("parameters", {}),
                        tools=tools,
                        custom_tools=self._custom_tools,
                        batch=batch,
                    )
                )

        return adk_tools

    def _create_runner(self, tools: AgentToolsProtocol) -> InMemoryRunner:
        """Create a fresh ADK InMemoryRunner with current tools."""
        ADKAgent, InMemoryRunnerCls, _, Gemini, types = _require_adk()
        from band.integrations.google_adk.decline import (  # noqa: PLC0415 -- optional google_adk dependency
            DeclineBatch,
        )

        batch = DeclineBatch()
        adk_tools = self._build_adk_tools(tools, batch)

        adk_agent = ADKAgent(
            name=_sanitize_adk_agent_name(self.agent_name),
            model=Gemini(
                model=self.config.model,
                retry_options=types.HttpRetryOptions(
                    attempts=self.config.max_retries + 1
                ),
            ),
            instruction=self._system_prompt,
            tools=adk_tools,
            before_model_callback=batch.before_model,
            after_model_callback=batch.after_model,
        )

        return InMemoryRunnerCls(
            agent=adk_agent,
            app_name=_APP_NAME,
        )

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: GoogleADKMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle one turn over its original messages via Google ADK.

        Uses ADK's Runner for the full tool loop. The runner handles
        LLM calls, tool execution, and conversation management.
        """
        _, _, _, _, types = _require_adk()
        from google.adk.agents.invocation_context import (  # noqa: PLC0415 -- optional google_adk extra
            LlmCallsLimitExceededError,
        )
        from google.adk.agents.run_config import (  # noqa: PLC0415 -- optional google_adk extra
            RunConfig,
        )

        message_ids = ids_of(messages)
        logger.debug("Handling messages %s in room %s", message_ids, room_id)

        # Initialize or seed per-room history
        if is_session_bootstrap:
            self._room_history[room_id] = list(history) if history else []
            if history:
                logger.info(
                    "Room %s: Loaded %s historical messages",
                    room_id,
                    len(history),
                )
        elif room_id not in self._room_history:
            # Safety: ensure history exists even if not first message
            self._room_history[room_id] = []

        prompt = self._build_turn_prompt(
            self._room_history[room_id], messages, participants_msg, contacts_msg
        )
        self._remember_inputs(room_id, messages)
        if prompt.retained_transcript_chars < prompt.original_transcript_chars:
            logger.warning(
                "Room %s: Transcript truncated from %d to %d chars "
                "to stay within token budget",
                room_id,
                prompt.original_transcript_chars,
                prompt.retained_transcript_chars,
            )
        if participants_msg:
            logger.info("Room %s: Participants updated", room_id)
        if contacts_msg:
            logger.info("Room %s: Contacts broadcast received", room_id)

        # Per-turn usage, summed across the event stream below. Initialized
        # outside the try so the finally can emit whatever accumulated.
        turn_usage = TurnUsage()
        tool_sequence: list[list[str]] = []
        # None until the try's construction succeeds, so the finally's close()
        # has nothing to do if runner construction itself is what failed.
        runner: InMemoryRunner | None = None
        try:
            # A fresh runner is created per message because InMemoryRunner
            # accumulates session history internally and tool schemas may change
            # between calls.  History is injected as a text transcript instead.
            runner = self._create_runner(tools)

            # Always create a new session ID — each runner is fresh, so there
            # is no in-memory state to resume.  The ID is stored for cleanup
            # tracking.  The session must be pre-created in the runner's
            # InMemorySessionService before calling run_async, which expects
            # the session to already exist.
            session_id = str(uuid.uuid4())
            self._room_sessions[room_id] = session_id
            await runner.session_service.create_session(
                app_name=_APP_NAME,
                user_id=room_id,
                session_id=session_id,
            )
            logger.debug("Room %s: Created new ADK session %s", room_id, session_id)

            user_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt.text)],
            )

            # Run the ADK agent - it handles the full tool loop. Usage is
            # reported per model response on the event stream, so sum across
            # the loop into one per-turn TurnUsage.
            final_response_text = ""
            model_failure: ProviderRunError | None = None
            # ADK refuses the model call past max_llm_calls. A landed decline
            # answers the next request in before_model_callback, which runs
            # before ADK counts the call, so a decline never uses up the cap.
            async for event in runner.run_async(
                user_id=room_id,
                session_id=session_id,
                new_message=user_content,
                run_config=RunConfig(max_llm_calls=self.config.max_tool_rounds),
            ):
                if function_calls := event.get_function_calls():
                    tool_sequence.append([call.name or "" for call in function_calls])
                if Emit.USAGE in self.features.emit:
                    turn_usage = turn_usage + self._usage_from_event(event)

                # ADK returns a model failure as an event. Its flow ends the
                # run after one, so drain rather than break: breaking leaves
                # ADK's nested generators open inside their tracing spans.
                # ADK <= 1.10 reports an empty normal finish as "STOP".
                # ADK 2.x marks the same case MODEL_RETURNED_NO_CONTENT via
                # apply_empty_response_policy; keep both as missing-reply, not
                # a hard provider failure.
                if event.error_code and event.error_code not in {
                    types.FinishReason.STOP,
                    "STOP",
                    "MODEL_RETURNED_NO_CONTENT",
                }:
                    model_failure = ProviderRunError(
                        event.error_code, event.error_message
                    )

                # Report tool calls/results if enabled
                if Emit.TOOL_CALLS in self.features.emit:
                    try:
                        await self._report_event(event, tools)
                    except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                        logger.warning("Failed to report event: %s", e)

                if event.is_final_response():
                    # Extract text from the final response for history tracking
                    final_response_text = self._extract_event_text(event)
                    logger.debug(
                        "Room %s: ADK agent completed with final response",
                        room_id,
                    )
            if model_failure is not None:
                raise model_failure
        except LlmCallsLimitExceededError as exc:
            error = ToolRoundsExceededError(
                self.config.max_tool_rounds, room_id, tool_sequence
            )
            await tools.send_failure(AgentFailure(_PROVIDER, str(error)))
            raise error from exc
        except Exception as e:
            logger.exception("Error running ADK agent in room %s", room_id)
            await tools.send_failure(generic_provider_failure(_PROVIDER, e))
            raise
        finally:
            # Emit before close so a close() failure can't drop the usage, but
            # nested so close() runs even if a cancellation interrupts the emit.
            try:
                # No-op unless Emit.USAGE is on; best-effort, never raises.
                await self.emit_usage(tools, turn_usage)
            finally:
                if runner is not None:
                    try:
                        await runner.close()
                    finally:
                        await cast(Any, runner.agent).model.aclose()

        # Failed runs keep their inputs, but not unfinished model output.
        if final_response_text:
            self._room_history[room_id].append(
                {"role": "model", "content": final_response_text}
            )

        self._trim_history(room_id)

        logger.debug("Messages %s processed successfully", message_ids)

    def _build_turn_prompt(
        self,
        history: GoogleADKMessages,
        messages: Sequence[PlatformMessage],
        participants_msg: str | None,
        contacts_msg: str | None,
    ) -> TurnPrompt:
        """Render bounded context and current inputs without changing history."""
        current_ids = set(ids_of(messages))
        context = [entry for entry in history if entry.get("id") not in current_ids]
        transcript = self._format_history_transcript(
            context[-self.config.max_history_messages :]
        )
        original_transcript_chars = len(transcript)
        parts: list[str] = []
        if transcript:
            if len(transcript) > self.config.max_transcript_chars:
                transcript = transcript[-self.config.max_transcript_chars :]
                # Drop the partial first line when the character limit cuts one.
                newline = transcript.find("\n")
                if newline != -1:
                    transcript = transcript[newline + 1 :]
            parts.append(
                f"{HISTORY_CONTEXT_START}\n{transcript}\n{HISTORY_CONTEXT_END}\n\n"
            )
        if participants_msg:
            parts.append(f"{SYSTEM_UPDATE_PREFIX}{participants_msg}")
        if contacts_msg:
            parts.append(f"{SYSTEM_UPDATE_PREFIX}{contacts_msg}")
        parts.append(format_current_inputs(messages))
        return TurnPrompt(
            text="\n".join(parts),
            original_transcript_chars=original_transcript_chars,
            retained_transcript_chars=len(transcript),
        )

    def _remember_inputs(
        self, room_id: str, messages: Sequence[PlatformMessage]
    ) -> None:
        """Retain each inbound message once within the room's history window."""
        room_history = self._room_history[room_id]
        remembered_ids = {entry.get("id") for entry in room_history}
        for message in messages:
            if message.id not in remembered_ids:
                room_history.append(
                    {
                        "id": message.id,
                        "role": "user",
                        "content": message.format_for_llm(),
                    }
                )
                remembered_ids.add(message.id)
        self._trim_history(room_id)

    def _trim_history(self, room_id: str) -> None:
        """Bound retained inputs even when provider execution fails."""
        # Keep twice the window so the next turn still has a full page.
        if len(self._room_history[room_id]) > self.config.max_history_messages * 2:
            self._room_history[room_id] = self._room_history[room_id][
                -self.config.max_history_messages :
            ]

    @staticmethod
    def _usage_from_event(event: Any) -> TurnUsage:
        """Map an ADK event's ``usage_metadata`` onto TurnUsage.

        Usage rides model-response events; events without it (tool calls, etc.)
        contribute empty usage. Gemini has no cache-write dimension (left 0).

        Gemini reports thinking tokens *disjointly* from output (its own
        ``total_token_count`` is ``prompt + candidates + thoughts``, so
        ``candidates_token_count`` excludes thoughts), so fold
        ``thoughts_token_count`` into ``output_tokens`` — otherwise thinking-model
        turns undercount, and this stays consistent with providers that already
        count reasoning inside output.
        """
        return TurnUsage.from_object(
            getattr(event, "usage_metadata", None),
            input="prompt_token_count",
            output="candidates_token_count",
            reasoning="thoughts_token_count",
            cache_read="cached_content_token_count",
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up session and history when agent leaves a room."""
        self._room_history.pop(room_id, None)
        removed = self._room_sessions.pop(room_id, None)
        if removed is not None:
            logger.debug("Room %s: Cleaned up ADK session", room_id)

    def _format_history_transcript(self, history: GoogleADKMessages) -> str:
        """Render converted history as a labeled text transcript.

        Delegates to the converter so the own-agent name used for labeling
        comes from a single source of truth (the converter, which already
        owns own-vs-peer attribution during ``convert()``).  The converter
        also owns the tool-call/tool-result preview format.

        ``SimpleAdapter`` types ``self.history_converter`` as the generic
        ``HistoryConverter | None``; the ADK constructor always installs a
        concrete ``GoogleADKHistoryConverter`` (defaulting to a fresh one
        if the caller omits it), so the narrowing here is a no-op at
        runtime that lets the type checker see ``format_transcript``.
        """
        converter = cast(GoogleADKHistoryConverter, self.history_converter)
        return converter.format_transcript(history)

    @staticmethod
    def _extract_event_text(event: Any) -> str:
        """Extract text content from an ADK event for history tracking."""
        if not hasattr(event, "content") or not event.content:
            return ""
        parts = getattr(event.content, "parts", None)
        if not parts:
            return ""
        texts: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
        return " ".join(texts)

    async def _report_event(self, event: Any, tools: AgentToolsProtocol) -> None:
        """Report ADK event as tool_call/tool_result if applicable."""
        if not hasattr(event, "get_function_calls") or not hasattr(
            event, "get_function_responses"
        ):
            logger.debug(
                "Skipping event without function call/response methods: %s",
                type(event).__name__,
            )
            return

        function_calls = event.get_function_calls()
        if function_calls:
            for fc in function_calls:
                try:
                    tool_name = getattr(fc, "name", "unknown")
                    try:
                        args = dict(fc.args) if fc.args else {}
                    except (TypeError, ValueError):
                        args = {"raw": str(fc.args)} if fc.args else {}
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.ARGS: redact_tool_call_args(
                                    tool_name, args
                                ),
                                ToolEventKey.TOOL_CALL_ID: getattr(fc, "id", ""),
                            }
                        ),
                        message_type="tool_call",
                    )
                except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                    logger.warning("Failed to send tool_call event: %s", e)

        function_responses = event.get_function_responses()
        if function_responses:
            for fr in function_responses:
                try:
                    tool_name = getattr(fr, "name", "unknown")
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.IS_ERROR: is_failed_tool_output(
                                    getattr(fr, "response", None)
                                ),
                                ToolEventKey.OUTPUT: _redacted_function_response_output(
                                    tool_name, getattr(fr, "response", None)
                                ),
                                ToolEventKey.TOOL_CALL_ID: getattr(fr, "id", ""),
                            }
                        ),
                        message_type="tool_result",
                    )
                except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                    logger.warning("Failed to send tool_result event: %s", e)
