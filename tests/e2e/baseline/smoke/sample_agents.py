"""Cheap agents and driving instructions for the matrix smokes.

``ADAPTER_BUILDERS`` maps an adapter id to its dependency gate and a builder;
``adapter_params`` renders it as ``pytest.param``s with the right ``@requires``
gate, so a matrix test reads ``parametrize("adapter_id", adapter_params())``.
Only standard tool-loop adapters are here (Anthropic, LangGraph) -- both route
tool calls through ``execute_tool_call``, so ``band_send_event`` posts a real
event and ``band_store_memory`` writes a real memory. Adding a framework is a
single ``ADAPTER_BUILDERS`` entry plus a ``Dep``.

Following ``sample_tools``/``test_tool_calls``, the agent gets a fixed
role-setting system prompt and the *user message* carries the instruction (with
the unique marker). Each instruction forces exactly one observable action --
``band_send_event`` for an event, ``band_store_memory`` for a memory, the only
way to produce it -- so a precise instruction is the only way to comply.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection

import pytest

from band.adapters.anthropic import AnthropicAdapter
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, MessageType
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    WorkingLongTermMemoryType,
)

from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.observations import MemoryTool

# Fixed role-setter: the actionable instruction (and marker) travels in the user
# message, exactly like the opaque-tool smokes.
TOOL_AGENT_SYSTEM_PROMPT = (
    "You are under test. When the user messages you, do exactly what they ask: "
    "make the requested tool call(s) with the given arguments and nothing else. "
    "Do not send a chat message unless explicitly asked."
)

AgentBuilder = Callable[..., SimpleAdapter]


def build_anthropic_agent(
    settings: BaselineSettings, *, features: AdapterFeatures | None = None
) -> AnthropicAdapter:
    """An Anthropic agent under the shared role-setting system prompt."""
    return AnthropicAdapter(
        model=settings.llm_models.anthropic_model,
        provider_key=settings.llm_credentials.anthropic_api_key,
        prompt=TOOL_AGENT_SYSTEM_PROMPT,
        features=features,
    )


def build_langgraph_agent(
    settings: BaselineSettings, *, features: AdapterFeatures | None = None
) -> SimpleAdapter:
    """A LangGraph (OpenAI-backed) agent under the shared role-setting prompt."""
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(
            model=settings.llm_models.openai_model,
            api_key=settings.llm_credentials.openai_api_key,
        ),
        checkpointer=MemorySaver(),
        custom_section=TOOL_AGENT_SYSTEM_PROMPT,
        features=features,
    )


# TODO: this is temporary until we create generic builders in the next PR
ADAPTER_BUILDERS: dict[str, tuple[Dep, AgentBuilder]] = {
    "anthropic": (Dep.ANTHROPIC, build_anthropic_agent),
    "langgraph": (Dep.OPENAI, build_langgraph_agent),
}


def build_agent(
    adapter_id: str,
    settings: BaselineSettings,
    *,
    features: AdapterFeatures | None = None,
) -> SimpleAdapter:
    """Build the agent registered under ``adapter_id``."""
    _, builder = ADAPTER_BUILDERS[adapter_id]
    return builder(settings, features=features)


def adapter_params(include: Collection[str] | None = None) -> list[pytest.param]:
    """One ``pytest.param`` per adapter, each gated by its provider key.

    The ``requires(...)`` mark is resolved per-parameter by the conftest gate
    hook (a missing key fails the cell). Pass ``include`` to restrict the matrix
    to specific adapter ids -- e.g. the event matrix runs Anthropic-only because
    ``gpt-5.4-mini`` is unreliable at driving ``band_send_event`` for
    thought/error, so LangGraph event cells flake.
    """
    return [
        pytest.param(adapter_id, marks=requires(dep), id=adapter_id)
        for adapter_id, (dep, _) in ADAPTER_BUILDERS.items()
        if include is None or adapter_id in include
    ]


def memory_features() -> AdapterFeatures:
    """Features for the memory smokes: expose the memory tools, and record the
    tool call as a ``tool_call`` event so the call layer is observable."""
    return AdapterFeatures(capabilities={Capability.MEMORY}, emit={Emit.EXECUTION})


def unique_marker(prefix: str) -> str:
    """A high-entropy token to assert verbatim in event/memory content."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def emit_event_instruction(event_type: MessageType, marker: str) -> str:
    """User message forcing exactly one ``band_send_event`` of ``event_type``
    whose content carries ``marker`` verbatim."""
    return (
        f"Call the tool band_send_event exactly once with "
        f"message_type='{event_type.value}' and content that includes the exact "
        f"token {marker} (verbatim). That tool call is your ONLY action -- do not "
        "reply with a chat message and do not call any other tool. A plain-text "
        "reply does not satisfy this; you must call band_send_event."
    )


def emit_thoughts_instruction(markers: list[str]) -> str:
    """User message forcing one ``band_send_event`` thought per marker (used to
    demonstrate a count-floor assertion)."""
    tokens = ", ".join(markers)
    return (
        f"Call the tool band_send_event once for each of these tokens: {tokens}. "
        f"Each call uses message_type='{MessageType.THOUGHT.value}' with content "
        "containing that exact token verbatim. Those tool calls are your ONLY "
        "action -- do not reply with a chat message and do not call any other "
        "tool. A plain-text reply does not satisfy this."
    )


def store_memory_instruction(marker: str) -> str:
    """User message forcing one organization-scoped ``band_store_memory`` whose
    content carries ``marker`` verbatim, with an exact valid system/type combo."""
    return (
        "Call band_store_memory exactly once with these exact arguments: "
        f"content = a short sentence that includes the exact token {marker}; "
        f"system = {MemorySystem.LONG_TERM.value}; "
        f"type = {WorkingLongTermMemoryType.SEMANTIC.value}; "
        f"segment = {MemorySegment.USER.value}; "
        f"scope = {MemoryStoreScope.ORGANIZATION.value}; "
        "thought = a brief reason. Do not include subject_id. Do not call any "
        "other tool."
    )


def store_subject_memory_instruction(marker: str, subject_id: str) -> str:
    """User message forcing one subject-scoped ``band_store_memory`` about
    ``subject_id`` whose content carries ``marker`` verbatim."""
    return (
        "Call band_store_memory exactly once with these exact arguments: "
        f"content = a short sentence that includes the exact token {marker}; "
        f"system = {MemorySystem.LONG_TERM.value}; "
        f"type = {WorkingLongTermMemoryType.SEMANTIC.value}; "
        f"segment = {MemorySegment.AGENT.value}; "
        f"scope = {MemoryStoreScope.SUBJECT.value}; "
        f"subject_id = {subject_id}; "
        "thought = a brief reason. Do not call any other tool."
    )


def supersede_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-supersede lifecycle in one turn: store an
    org-scoped memory carrying ``marker``, then supersede it by the id the store
    call returns."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={WorkingLongTermMemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.ORGANIZATION.value}, and a brief thought. "
        f"Then call {MemoryTool.SUPERSEDE.value} with memory_id set to the id "
        "returned by the store call. Do not call any other tool."
    )


def archive_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-archive lifecycle in one turn: store an
    org-scoped memory carrying ``marker``, then archive it by the id the store
    call returns."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={WorkingLongTermMemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.ORGANIZATION.value}, and a brief thought. "
        f"Then call {MemoryTool.ARCHIVE.value} with memory_id set to the id "
        "returned by the store call. Do not call any other tool."
    )


def recall_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-recall flow in one turn: store an
    org-scoped memory carrying ``marker``, then look it back up with the list and
    get tools (exercises the read-side memory tools)."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={WorkingLongTermMemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.ORGANIZATION.value}, and a brief thought. "
        f"Then call {MemoryTool.LIST.value} with content_query={marker} to find "
        f"it. Then call {MemoryTool.GET.value} with memory_id set to the id of a "
        "memory the list returned. Do not call any other tool."
    )


def store_two_memories_instruction(marker: str) -> str:
    """User message forcing two org-scoped stores that both carry ``marker`` but
    differ in system/type, so one ``content_query=marker`` read returns both and
    the store-layer view can be sliced by dimension."""
    return (
        f"Call {MemoryTool.STORE.value} twice, both with content including the "
        f"exact token {marker} and a brief thought, both "
        f"segment={MemorySegment.USER.value} "
        f"scope={MemoryStoreScope.ORGANIZATION.value}. First store: "
        f"system={MemorySystem.LONG_TERM.value}, "
        f"type={WorkingLongTermMemoryType.SEMANTIC.value}. Second store: "
        f"system={MemorySystem.WORKING.value}, "
        f"type={WorkingLongTermMemoryType.EPISODIC.value}. "
        "Do not call any other tool."
    )
