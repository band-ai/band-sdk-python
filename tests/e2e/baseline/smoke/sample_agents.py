"""Driving instructions + matrix glue for the smoke tests.

Adapter construction and discovery live in the toolkit registry
(``toolkit.adapters``); this module is the pytest-facing glue over it.
``build_agent`` builds a registered adapter under the shared role-setting prompt;
the matrix glue (``adapter_params`` / ``across_adapters`` / ``with_agents``) lives in
``tests.e2e.baseline.agents``. Adding a framework is a single ``@adapter`` entry in
the registry -- nothing here changes.

Following ``sample_tools``/``test_tool_calls``, the agent gets a fixed
role-setting system prompt and the *user message* carries the instruction (with
the unique marker). Each instruction forces exactly one observable action --
``band_send_event`` for an event, ``band_store_memory`` for a memory, the only
way to produce it -- so a precise instruction is the only way to comply.
"""

from __future__ import annotations

import uuid


from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, MessageType
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    WorkingLongTermMemoryType,
)

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import build_adapter
from tests.e2e.baseline.toolkit.observations import MemoryTool

# Fixed role-setter: the actionable instruction (and marker) travels in the user
# message, exactly like the opaque-tool smokes.
TOOL_AGENT_SYSTEM_PROMPT = (
    "You are under test. When the user messages you, do exactly what they ask: "
    "make the requested tool call(s) with the given arguments and nothing else. "
    "Do not send a chat message unless explicitly asked."
)


def build_agent(
    adapter_id: str,
    settings: BaselineSettings,
    *,
    features: AdapterFeatures | None = None,
) -> SimpleAdapter:
    """Build the registered adapter under the shared role-setting prompt.

    Delegates to the toolkit registry (the single place that knows how to
    construct each framework). ``features`` flips capabilities such as memory on;
    the steering prompt is the fixed ``TOOL_AGENT_SYSTEM_PROMPT``.
    """
    return build_adapter(
        adapter_id, settings, prompt=TOOL_AGENT_SYSTEM_PROMPT, features=features
    )


def memory_features() -> AdapterFeatures:
    """Features for the memory smokes: expose the memory tools, and record the
    tool call as a ``tool_call`` event so the call layer is observable."""
    return AdapterFeatures(capabilities={Capability.MEMORY}, emit={Emit.EXECUTION})


# Reusable agent shapes for ``@with_agents(..., **SHAPE)``: the prompt (and
# features) a smoke runs its agents under. Declared once here so every test shares
# the same shape instead of re-spelling it.
TOOL_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT}
MEMORY_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": memory_features()}


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
