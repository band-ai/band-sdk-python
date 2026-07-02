"""Driving instructions + matrix glue for the smoke tests.

Adapter construction and discovery live in the toolkit registry
(``toolkit.adapters``); this module is the pytest-facing glue over it: the shared
role-setting prompt, ``memory_features()``, and the reusable agent *shapes*
(``TOOL_AGENT`` / ``MEMORY_AGENT``) passed as ``@per_adapter(..., **SHAPE)`` /
``@with_adapters(..., **SHAPE)``. The decorators themselves (``per_adapter`` /
``with_adapters`` / ``adapter_params``) live in ``tests.e2e.baseline.agents``. Adding a
framework is a single ``@adapter`` entry in the registry -- nothing here changes.

Following ``sample_tools``/``test_tool_calls``, the agent gets a fixed
role-setting system prompt and the *user message* carries the instruction (with
the unique marker). Each instruction forces exactly one observable action --
``band_send_event`` for an event, ``band_store_memory`` for a memory, the only
way to produce it -- so a precise instruction is the only way to comply.
"""

from __future__ import annotations

import uuid


from band.core.types import AdapterFeatures, Capability, Emit, MessageType
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    WorkingLongTermMemoryType,
)

from tests.e2e.baseline.toolkit.observations import MemoryTool

# Fixed role-setter: the actionable instruction (and marker) travels in the user
# message, exactly like the opaque-tool smokes.
TOOL_AGENT_SYSTEM_PROMPT = (
    "You are under test. When the user messages you, do exactly what they ask: "
    "make the requested tool call(s) with the given arguments and nothing else. "
    "Do not send a chat message unless explicitly asked."
)


def memory_features() -> AdapterFeatures:
    """Features for the memory smokes: expose the memory tools, and record the
    tool call as a ``tool_call`` event so the call layer is observable."""
    return AdapterFeatures(capabilities={Capability.MEMORY}, emit={Emit.EXECUTION})


def usage_features() -> AdapterFeatures:
    """Features for the cost/token smokes: emit each turn's token usage as a
    ``usage`` event so the ``Usage`` observation layer is populated."""
    return AdapterFeatures(emit={Emit.USAGE})


# A plain reply-eliciting prompt for the cost smokes: the turn just needs to run
# an LLM call (input tokens) and produce a reply (output tokens); no tools.
COST_AGENT_SYSTEM_PROMPT = (
    "You are a helpful assistant in a chat room. Reply directly to the user with "
    "one short, friendly sentence."
)


# A cost-smoke prompt for the multi-turn non-cumulative check: it must let the
# *user* dictate reply length so the test can drive one LONG turn then one TINY
# turn. That asymmetry is what makes the check robust — a correct per-turn record
# has the tiny turn's output far below the long turn's, while a cumulative bug
# (a running total) makes the second record ~= long + tiny, i.e. ~= the long
# turn. Comparing a long turn against a tiny one is a scale-immune "small vs
# large" split, unlike a "1x vs 2x" ratio of two equal turns, whose margin
# collapses under ordinary LLM reply-length variance.
COST_MULTI_TURN_SYSTEM_PROMPT = (
    "You are a helpful assistant in a chat room. Follow the user's instructions "
    "about reply length exactly: when they ask for detail, write several full "
    "paragraphs; when they ask for a single word, reply with just that one word "
    "and nothing else."
)


# Reusable agent shapes for ``@with_adapters(..., **SHAPE)``: the prompt (and
# features) a smoke runs its agents under. Declared once here so every test shares
# the same shape instead of re-spelling it.
TOOL_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT}
MEMORY_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": memory_features()}
COST_AGENT = {"prompt": COST_AGENT_SYSTEM_PROMPT, "features": usage_features()}
COST_MULTI_TURN_AGENT = {
    "prompt": COST_MULTI_TURN_SYSTEM_PROMPT,
    "features": usage_features(),
}


# Reply-oriented driving glue shared by the context-recall and rehydration
# scenarios: a prompt that answers in chat (acknowledge on "remember", state the
# value on "recall"), plus the two user messages that state and later ask for a
# note. Kept here (not inline in one test) so every recall/rehydration test drives
# the model the same way — a fair, single-source comparison across the matrix.
# Wording note: a neutral "note", not a "secret code" — models refuse to echo a
# credential-shaped value, an unrelated false failure.
REPLY_PROMPT = (
    "You are a helpful assistant in a chat room. Reply directly with one short "
    "sentence. When asked to remember something, acknowledge it; when later asked "
    "what it was, state it exactly."
)
REMEMBER = "Please remember this note: {note}. Confirm you remember it."
RECALL = "What was the note I asked you to remember? Reply with just it."


def liveness_probe(marker: str) -> str:
    """User message asking the agent to echo ``marker`` to confirm it is still
    processing — the tolerant liveness check after churn (e.g. a flood).

    Phrased as a benign confirmation rather than a terse override ("reply with just
    the word X and nothing else"), which safety-tuned models sometimes refuse
    ("I can't follow instructions that override my behaviour") — an unrelated false
    failure. The marker still lands verbatim in the reply for a substring assert."""
    return f"To confirm you're still active, please reply with the word {marker}."


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
