"""
Google ADK adapter using SimpleAdapter pattern.

Integrates with the Google Agent Development Kit (ADK) to run Gemini-powered
agents on the Thenvoi platform. Uses ADK's built-in Runner for tool loop
management and session handling.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid
import warnings
from typing import ClassVar, TYPE_CHECKING, Any

from pydantic import ValidationError

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.google_adk import GoogleADKHistoryConverter, GoogleADKMessages
from thenvoi.runtime.custom_tools import (
    CustomToolDef,
    custom_tools_to_schemas,
    execute_custom_tool,
    find_custom_tool,
)
from thenvoi.runtime.prompts import render_system_prompt

if TYPE_CHECKING:
    from google.adk.runners import InMemoryRunner
    from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_APP_NAME = "thenvoi"
_MAX_TOOL_OUTPUT_PREVIEW = 200
_DEFAULT_MAX_HISTORY_MESSAGES = 50
_DEFAULT_MAX_TRANSCRIPT_CHARS = 100_000

# Candidate method names that google-adk BaseTool may use to expose tool
# declarations.  The bridge overrides every match found on the installed
# version so it keeps working if ADK renames the internal API.
_DECLARATION_CANDIDATES: tuple[str, ...] = (
    "_get_declaration",  # google-adk 1.x (current internal API)
    "get_declaration",  # likely public rename candidate
)


@functools.lru_cache(maxsize=1)
def _require_adk() -> tuple[type, type, type, Any]:
    """Import Google ADK dependencies lazily.

    Cached after the first successful call so repeated access is free.
    Only triggered when ``GoogleADKAdapter`` is instantiated, not at
    module import time.

    Returns:
        (ADKAgent, InMemoryRunner, BaseTool, types) tuple.

    Raises:
        ImportError: If google-adk is not installed.
    """
    try:
        from google.adk import Agent as ADKAgent
        from google.adk.runners import InMemoryRunner
        from google.adk.tools import BaseTool
        from google.genai import types
    except ImportError as exc:
        raise ImportError(
            "google-adk is required for GoogleADKAdapter. "
            "Install with: uv add thenvoi-sdk[google_adk]"
        ) from exc
    return ADKAgent, InMemoryRunner, BaseTool, types


def _strip_additional_properties(
    openai_params: dict[str, Any] | list[Any] | Any,
) -> Any:
    """Convert OpenAI JSON Schema parameters to Gemini format.

    Gemini does not support the ``additionalProperties`` key in function
    parameter schemas.  Passing it causes ``google.genai`` to reject the
    declaration with a validation error.  This helper strips the key
    recursively so the schema is compatible.
    """
    if isinstance(openai_params, list):
        return [
            _strip_additional_properties(item)
            if isinstance(item, (dict, list))
            else item
            for item in openai_params
        ]
    if not isinstance(openai_params, dict):
        return openai_params

    cleaned: dict[str, Any] = {}
    for key, value in openai_params.items():
        if key == "additionalProperties":
            continue
        if isinstance(value, (dict, list)):
            cleaned[key] = _strip_additional_properties(value)
        else:
            cleaned[key] = value
    return cleaned


@functools.lru_cache(maxsize=1)
def _get_tool_bridge_class() -> type:
    """Build the ``_ThenvoiToolBridge`` class lazily.

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
    _, _, BaseTool, types = _require_adk()

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
            "(pinned to google-adk >=1.0,<2). Your installed version "
            "may be incompatible."
        )

    logger.debug(
        "google-adk BaseTool declaration method(s) detected: %s",
        ", ".join(active_methods),
    )

    class _ThenvoiToolBridge(BaseTool):
        """Bridges a Thenvoi platform tool to Google ADK.

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
        ):
            super().__init__(name=tool_name, description=tool_description)
            self._parameters_schema = parameters_schema
            self._tools = tools
            self._custom_tools = custom_tools

            # Eagerly build and cache the declaration so schema errors
            # surface at construction time rather than mid-conversation.
            try:
                self._cached_declaration = types.FunctionDeclaration(
                    name=tool_name,
                    description=tool_description,
                    parameters=_strip_additional_properties(parameters_schema),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to build FunctionDeclaration for tool '{tool_name}'. "
                    "This may indicate an incompatible google-adk version — "
                    "the adapter relies on BaseTool's declaration mechanism "
                    "pinned to google-adk >=1.0,<2."
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
            """Execute the tool via Thenvoi's AgentToolsProtocol."""
            try:
                custom_tool = find_custom_tool(self._custom_tools, self.name)
                if custom_tool:
                    result = await execute_custom_tool(custom_tool, args)
                else:
                    result = await self._tools.execute_tool_call(self.name, args)

                if not isinstance(result, str):
                    return json.dumps(result, default=str)
                return result
            except ValidationError as e:
                errors = "; ".join(
                    f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                msg = f"Invalid arguments for {self.name}: {errors}"
                logger.error("Tool %s validation failed: %s", self.name, msg)
                return msg
            except ValueError as e:
                logger.error("Invalid arguments for %s: %s", self.name, e)
                return str(e)
            except Exception as e:
                logger.exception("Tool %s failed", self.name)
                return f"Error executing {self.name}: {e}"

    # Override every detected declaration method so the bridge works
    # even if ADK renames the internal API in a future minor release.
    for method_name in active_methods:
        setattr(
            _ThenvoiToolBridge,
            method_name,
            _ThenvoiToolBridge._build_declaration,
        )

    # Smoke-test: verify the declaration mechanism works end-to-end.
    # Catches signature changes that hasattr alone would miss.
    try:
        _probe = _ThenvoiToolBridge(
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
            ">=1.0,<2 — your installed version may be incompatible."
        ) from exc

    return _ThenvoiToolBridge


class GoogleADKAdapter(SimpleAdapter[GoogleADKMessages]):
    """
    Google ADK adapter using SimpleAdapter pattern.

    Uses Google's Agent Development Kit with Gemini models for agent
    interactions, with automatic tool bridging and session management.

    Tool bridges are created per ``on_message`` call with direct references
    to the current ``AgentToolsProtocol`` and custom tools, so each
    invocation is self-contained and safe for concurrent use.

    Example:
        adapter = GoogleADKAdapter(
            model="gemini-2.5-flash",
            custom_section="You are a helpful assistant.",
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        system_prompt: str | None = None,
        custom_section: str | None = None,
        enable_execution_reporting: bool = False,
        enable_memory_tools: bool = False,
        history_converter: GoogleADKHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        max_history_messages: int = _DEFAULT_MAX_HISTORY_MESSAGES,
        max_transcript_chars: int = _DEFAULT_MAX_TRANSCRIPT_CHARS,
        features: AdapterFeatures | None = None,
    ):
        # Validate google-adk is installed early (cached, so cheap on repeat).
        _require_adk()

        # --- Deprecation shim: boolean → features migration ---
        _has_legacy_booleans = enable_execution_reporting or enable_memory_tools
        if _has_legacy_booleans and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both legacy boolean flags "
                "(enable_execution_reporting / enable_memory_tools) and 'features'. "
                "Use features=AdapterFeatures(...) instead."
            )

        if _has_legacy_booleans:
            warnings.warn(
                "enable_execution_reporting and enable_memory_tools are deprecated. "
                "Use features=AdapterFeatures(emit={Emit.EXECUTION}, "
                "capabilities={Capability.MEMORY}) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            features = AdapterFeatures(
                emit=frozenset({Emit.EXECUTION})
                if enable_execution_reporting
                else frozenset(),
                capabilities=frozenset({Capability.MEMORY})
                if enable_memory_tools
                else frozenset(),
            )

        super().__init__(
            history_converter=history_converter or GoogleADKHistoryConverter(),
            features=features,
        )

        self.model = model
        self._system_prompt_override = system_prompt
        self.custom_section = custom_section
        self.max_history_messages = max_history_messages
        self.max_transcript_chars = max_transcript_chars

        # Custom tools (user-provided)
        self._custom_tools: list[CustomToolDef] = additional_tools or []

        # Effective system prompt (rendered in on_started)
        self._system_prompt: str = ""

        # Per-room accumulated message history for transcript injection.
        # A fresh InMemoryRunner is created per message, so continuity comes
        # from injecting the accumulated transcript, not from runner state.
        # Thread-safety: the runtime's ExecutionContext guarantees that
        # on_message is called sequentially per room (single asyncio.Task per
        # room with an asyncio.Queue), so no lock is needed here.
        self._room_history: dict[str, GoogleADKMessages] = {}

        # Per-room session IDs for logging/debugging.
        self._room_sessions: dict[str, str] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt and create ADK agent after metadata is fetched.

        Prompt precedence (matches Anthropic/Gemini):
          1. If ``system_prompt`` was provided at construction time, it wins —
             ``custom_section`` and ``features``-based capability sections are
             ignored. The override is passed through verbatim.
          2. Otherwise, ``render_system_prompt`` renders the SDK base prompt
             plus ``custom_section``, with capability sections gated on
             ``features.capabilities``.
        """
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self._system_prompt_override or render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.custom_section or "",
            features=self.features,
        )

        logger.info("Google ADK adapter started for agent: %s", agent_name)

    def _build_adk_tools(self, tools: AgentToolsProtocol) -> list[Any]:
        """Build ADK tool bridges from Thenvoi tool schemas."""
        ToolBridge = _get_tool_bridge_class()
        openai_schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
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
                    )
                )

        return adk_tools

    def _create_runner(self, tools: AgentToolsProtocol) -> InMemoryRunner:
        """Create a fresh ADK InMemoryRunner with current tools."""
        ADKAgent, InMemoryRunnerCls, _, _ = _require_adk()
        adk_tools = self._build_adk_tools(tools)

        adk_agent = ADKAgent(
            name=self.agent_name or "thenvoi_agent",
            model=self.model,
            instruction=self._system_prompt,
            tools=adk_tools,
        )

        return InMemoryRunnerCls(
            agent=adk_agent,
            app_name=_APP_NAME,
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: GoogleADKMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle incoming message via Google ADK.

        Uses ADK's Runner for the full tool loop. The runner handles
        LLM calls, tool execution, and conversation management.
        """
        _, _, _, types = _require_adk()

        logger.debug("Handling message %s in room %s", msg.id, room_id)

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

        # A fresh runner is created per message because InMemoryRunner
        # accumulates session history internally and tool schemas may change
        # between calls.  History is injected as a text transcript instead.
        runner = self._create_runner(tools)
        try:
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

            # Build the user message content
            parts: list[str] = []

            # Inject recent accumulated history as transcript for context.
            # Apply sliding window to avoid unbounded transcript growth.
            room_history = self._room_history[room_id]
            if room_history:
                windowed = room_history[-self.max_history_messages :]
                transcript = self._format_history_transcript(windowed)
                if transcript:
                    if len(transcript) > self.max_transcript_chars:
                        original_len = len(transcript)
                        transcript = transcript[-self.max_transcript_chars :]
                        # Cut to the next newline to avoid a partial first line
                        nl = transcript.find("\n")
                        if nl != -1:
                            transcript = transcript[nl + 1 :]
                        logger.warning(
                            "Room %s: Transcript truncated from %d to %d chars "
                            "to stay within token budget",
                            room_id,
                            original_len,
                            len(transcript),
                        )
                    parts.append(
                        f"[Previous conversation context]\n{transcript}\n"
                        f"[End of previous context]\n\n"
                    )

            # Inject participants update
            if participants_msg:
                parts.append(f"[System]: {participants_msg}")
                logger.info("Room %s: Participants updated", room_id)

            # Inject contacts update
            if contacts_msg:
                parts.append(f"[System]: {contacts_msg}")
                logger.info("Room %s: Contacts broadcast received", room_id)

            # Add the actual message
            parts.append(msg.format_for_llm())

            user_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text="\n".join(parts))],
            )

            logger.info(
                "Room %s: Running ADK agent (bootstrap=%s, history_size=%s)",
                room_id,
                is_session_bootstrap,
                len(room_history),
            )

            # Run the ADK agent - it handles the full tool loop
            final_response_text = ""
            async for event in runner.run_async(
                user_id=room_id,
                session_id=session_id,
                new_message=user_content,
            ):
                # Report tool calls/results if enabled
                if Emit.EXECUTION in self.features.emit:
                    try:
                        await self._report_event(event, tools)
                    except Exception as e:
                        logger.warning("Failed to report event: %s", e)

                if event.is_final_response():
                    # Extract text from the final response for history tracking
                    final_response_text = self._extract_event_text(event)
                    logger.debug(
                        "Room %s: ADK agent completed with final response",
                        room_id,
                    )
        except Exception as e:
            logger.exception("Error running ADK agent in room %s", room_id)
            await self._report_error(tools, str(e))
            raise
        finally:
            await runner.close()

        # Accumulate message history for future transcript injection
        self._room_history[room_id].append(
            {"role": "user", "content": msg.format_for_llm()}
        )
        if final_response_text:
            self._room_history[room_id].append(
                {"role": "model", "content": final_response_text}
            )

        # Trim accumulated history to prevent unbounded memory growth.
        # Keep twice the window so the sliding window in the next call
        # still has a full page of messages to work with.
        trim_threshold = self.max_history_messages * 2
        if len(self._room_history[room_id]) > trim_threshold:
            self._room_history[room_id] = self._room_history[room_id][
                -self.max_history_messages :
            ]

        logger.debug("Message %s processed successfully", msg.id)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up session and history when agent leaves a room."""
        self._room_history.pop(room_id, None)
        removed = self._room_sessions.pop(room_id, None)
        if removed is not None:
            logger.debug("Room %s: Cleaned up ADK session", room_id)

    def _format_history_transcript(self, history: GoogleADKMessages) -> str:
        """Format converted history as a text transcript for context injection."""
        lines: list[str] = []
        for msg in history:
            content = msg.get("content", "")
            if isinstance(content, str):
                lines.append(content)
            elif isinstance(content, list):
                # Tool call/result blocks - summarize
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "function_call":
                            lines.append(
                                f"[Tool Call] {block.get('name', 'unknown')}"
                                f" ({json.dumps(block.get('args', {}), default=str)})"
                            )
                        elif block_type == "function_response":
                            output = str(block.get("output", ""))
                            truncated = (
                                output[:_MAX_TOOL_OUTPUT_PREVIEW] + "..."
                                if len(output) > _MAX_TOOL_OUTPUT_PREVIEW
                                else output
                            )
                            lines.append(
                                f"[Tool Result] {block.get('name', 'unknown')}: "
                                f"{truncated}"
                            )
        return "\n".join(lines)

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
                    try:
                        args = dict(fc.args) if fc.args else {}
                    except (TypeError, ValueError):
                        args = {"raw": str(fc.args)} if fc.args else {}
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": getattr(fc, "name", "unknown"),
                                "args": args,
                                "tool_call_id": getattr(fc, "id", ""),
                            }
                        ),
                        message_type="tool_call",
                    )
                except Exception as e:
                    logger.warning("Failed to send tool_call event: %s", e)

        function_responses = event.get_function_responses()
        if function_responses:
            for fr in function_responses:
                try:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": getattr(fr, "name", "unknown"),
                                "output": str(fr.response)
                                if getattr(fr, "response", None)
                                else "",
                                "tool_call_id": getattr(fr, "id", ""),
                            }
                        ),
                        message_type="tool_result",
                    )
                except Exception as e:
                    logger.warning("Failed to send tool_result event: %s", e)

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception as e:
            logger.warning("Failed to send error event: %s", e)
